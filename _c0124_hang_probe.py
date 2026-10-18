# -*- coding: utf-8 -*-
"""C-0124 打桩探针 ③：挂起自愈（零花费）。

回答 task-1 的核心分歧：「卡住」在代码里到底会不会**永远**卡住？

做法：把 `chat.chat_once` 换成**永不返回**的桩（模拟"连接活着、服务端不回包"），
把三道兜底调成秒级，看：
  ① 单次调用是否被 `_call_with_hard_timeout` 判停（`ModelCallHang`）；
  ② 被判停后**槽位是否当场归还**（后续 worker 还能不能拿到槽）；
  ③ `build_v2` 是否**自己走了出来**并落盘（而不是 with ThreadPoolExecutor 钉死）；
  ④ 从"调用开始"到"界面看到终点"**最长静默多久**（= 用户感知的"卡住"时长）。

三组场景（都用真代码，只换模型桩）：
  A 硬墙：KP_CALL_HARD_TIMEOUT=3，调用全挂 → 期望 3 秒级判停
  B 判停线：KP_STALL_TIMEOUT=5，硬墙设大 → 期望 5 秒级收尾
  C 对照：正常桩（0.2s/条）→ 期望跑完，证明兜底不误伤正常路径

**不碰用户真实资料与真实库**：产物写 `_c0124_shadow/`，数据层用合成课件。
"""
import hashlib
import json
import os
import sys
import time
import threading
import traceback

sys.path.insert(0, r"D:\study_agent")
os.environ["PYTHONUTF8"] = "1"

SHADOW = r"D:\study_agent\_c0124_shadow"
N_PAGES = 12
REAL_FILES = [
    r"D:\study_agent\subjects\生药药动\index\study.db",
    r"D:\study_agent\subjects\药物化学\index\study.db",
    r"D:\study_agent\index\study.db",
]


def sha256(path):
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for b in iter(lambda: f.read(1 << 20), b""):
                h.update(b)
        return h.hexdigest()
    except OSError as e:
        return "<不可读:%s>" % type(e).__name__


def fake_read_text_pages(doc_id, db_path=None):
    blocks, cover = {}, {}
    for p in range(1, N_PAGES + 1):
        cid = 8000 + p
        t = "第 %d 页 合成材料（C-0124 影子样本）。药物代谢与排泄示例段落。" % p
        blocks[p] = [{"chunk_id": cid, "ord": p, "kind": "正文块", "ref": "p.%d" % p,
                      "page": p, "pages": [p], "chars": len(t), "text": t}]
        cover[p] = [cid]
    return {"blocks": blocks, "cover": cover, "figrows": {}, "max_page": N_PAGES,
            "n_blocks": N_PAGES, "pages_all": list(range(1, N_PAGES + 1))}


def install_stub(mode, per_call_sleep=0.0):
    """mode='hang' → 永不返回；mode='ok' → 正常快返回。"""
    from core import chat
    state = {"n_hang": 0, "n_ok": 0}
    lock = threading.Lock()

    def stub(messages, model=None, max_tokens=2048, thinking=False, timeout=180,
             temperature=0.3, reasoning_effort=None, **kw):
        sys_p = messages[0]["content"] if messages else ""
        user_p = messages[1]["content"] if len(messages) > 1 else ""
        is_kp = ("末级知识点" in sys_p or "知识点" in sys_p[:200])
        if mode == "hang":
            if is_kp:
                with lock:
                    state["n_hang"] += 1
                time.sleep(3600)          # ← 挂起：连接活着、永不回包
            kids = [{"name": "要点 %d" % p, "pages": [p]} for p in range(1, N_PAGES + 1)]
            return {"content": json.dumps(
                {"root": {"name": "合成课件", "children": [
                    {"name": "合成章节", "children": kids}]}}, ensure_ascii=False),
                "model": "STUB", "usage": {"prompt_tokens": 10, "completion_tokens": 5,
                                           "total_tokens": 15}}
        with lock:
            state["n_ok"] += 1
        if is_kp and per_call_sleep:
            time.sleep(per_call_sleep)
        if not is_kp:
            kids = [{"name": "要点 %d" % p, "pages": [p]} for p in range(1, N_PAGES + 1)]
            return {"content": json.dumps(
                {"root": {"name": "合成课件", "children": [
                    {"name": "合成章节", "children": kids}]}}, ensure_ascii=False),
                "model": "STUB", "usage": {"prompt_tokens": 10, "completion_tokens": 5,
                                           "total_tokens": 15}}
        return {"content": json.dumps({
            "name": "要点", "summary": "打桩示例总结。",
            "points": ["示例一。"], "excluded_pages": []}, ensure_ascii=False),
            "model": "STUB", "usage": {"prompt_tokens": 10, "completion_tokens": 5,
                                       "total_tokens": 15}}

    chat.chat_once = stub
    return state


