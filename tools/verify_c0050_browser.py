# -*- coding: utf-8 -*-
"""卡 C-0050 ★真浏览器验证（headless Edge + CDP）：页签产物驱动 + 清除前端链路。

流程（备份 → 真跑浏览器 → 断言 → 恢复）：
  1. 备份 doc#5 的产物与 `.bak-*` 到卡目录 `backup/`（浏览器里会**真删**它）；
  2. 造一份"某节点挂 2 条知识点"的 fixture 产物（doc#99998，跑完必删）；
  3. 跑 `tools/probe_c0050_browser.mjs`（真 Edge，抠 DOM 文本 + 截图）；
  4. 断言：结构树来自产物 / 点节点看到理解与要点 / 多知识点节点先列出来 /
     旧格式给出"需重新整理" / 点清除真弹二次确认 / 清完 toast 报数 /
     页签当场回"未整理" / 无 JS 报错；
  5. 文件系统对账（产物真的没了、outline 逐字节未变）；
  6. 恢复现场并核对 sha256。

用法：
    python tools\\verify_c0050_browser.py --port 8899 --doc 5 --legacy 1
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core import config, kp_build, subjects  # noqa: E402

CARD = Path(r"C:\Users\Lenovo\.dsh\steward-lite\consoles"
            r"\session-23a82a5f-fd66-4ae6-94c1-18f3e276c043\cards\C-0050")
BACKUP = CARD / "backup"
SHOTS = CARD / "shots"
# 一个结构节点挂**多条**知识点时的选择列表（卡 C-0050 新增交互）在真数据里
# 遇不到（doc#5 每个叶节点 1 条），所以按产物的同一套字段造一份假产物来验那条
# 分支。doc_id 不在库、只在验证期间存在，跑完（含异常）一律删掉。
FIXTURE_DOC = 99998
FIXTURE_NAME = "%d-末级知识点总结.json" % FIXTURE_DOC
OK = 0
BAD = 0
LINES: list[str] = []


def emit(s: str = "") -> None:
    print(s)
    LINES.append(s)


def check(cond, label: str, detail: str = "") -> bool:
    global OK, BAD
    if cond:
        OK += 1
        emit("  [OK] %s%s" % (label, ("  —— " + detail) if detail else ""))
    else:
        BAD += 1
        emit("  [★FAIL] %s%s" % (label, ("  —— " + detail) if detail else ""))
    return bool(cond)


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def snap(d: Path) -> dict:
    return {str(p.relative_to(d)): sha256(p)
            for p in sorted(d.rglob("*")) if p.is_file()} if d.is_dir() else {}


def snap_k(kdir: Path) -> dict:
    """产物目录的快照，**排除验证 fixture**（它不是真数据，跑完就删）。"""
    return {k: v for k, v in snap(kdir).items() if k != FIXTURE_NAME}


def api(port: int, path: str) -> dict:
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d%s" % (port, path),
                                    timeout=30) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return {"error": "HTTP %s" % e.code}
    except Exception as e:  # noqa: BLE001
        return {"error": "%s: %s" % (type(e).__name__, e)}


def make_multi_fixture(kdir: Path, src_doc: int) -> tuple:
    """造一份"某节点挂 2 条知识点"的假产物，返回 (路径, 说明)。"""
    src = kdir / ("%d-末级知识点总结.json" % src_doc)
    if not src.exists():
        return None, {}
    doc = copy.deepcopy(json.loads(src.read_text(encoding="utf-8")))
    doc.setdefault("meta", {})["doc_id"] = FIXTURE_DOC
    doc["meta"]["pipeline_version"] = "C-0049-v2"
    node = None
    stack = [doc["structure"]["root"]]
    while stack:
        n = stack.pop(0)
        if n.get("kp_ids"):
            node = n
            break
        stack += (n.get("children") or [])
    if node is None:
        return None, {}
    base = next((k for k in doc["knowledge_points"]
                 if k.get("id") == node["kp_ids"][0]), None)
    if base is None:
        return None, {}
    extra = copy.deepcopy(base)
    extra["id"] = str(base["id"]) + "-B"
    extra["name"] = str(base.get("name") or "") + "（同一节点下的第二条）"
    extra["summary"] = ("这是同一个结构节点下的第二条知识点（一个叶节点覆盖超过 3 页时"
                        "会被切成多条）。它是卡 C-0050 的验证 fixture，不是真实产物内容。")
    extra["points"] = ["同一结构节点可以挂多条知识点",
                       "点开节点时先列出来，点哪条看哪条"]
    doc["knowledge_points"] = list(doc["knowledge_points"]) + [extra]
    node["kp_ids"] = list(node["kp_ids"]) + [extra["id"]]
    p = kdir / FIXTURE_NAME
    p.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return p, {"node_id": node.get("id"), "node_name": node.get("name"),
               "ids": list(node["kp_ids"]), "extra_name": extra["name"],
               "first_id": str(base["id"]), "first_name": str(base.get("name") or "")}


def drop_fixture(kdir: Path) -> None:
    try:
        p = kdir / FIXTURE_NAME
        if p.exists():
            p.unlink()
    except OSError:
        pass


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8899)
    ap.add_argument("--doc", type=int, default=5)
    ap.add_argument("--legacy", type=int, default=1)
    ap.add_argument("--skip-restore", action="store_true")
    ap.add_argument("--autostart", action="store_true",
                    help="自己起一个临时端口实例（跑完杀掉），不用事先起看板")
    args = ap.parse_args()

    # 可选：自己起临时实例（挑一个空闲端口）。跑完/异常都靠 atexit 杀掉，
    # **绝不碰 8756**（那是大管家在用的常驻实例）。
    if args.autostart:
        import atexit
        import socket
        import time as _time
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        args.port = s.getsockname()[1]
        s.close()
        blog = ROOT / "logs" / "_c0050_browser_auto.out.log"
        blog.parent.mkdir(parents=True, exist_ok=True)
        fh = blog.open("wb")
        proc = subprocess.Popen(
            [sys.executable, "-X", "utf8", "-u", str(ROOT / "tools" / "dashboard.py"),
             "--port", str(args.port), "--no-open"],
            cwd=str(ROOT), stdout=fh, stderr=subprocess.STDOUT)
        atexit.register(lambda: proc.terminate())
        for _ in range(80):
            _time.sleep(0.4)
            if not api(args.port, "/api/stats").get("error"):
                break
        print("  已自起临时实例：端口 %d（PID %s）" % (args.port, proc.pid))

    subjects.restore()
    kdir = Path(config.LOG_DIR) / "knowledge"
    odir = Path(config.LOG_DIR) / "outline"
    docex = kp_build.out_file(args.doc)
    SHOTS.mkdir(parents=True, exist_ok=True)
    BACKUP.mkdir(parents=True, exist_ok=True)

    emit("=" * 78)
    emit("卡 C-0050 ★真浏览器验证（headless Edge + CDP，端口 %d）" % args.port)
    emit("  目标课件 doc#%d（v2 产物）/ doc#%d（旧格式产物）" % (args.doc, args.legacy))
    emit("=" * 78)

    # 0) 实例探活 + 现场先恢复一次（防上次中断）
    for b in sorted(BACKUP.glob(docex.name + "*")):
        dst = kdir / b.name
        if not dst.exists():
            shutil.copy2(b, dst)
    h = api(args.port, "/api/stats")
    if h.get("error"):
        emit("★临时实例 %d 打不通：%s" % (args.port, h["error"]))
        return 2

    before = api(args.port, "/api/mindmap?doc_id=%d" % args.doc)
    emit("\n[0] 前提：doc#%d 现在 state=%s / %s 个节点 / %s 条知识点"
         % (args.doc, before.get("artifact_state"), before.get("n_nodes"),
            before.get("lkp_total")))
    if before.get("artifact_state") != "v2":
        emit("★前提不成立：这份课件现在不是 v2 产物（先重新整理或恢复备份）")
        return 2
    kp_id = ""
    kp_points: list = []
    kp_summary = ""
    stack = list(before.get("tree") or [])
    while stack and not kp_id:
        n = stack.pop(0)
        if n.get("lkp_ids"):
            kp_id = n["lkp_ids"][0]
        stack += (n.get("children") or [])
    if kp_id:
        d = api(args.port, "/api/summary-kp?doc_id=%d&kp_id=%s" % (args.doc, kp_id))
        kp_points = d.get("points") or []
        kp_summary = str(d.get("summary") or "")
    emit("  抽查知识点：%s（%d 条要点 / 理解 %d 字）"
         % (kp_id, len(kp_points), len(kp_summary)))

    # 1) 备份 + fixture
    emit("\n[1] 备份现场（浏览器里会真删）")
    for p in sorted(kdir.glob(docex.name + "*")):
        if p.is_file():
            shutil.copy2(p, BACKUP / p.name)
            emit("  备份 %s" % p.name)
    fx_path, fx = make_multi_fixture(kdir, args.doc)
    emit("  fixture：%s（节点 %s 挂 %s 条知识点）"
         % (FIXTURE_NAME if fx_path else "没造出来", fx.get("node_id"),
            len(fx.get("ids") or [])))
    ksnap0 = snap_k(kdir)
    osnap0 = snap(odir)

    # 2) 真跑浏览器
    emit("\n[2] 真跑浏览器（node tools\\probe_c0050_browser.mjs）")
    jsout = CARD / "browser_probe.json"
    r = subprocess.run(
        ["node", str(ROOT / "tools" / "probe_c0050_browser.mjs"),
         "--port", str(args.port), "--doc", str(args.doc),
         "--legacy", str(args.legacy), "--outdir", str(SHOTS),
         "--multidoc", str(FIXTURE_DOC) if fx_path else "",
         "--json", str(jsout)],
        cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=300)
    if r.returncode != 0:
        emit("  node 退出码 %s\n  stderr: %s" % (r.returncode, (r.stderr or "")[-1500:]))
    D = json.loads(jsout.read_text(encoding="utf-8")) if jsout.exists() else {}
    if not D:
        emit("★探针没有产出 JSON")
        drop_fixture(kdir)
        return 1

    st = D.get("steps") or {}
    v2 = D.get("v2") or {}
    lg = D.get("legacyView") or {}
    ac = D.get("afterClear") or {}

    emit("\n[3] 断言：① 页签由**整理产物**驱动")
    check(bool((st.get("mount") or {}).get("ok")), "知识点页签挂载成功",
          json.dumps(st.get("mount"), ensure_ascii=False)[:120])
    check(v2.get("nNodes") == before.get("n_nodes"),
          "树上节点数 = 接口/产物的节点数（%s）" % before.get("n_nodes"),
          "浏览器数到 %s 个 .mmname" % v2.get("nNodes"))
    check("结构来自" in str(v2.get("infoBar")) and "整理产物" in str(v2.get("infoBar")),
          "信息条写明结构来自整理产物",
          str(v2.get("infoBar")).replace("\n", " / ")[:120])
    check("覆盖 34/34 页" in str(v2.get("infoBar")),
          "覆盖率行照旧渲染（34/34）",
          "含『无跨条重复计数』：%s" % ("无跨条重复计数" in str(v2.get("infoBar"))))
    check(v2.get("nHasKp") == before.get("lkp_ready") and v2.get("nHasKp", 0) > 0,
          "末级节点带 ◆（has-kp）的个数 = 已挂总结的节点数",
          "浏览器 %s / 接口 %s" % (v2.get("nHasKp"), before.get("lkp_ready")))
    check(int(v2.get("nPending") or 0) == 0, "没有「待生成」的末级节点",
          "nPending=%s" % v2.get("nPending"))
    check(not str(v2.get("emptyText") or "").strip(),
          "v2 课件不是空态（页签上真的有树）")

    emit("\n[4] 断言：② 点节点能看到**理解与要点**")
    pu = st.get("popup") or {}
    ptext = str(pu.get("text") or "")
    check(bool(pu.get("ok")) and bool(pu.get("visible")),
          "点了末级节点「%s」→ 弹窗真的可见" % pu.get("clicked"),
          str(pu.get("reason") or "")[:80])
    check("要点" in ptext, "弹窗里有「要点」区", ptext.replace("\n", " / ")[:100])
    check("总结正文" in ptext, "弹窗里有「总结正文 · 出处明细 · 生成链条」折叠区")
    summ = str(pu.get("summaryText") or "")
    check(bool(kp_summary) and kp_summary[:24] in summ,
          "★展开折叠区能看到**理解**（总结正文，与产物逐字对得上）",
          "理解 %d 字；弹窗里 %d 字；开头「%s」"
          % (len(kp_summary), len(summ), summ[:34]))
    check(int(pu.get("pointsCount") or 0) == len(kp_points)
          and int(pu.get("pointsCount") or 0) > 0,
          "★弹窗里**要点**条数与产物一致（%d 条，逐条渲染成 li）" % len(kp_points),
          "弹窗 %s 条 li" % pu.get("pointsCount"))
    hit = [p for p in kp_points if p and p[:12] in ptext]
    check(bool(kp_points) and len(hit) >= 1,
          "弹窗里的要点与产物/接口的要点**逐字对得上**",
          "命中 %d/%d 条；例：%s" % (len(hit), len(kp_points),
                                     (hit[0][:30] if hit else "—")))
    check(("末级知识点" in ptext) and ("层" in ptext),
          "弹窗头部写明这是末级知识点（含层级）")
    nx = st.get("nextKp") or {}
    check(bool(nx.get("ok")), "「下一知识点」按钮存在且可交互",
          "title=%s" % str(nx.get("title"))[:60])

    emit("\n[5] 断言：③ 旧格式课件给出明确状态（不是空白、不是报错）")
    lgtext = str(lg.get("emptyText") or "")
    check(int(lg.get("nNodes") or 0) == 0, "旧格式课件不再显示结构树",
          "nNodes=%s" % lg.get("nNodes"))
    check(("旧格式" in lgtext) and ("重新整理" in lgtext),
          "明确说明「旧格式，需重新整理」",
          lgtext.replace("\n", " / ")[:120])
    check(("读取失败" not in lgtext) and ("出错" not in lgtext)
          and not (D.get("errors") or []),
          "不是报错（无「读取失败/出错」，且控制台无 JS 异常）",
          "errors=%s" % (D.get("errors") or "无"))

    emit("\n[5b] 断言：③b 一个节点挂多条知识点 → 点开先列出来让人选")
    mv = D.get("multiView") or {}
    mp = st.get("multiPopup") or {}
    mpick = st.get("multiPick") or {}
    if fx_path:
        check(int(mv.get("nNodes") or 0) > 0,
              "fixture 课件（doc#%d）在页签上正常出图" % FIXTURE_DOC,
              "%s 个节点" % mv.get("nNodes"))
        check(bool(mp.get("ok")) and int(mp.get("nItems") or 0) == len(fx["ids"]),
              "点那个节点 → 弹窗列出全部 %d 条知识点（不替人挑一条）" % len(fx["ids"]),
              "列表 %s 项：%s" % (mp.get("nItems"),
                                  str(mp.get("text") or "").replace("\n", " / ")[:90]))
        check(str(fx["extra_name"]) in str(mp.get("text") or ""),
              "列表里两条都在（含新造的第二条）", str(fx["extra_name"])[:40])
        check(str(mp.get("title") or "").find("条知识点") >= 0,
              "节点提示也写明「这个节点下有几条知识点」",
              str(mp.get("title"))[:60])
        check(bool(mpick.get("ok")) and int(mpick.get("pointsCount") or 0) > 0
              and bool(str(mpick.get("summaryText") or "").strip()),
              "点列表里的条目 → 正常打开它的要点与理解",
              "%s：要点 %s 条 / 理解 %s 字"
              % (mpick.get("kp"), mpick.get("pointsCount"),
                 len(str(mpick.get("summaryText") or ""))))
    else:
        emit("  （fixture 没造出来，跳过这一档）")
    drop_fixture(kdir)          # fixture 用完即删（对账与恢复都不含它）

    emit("\n[6] 断言：④ 清除的**前端链路**（勾选 → 点清除 → 真弹二次确认 → 确定）")
    panel = st.get("openPanel") or {}
    check(bool(panel.get("ok")) and bool(panel.get("clearBtn")),
          "批量面板里有「清除选中课件的整理产物」按钮（与「开始批量整理」并列）",
          "面板 %s 行" % panel.get("nRows"))
    check(bool((st.get("tick") or {}).get("checked")), "勾选目标课件（复用同一套多选）")
    cc = st.get("clearClick") or {}
    check(bool(cc.get("dialogSeen")), "★点击清除后**真的弹出了二次确认**")
    dlg = (D.get("dialogs") or [{}])[0]
    check(("不可逆" in str(dlg.get("message"))) and ("重新整理" in str(dlg.get("message"))),
          "确认框写明「不可逆 + 必须重新整理才能重建」",
          str(dlg.get("message", "")).replace("\n", " / ")[:160])
    toast = str((st.get("toast") or {}).get("text") or "")
    check("已清除" in toast, "清完 toast **如实报数**（删了几个文件/几份课件）", toast[:120])

    emit("\n[7] 断言：⑤ 文件系统对账 + 页签回到「未整理」")
    gone = not docex.exists()
    baks_left = sorted(kdir.glob(docex.name + ".bak-*"))
    check(gone and not baks_left, "★产物与备份在文件系统里**真的消失**",
          "产物存在=%s；残留备份 %s" % (docex.exists(),
                                        [b.name for b in baks_left] or "无"))
    ksnap1 = snap_k(kdir)
    osnap1 = snap(odir)
    removed = sorted(set(ksnap0) - set(ksnap1))
    changed = sorted(k for k in (set(ksnap0) & set(ksnap1)) if ksnap0[k] != ksnap1[k])
    check(all(k.startswith(docex.name) for k in removed) and not changed,
          "只删了目标产物与它的 .bak-*（别的文件一个没动）",
          "消失 %s / 改动 %s" % (removed, changed or "无"))
    o_changed = sorted(k for k in (set(osnap0) & set(osnap1)) if osnap0[k] != osnap1[k])
    check(not o_changed and len(osnap0) == len(osnap1),
          "★logs/outline/*.json 一份没少、逐字节未变（sha256 对照）",
          "%d 个文件，改动 %s" % (len(osnap1), o_changed or "无"))
    ac_text = str(ac.get("emptyText") or "")
    check(int(ac.get("nNodes") or 0) == 0 and ("还没有整理过知识点" in ac_text),
          "★页签**当场**回到「未整理」（并给出明确文案，不是空白）",
          ac_text.replace("\n", " / ")[:120])
    ac_opt = str(ac.get("optionText") or "")
    check("未整理" in ac_opt and "25 节点" not in ac_opt,
          "下拉项摘要也跟着刷新（不再是清除前的「34 页 / 25 节点」）",
          ac_opt[:90])
    for name, p in (D.get("shots") or {}).items():
        if p:
            f = Path(p)
            check(f.exists() and f.stat().st_size > 3000,
                  "截图存证 %s（%s）" % (name, f.name),
                  "%d 字节" % (f.stat().st_size if f.exists() else 0))

    # 8) 恢复
    emit("\n[8] 恢复现场")
    drop_fixture(kdir)
    if args.skip_restore:
        emit("  （--skip-restore：故意不恢复，留给调用方处理）")
    else:
        for b in sorted(BACKUP.glob(docex.name + "*")):
            shutil.copy2(b, kdir / b.name)
        ksnap2 = snap_k(kdir)
        check(ksnap2 == ksnap0, "恢复后与清除前**逐字节一致**",
              "%d 个文件" % len(ksnap2))
        after = api(args.port, "/api/mindmap?doc_id=%d" % args.doc)
        check(after.get("artifact_state") == "v2"
              and after.get("lkp_total") == before.get("lkp_total"),
              "页签恢复显示（state=v2，知识点条数不变）",
              "state=%s kps=%s" % (after.get("artifact_state"), after.get("lkp_total")))

    emit("\n" + "=" * 78)
    emit("结果：%d 通过 / %d 失败" % (OK, BAD))
    emit("=" * 78)
    (CARD / "browser_verify.log").write_text("\n".join(LINES) + "\n", encoding="utf-8")
    (CARD / "browser_verify.json").write_text(json.dumps(
        {"ok": OK, "bad": BAD, "probe": D}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    return 0 if BAD == 0 else 1


if __name__ == "__main__":
    # 兜底：断言中途抛异常也必须把现场恢复回去（浏览器里是真删，丢了就是丢了）
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except BaseException as e:  # noqa: BLE001
        subjects.restore()
        kd = Path(config.LOG_DIR) / "knowledge"
        drop_fixture(kd)
        restored = []
        for b in sorted(BACKUP.glob("*")):
            if b.is_file():
                shutil.copy2(b, kd / b.name)
                restored.append(b.name)
        print("★异常中断（%s: %s），已从卡目录备份恢复现场：%s"
              % (type(e).__name__, e, restored))
        raise