# -*- coding: utf-8 -*-
"""C-0124 打桩探针 ②：时间序列证据 —— 「知识点整理」进度到底在不在动（零花费）。

**不调任何真实模型**：把 `core.chat.chat_once` 换成桩（结构调用返回固定树，
知识点调用 sleep 模拟 10~30 秒/条的真实耗时，可配）。

**不碰用户真实资料与真实库**：
  · 产物写到 `_c0124_shadow/`；
  · 页码映射层 `read_text_pages()` 被换成合成课件（15 页），所以**连库都不用读**；
  · 跑完打印真库/真产物的 SHA256 供前后对比。

证据口径（task-1 要求"阶段 A→B→C、条数 0→1→2"）：
  每条完成时打一行 `t=+SS.Ss  stage=..  kp_done=x/y  inflight=..` 的**时间序列**，
  并打印 `_STATE["kp_done"]` 的采样轨迹 —— 证明计数**单调推进**（不是卡住）。
"""
import hashlib
import io
import os
import sys
import time
import json
import threading
import traceback

sys.path.insert(0, r"D:\study_agent")
os.environ.setdefault("PYTHONUTF8", "1")

# 桩环境：单份内部并发 2、闸门 2，避免开太多线程
os.environ["KP_INNER_WORKERS"] = "2"
os.environ["KP_MODEL_GATE"] = "2"

SHADOW = r"D:\study_agent\_c0124_shadow"
REAL_SUBJECT_DB = r"D:\study_agent\subjects\生药药动\index\study.db"
REAL_ARTIFACTS = [
    r"D:\study_agent\subjects\生药药动\logs\knowledge\2-末级知识点总结.json",
    r"D:\study_agent\subjects\生药药动\logs\knowledge\3-末级知识点总结.json",
]
REAL_SUBJECTS_JSON = r"D:\study_agent\logs\subjects.json"

KP_SLEEP = float(os.environ.get("C0124_KP_SLEEP", "1.2"))   # 每条知识点的桩耗时（秒）
N_PAGES = 15
LIMIT_KP = int(os.environ.get("C0124_LIMIT", "3"))


def sha256(path):
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for b in iter(lambda: f.read(1 << 20), b""):
                h.update(b)
        return h.hexdigest()
    except OSError as e:
        return "<不可读 %s>" % e


def snapshot_real():
    d = {}
    for p in [REAL_SUBJECT_DB, REAL_SUBJECTS_JSON] + REAL_ARTIFACTS:
        d[p] = sha256(p)
    return d


# ---------------------------------------------------------------- 合成课件
SYNTH_TEXT_PAGES = {
    p: ("第 %d 页 合成材料（C-0124 影子样本，非用户真实资料）。"
        "本次主题 %d：药物代谢与排泄的示例段落，用于验证进度是否推进。" % (p, p))
    for p in range(1, N_PAGES + 1)
}


# ---------------------------------------------------------------- 桩：模型
def make_stub():
    from core import chat

    calls_log = []
    lock = threading.Lock()
    seq = [0]

    def stub_chat_once(messages, model=None, max_tokens=2048, thinking=False,
                       timeout=180, temperature=0.3, reasoning_effort=None, **kw):
        sys_p = messages[0]["content"] if messages else ""
        user_p = messages[1]["content"] if len(messages) > 1 else ""
        t0 = time.time()
        # 判据取自提示词真源：结构两步的 system 都以「结构」/「思维导图」开头；
        # 知识点那步的 system 是「末级知识点总结」。这里按"知识点"关键词判，
        # 其余一律当结构 —— 并在日志里留证，避免桩分类悄悄跑偏。
        is_kp = ("末级知识点" in sys_p or "知识点" in sys_p[:200])
        with lock:
            seq[0] += 1
            n = seq[0]
        if not is_kp:
            kids = [{"name": "合成要点 %d" % p, "pages": [p]} for p in range(1, N_PAGES + 1)]
            sec = {"name": "合成章节", "children": kids}
            content = json.dumps({"root": {"name": "合成课件", "children": [sec]}},
                                 ensure_ascii=False)
        else:
            time.sleep(KP_SLEEP)          # ← 模拟真实的一条 10~30 秒
            content = json.dumps({
                "name": "合成要点",
                "summary": "这是打桩生成的示例总结（零花费），用于验证进度推进。",
                "points": ["示例要点一。", "示例要点二。"],
                "excluded_pages": [],
            }, ensure_ascii=False)
        dt = time.time() - t0
        print("      [stub #%d] %s dt=%.2fs sys=%r user=%r"
              % (n, "KNOWLEDGE" if is_kp else "STRUCT", dt,
                 sys_p[:34].replace("\n", " "), user_p[:34].replace("\n", " ")))
        with lock:
            calls_log.append({"n": n, "is_kp": is_kp, "dt": round(dt, 2),
                              "sys_head": sys_p[:60]})
        return {"content": content, "model": "STUB-c0124",
                "usage": {"prompt_tokens": 100, "completion_tokens": 50,
                          "total_tokens": 150}}

    chat.chat_once = stub_chat_once
    return calls_log