def run_case(tag, env, mode, per_call_sleep=0.0, doc_id=0):
    os.makedirs(SHADOW, exist_ok=True)
    for k, v in env.items():
        os.environ[k] = str(v)
    out = os.path.join(SHADOW, "%s-末级知识点总结.json" % tag)
    if os.path.exists(out):
        os.remove(out)

    from core import kp_build
    kp_build.read_text_pages = fake_read_text_pages
    kp_build.configure_model_gate(2)
    state = install_stub(mode, per_call_sleep)

    t0 = time.time()
    meta, err = None, ""
    try:
        meta = kp_build.build_v2(int(doc_id), out_path=out, limit=2, force=True,
                                 progress_on=True,
                                 log=lambda m: None)
    except Exception as e:
        err = "%s: %s" % (type(e).__name__, e)
    dt = time.time() - t0

    wrote = os.path.exists(out)
    status = (meta or {}).get("status") if meta else None
    gate = kp_build.model_gate_stats()
    print("  [%s] 用时 %6.2fs  异常=%s" % (tag, dt, err or "无"))
    print("        meta.status=%s  产物写出=%s" % (status, "是" if wrote else "否"))
    print("        闸门 inflight=%d/%d（归还=%s）  挂起调用数=%d"
          % (gate["inflight"], gate["limit"],
             "是" if gate["inflight"] == 0 else "★否", state.get("n_hang", 0)))
    if wrote:
        try:
            m = json.load(open(out, encoding="utf-8")).get("meta") or {}
            print("        产物 meta.status=%s  error=%s"
                  % (m.get("status"), str(m.get("error"))[:90]))
            errs = [c.get("error") for c in (m.get("calls") or []) if not c.get("ok")]
            if errs:
                print("        失败调用记账 %d 条，首条=%s" % (len(errs), str(errs[0])[:80]))
        except Exception as e:
            print("        产物读取失败：%s" % e)
    return {"tag": tag, "dt": dt, "err": err, "wrote": wrote, "status": status,
            "inflight": gate["inflight"], "n_hang": state.get("n_hang", 0)}


def main():
    print("=" * 78)
    print("C-0124 打桩验证：挂起自愈（真实模型调用 0 次 → 花费 CNY 0.0000）")
    print("=" * 78)
    before = {p: sha256(p) for p in REAL_FILES}

    results = []
    print("\n【场景 A】硬墙：KP_CALL_HARD_TIMEOUT=3、KP_SLOT_WAIT_TIMEOUT=3，调用全挂")
    print("-" * 78)
    results.append(run_case("A", {"KP_CALL_HARD_TIMEOUT": 3, "KP_SLOT_WAIT_TIMEOUT": 3,
                                  "KP_STALL_TIMEOUT": 30}, "hang", doc_id=991))

    print("\n【场景 B】判停线：KP_STALL_TIMEOUT=4（硬墙 90 秒 = 调用不会回来），调用全挂")
    print("-" * 78)
    results.append(run_case("B", {"KP_CALL_HARD_TIMEOUT": 90, "KP_SLOT_WAIT_TIMEOUT": 90,
                                  "KP_STALL_TIMEOUT": 4}, "hang", doc_id=992))

    print("\n【场景 C】对照：正常桩 0.2s/条，兜底不误伤")
    print("-" * 78)
    results.append(run_case("C", {"KP_CALL_HARD_TIMEOUT": 600, "KP_SLOT_WAIT_TIMEOUT": 600,
                                  "KP_STALL_TIMEOUT": 600}, "ok", per_call_sleep=0.2,
                            doc_id=993))

    after = {p: sha256(p) for p in REAL_FILES}
    print("\n[隔离断言] 真实库逐字节未变：%s"
          % ("是" if before == after else "★否"))
    for p in REAL_FILES:
        print("  %s %s %s" % (after[p][:16],
                              "一致" if before[p] == after[p] else "★变了", p))

    print("\n" + "=" * 78)
    prob = []
    a, b, c = results
    if a["dt"] > 30:
        prob.append("A 场景硬墙没在秒级生效（%.1fs）" % a["dt"])
    if a["inflight"] != 0:
        prob.append("A 场景槽位没归还（inflight=%d）" % a["inflight"])
    if not a["wrote"]:
        prob.append("A 场景没落盘")
    if b["dt"] > 60:
        prob.append("B 场景判停线没生效（%.1fs）" % b["dt"])
    if not c["wrote"] or c["status"] != "done":
        prob.append("C 场景正常路径被兜底误伤（status=%s wrote=%s）"
                    % (c["status"], c["wrote"]))
    if before != after:
        prob.append("★真实数据被改动")

    if prob:
        print("结论：**有问题**")
        for p in prob:
            print("  - " + p)
        return 1
    print("结论：挂起可自愈 —— 硬墙按秒级判停并**当场归还槽位**，build_v2 自己走出来")
    print("      并落盘（status=%s）；正常路径不受影响（C 场景 status=done）。"
          % a["status"])
    print("      真实库未被触碰。")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(2)