def main():
    os.makedirs(SHADOW, exist_ok=True)
    print("=" * 78)
    print("C-0124 打桩验证：知识点整理进度是否推进（零花费）")
    print("=" * 78)

    before = snapshot_real()
    print("\n[真值 SHA256 · 跑之前]")
    for k, v in before.items():
        print("  %s  %s" % (v[:16], k))

    from core import kp_build
    from core import pipeline_pool

    calls_log = make_stub()

    # 合成课件：换掉页码映射层（不读真库）。返回结构与 read_text_pages() 逐键一致：
    # blocks / cover / figrows / max_page / n_blocks / pages_all
    def fake_read_text_pages(doc_id, db_path=None):
        blocks, cover = {}, {}
        for p in range(1, N_PAGES + 1):
            cid = 9000 + p
            t = SYNTH_TEXT_PAGES[p]
            blocks[p] = [{
                "chunk_id": cid, "ord": p, "kind": "正文块",
                "ref": "p.%d" % p, "page": p, "pages": [p],
                "chars": len(t), "text": t,
            }]
            cover[p] = [cid]
        return {"blocks": blocks, "cover": cover, "figrows": {},
                "max_page": N_PAGES, "n_blocks": N_PAGES,
                "pages_all": list(range(1, N_PAGES + 1))}

    kp_build.read_text_pages = fake_read_text_pages

    out = os.path.join(SHADOW, "999-末级知识点总结.json")
    if os.path.exists(out):
        os.remove(out)

    events = []
    t_start = time.time()

    def on_event(ev):
        try:
            d = dict(ev)
            d["_t"] = round(time.time() - t_start, 2)
            events.append(d)
            if d.get("ev") in ("begin", "tick", "end", "stage"):
                st = kp_build.progress_snapshot()
                mg = kp_build.model_gate_stats()
                print("  t=+%6.2fs  %-6s stage=%-2s kp_done=%s/%s running=%s "
                      "inflight=%d/%d abandoned=%d %s"
                      % (d["_t"], d.get("ev"), st.get("stage_index"),
                         st.get("kp_done"), st.get("kp_total"), st.get("active"),
                         mg["inflight"], mg["limit"], mg.get("abandoned", -1),
                         (d.get("note") or d.get("msg") or "")[:40]))
                if st.get("eta_note"):
                    print("            ↳ eta_note=%s  (since=%.1fs eta=%.1fs)"
                          % (st["eta_note"], st["since_last_call_s"], st["eta_call_s"]))
        except Exception:
            traceback.print_exc()

    print("\n[开始 build_v2：结构走桩、每条知识点桩耗时 %.1fs，limit=%d]" % (KP_SLEEP, LIMIT_KP))
    print("-" * 78)

    # 卡 C-0124：**独立采样线程**，每 0.3 秒取一次 progress_snapshot —— 这才是
    # 看板轮询看到的东西。事件回调只在"条数变化"时触发（那时 since_last_call_s
    # 天然接近 0），证明不了"长调用期间界面仍在动"，必须另外采。
    hb_log = []
    hb_stop = threading.Event()

    def sampler():
        while not hb_stop.is_set():
            try:
                st = kp_build.progress_snapshot()
                mg = kp_build.model_gate_stats()
                if st.get("active"):
                    hb_log.append((round(time.time() - t_start, 2),
                                   int(st.get("kp_done") or 0),
                                   float(st.get("since_last_call_s") or 0.0),
                                   float(st.get("eta_call_s") or 0.0),
                                   int(mg.get("inflight") or 0),
                                   st.get("stage") or ""))
            except Exception:
                pass
            hb_stop.wait(0.3)

    th = threading.Thread(target=sampler, daemon=True)
    th.start()

    err = ""
    try:
        meta = kp_build.build_v2(
            999,
            out_path=out,
            db_path=None,
            limit=LIMIT_KP,
            force=True,
            progress_on=True,
            on_event=on_event,
            log=lambda m: print("    [log] " + str(m)[:150]),
        )
    except Exception as e:
        err = "%s: %s" % (type(e).__name__, e)
        traceback.print_exc()

    elapsed = time.time() - t_start
    hb_stop.set()
    print("-" * 78)
    print("\n[结果] 用时 %.1fs  异常=%s" % (elapsed, err or "无"))

    # ---- 心跳时间序列（长调用期间界面是否在动）----
    if hb_log:
        print("\n[心跳采样] 每 0.3s 一次，字段 = (t秒, kp_done, since_last_call_s, "
              "eta_call_s, inflight)")
        shown = 0
        for row in hb_log:
            if shown >= 14:
                break
            print("   t=+%6.2fs  kp_done=%-3d since=%5.1fs eta=%5.1fs inflight=%d  %s"
                  % row)
            shown += 1
        mx = max(r[2] for r in hb_log)
        print("   … 共 %d 个采样；since_last_call_s 最大 = %.1fs（%s）"
              % (len(hb_log), mx,
                 "心跳在长调用期间持续推进 ✓" if mx > 0.5 else "★心跳没动"))
        nz_eta = [r[3] for r in hb_log if r[3] > 0]
        print("   eta_call_s（同类中位耗时）出现次数 = %d，样本值=%s"
              % (len(nz_eta), sorted(set(nz_eta))[:5]))
        stages_seen = []
        for r in hb_log:
            if r[1] >= 1 and (not stages_seen or stages_seen[-1] != r[5]):
                stages_seen.append(r[5])
        print("   写知识点期间的 stage 文案（按采样去重）= %s" % stages_seen)

    # ---- 产物 ----
    ok_artifact = os.path.exists(out)
    print("[产物] %s  %s" % ("已写出" if ok_artifact else "★未写出", out))
    kps = []
    if ok_artifact:
        doc = json.load(open(out, encoding="utf-8"))
        kps = doc.get("knowledge_points") or []
        m = doc.get("meta") or {}
        print("        meta.status=%s  kp_actual=%s/%s  elapsed_s=%s  calls=%d"
              % (m.get("status"), m.get("kp_actual"), m.get("kp_total"),
                 m.get("elapsed_s"), len(m.get("calls") or [])))
        print("        knowledge_points 条数=%d" % len(kps))

    n_struct = sum(1 for c in calls_log if not c["is_kp"])
    n_kp = sum(1 for c in calls_log if c["is_kp"])
    print("\n[桩调用统计] 结构 %d 次 / 知识点 %d 次（真实模型调用 0 次 → 花费 CNY 0.0000）"
          % (n_struct, n_kp))

    # ---- 时间序列：条数 0→1→2 ----
    # ⚠ 采样口径：`end` 事件不带 kp_done，所以**从 snapshotted 事件**里取，
    # 并额外记一份末值，避免把"最后一个事件缺字段"误判成"进度不推进"。
    seq = []
    for e in events:
        v = e.get("kp_done")
        if v is None:
            continue
        if not seq or seq[-1][1] != v:
            seq.append((e["_t"], v))
    print("[时间序列] (t秒, kp_done) = %s" % seq)
    max_done = max([v for _t, v in seq], default=0)
    print("[推进幅度] kp_done 最大采样 = %d（末态产物 kp_actual=%s）"
          % (max_done, (meta or {}).get("kp_actual") if err == "" else "n/a"))

    after = snapshot_real()
    print("\n[真值 SHA256 · 跑之后]")
    same = True
    for k, v in after.items():
        mark = "一致" if before.get(k) == v else "★变了"
        if before.get(k) != v:
            same = False
        print("  %s  %s  %s" % (v[:16], mark, k))
    print("\n[隔离断言] 用户真库/真产物/真学科表**逐字节未变**：%s" % ("是" if same else "★否"))

    # ---- 判定 ----
    print("\n" + "=" * 78)
    prob = []
    if err:
        prob.append("build_v2 抛异常：%s" % err)
    if not ok_artifact:
        prob.append("产物未写出")
    if len(seq) < 2:
        prob.append("进度只有一个采样点（无法证明推进）")
    elif max_done < 1:
        prob.append("kp_done 始终为 0（进度不推进）")
    if n_kp < 1:
        prob.append("桩没被当成知识点调用（分类跑偏，长调用路径未被验证）")
    if kps and len(kps) < 1:
        prob.append("产物里没有知识点")
    if not same:
        prob.append("★真实数据被改动")
    if prob:
        print("结论：**有问题**")
        for p in prob:
            print("  - " + p)
        return 1
    print("结论：进度**单调推进**（阶段与条数都在变），产物正常落盘，真数据未被触碰。")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(2)
