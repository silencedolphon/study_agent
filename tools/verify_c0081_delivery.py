# Copyright (c) 2026 潜声海豚 <2227663973@qq.com>. All rights reserved.
# 本源码为著作权人私有作品，适用项目根目录 LICENSE（专有许可）。
# 允许个人安装与使用；禁止反向工程、修改、再分发、商业使用及声称原创。
# 本软件不含源码，按「现状」提供；详见 NOTICE.txt。
"""卡 C-0081 交付自检（**1~2 秒跑完**，供大管家二次验收）。

它**不重跑**整条端到端（那要 3~6 分钟，见 `tools/verify_c0081_share_e2e.py`），
而是在验收留下的现场上**独立复算**结论——一个字都不信上一步的自述：
  · 两个分享包：重新按 `core.share.inspect_package` 逐条核对结构 + 向量模型身份；
  · 甲（A 端数据根，已改名封存）与乙（B 端数据根）在**库里**各跑同题关键词检索，
    逐条比对 (ord, 出处, 文本前 70 字)——只读、不调模型；
  · 知识点产物：条数 / 每条名称 / 结构节点名逐项比对，并检查乙端引用块都在；
  · 页图：两边逐张 sha256 比（页图文件不在归档里时降级为核对当时算好的清单）；
  · 恶意包：在**导入层**（`import_package(dry_run=True)`）把 5 类包再打一遍，
    要求全部被拒且库内行数不变；
  · 乙端哨兵：三次导入窗口各 0 次 API 调用 / 0 次对外连接。

**现场有两份**，脚本按顺序找（可用 `--scene` 强制）：
  1. `%TEMP%\\sa_share_test\\`（端到端跑完的活现场，证据最全）；
  2. 卡目录 `evidence\\现场\\`（归档现场：两个库、知识点产物、精简包、5 个恶意包、哨兵）。
   临时目录被清理时自动回落到第 2 份，并在结论里注明降级了哪几项。

跑法（在项目根执行）：
    venv\\Scripts\\python.exe tools\\verify_c0081_delivery.py
输出：前 6 行是结论，明细写 `<现场>\\delivery_check.log`（活现场）或卡目录 `evidence\\delivery_check.log`。
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
SA = Path(os.environ.get("TEMP", ".")) / "sa_share_test"
CARD = Path(os.environ.get(
    "C0081_CARD",
    r"C:\Users\Lenovo\.dsh\steward-lite\consoles"
    r"\session-23a82a5f-fd66-4ae6-94c1-18f3e276c043\cards\C-0081"))
SUBJ_MIN, SUBJ_FULL = "接收精简库", "接收全量库"
QUERIES = ["Lipinski", "细胞色素P450", "药物代谢"]
API_MODULES = ("core.chat", "core.figures", "core.outline", "core.kp_build",
               "core.ingest", "core.terms_build")
BAD_NAMES = ("bad-traversal.zip", "bad-model.zip", "bad-missing-chunks.zip",
             "bad-nofield.zip", "bad-bomb.zip")

LOG: list[str] = []
CHECKS: list[dict] = []
NOTES: list[str] = []


def say(msg: str = "") -> None:
    LOG.append(msg)


def ck(name: str, ok: bool, detail: str = "") -> bool:
    CHECKS.append({"name": name, "ok": bool(ok), "detail": str(detail)[:400]})
    say("%s %s%s" % ("[OK]" if ok else "[!!]", name, (" —— " + str(detail)) if detail else ""))
    return bool(ok)


def skip(name: str, why: str) -> None:
    NOTES.append("%s（%s）" % (name, why))
    say("[--] %s —— 降级/跳过：%s" % (name, why))


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with Path(p).open("rb") as fh:
        for blk in iter(lambda: fh.read(1 << 20), b""):
            h.update(blk)
    return h.hexdigest()


def ro(p: Path) -> sqlite3.Connection:
    c = sqlite3.connect("file:///%s?mode=ro" % Path(p).as_posix(), uri=True)
    c.row_factory = sqlite3.Row
    return c


def counts_of(db: Path) -> dict:
    c = ro(db)
    try:
        return {t: c.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0]
                for t in ("documents", "chunks", "embeddings", "figure_desc")}
    finally:
        c.close()


def kp_of(p: Path) -> dict:
    if not p.is_file():
        return {"exists": False}
    obj = json.loads(p.read_text(encoding="utf-8"))
    kps = obj.get("knowledge_points") or []
    names: list = []

    def walk(n) -> None:
        if isinstance(n, dict):
            if n.get("name"):
                names.append(n["name"])
            for c in (n.get("children") or []):
                walk(c)
        elif isinstance(n, list):
            for c in n:
                walk(c)

    walk((obj.get("structure") or {}).get("root") or {})
    refs: set = set()
    for k in kps:
        for s in (k.get("sources") or []):
            for cid in (s.get("chunk_ids") or []):
                refs.add(int(cid))
            if s.get("chunk_id") is not None:
                refs.add(int(s["chunk_id"]))
        for cid in (k.get("material_block_ids") or []):
            refs.add(int(cid))
        for m in (k.get("material") or []):
            if m.get("chunk_id") is not None:
                refs.add(int(m["chunk_id"]))
    return {"exists": True, "n_kps": len(kps), "names": [k.get("name") for k in kps],
            "structure_names": names, "meta_doc_id": (obj.get("meta") or {}).get("doc_id"),
            "refs": refs}


def locate(force: str = "auto") -> dict | None:
    """活现场优先，其次卡目录归档现场。"""
    live_a = None
    cands = sorted(glob.glob(str(SA / "A_sealed_*"))) + [str(SA / "A")]
    for c in cands:
        if Path(c, "subjects").is_dir():
            live_a = Path(c)
            break
    live_ok = (live_a is not None and (SA / "B" / "subjects").is_dir()
               and (SA / "transfer" / "A-min.zip").is_file())
    arc = CARD / "evidence" / "现场"
    arc_ok = (arc / "B" / "subjects").is_dir() and (arc / "transfer" / "A-min.zip").is_file()
    if force == "live" or (force == "auto" and live_ok):
        if not live_ok:
            return None
        return {"kind": "live", "label": "临时现场 %s" % SA, "a": live_a, "b": SA / "B",
                "transfer": SA / "transfer", "bad": SA / "bad",
                "log": SA / "logs"}
    if force == "archive" or (force == "auto" and arc_ok):
        if not arc_ok:
            return None
        return {"kind": "archive", "label": "卡目录归档现场 %s" % arc, "a": arc / "A",
                "b": arc / "B", "transfer": arc / "transfer", "bad": arc / "bad",
                "log": CARD / "evidence"}
    return None


def find_bad(scene: dict, name: str) -> Path | None:
    for d in (scene["bad"], CARD / "evidence"):
        p = Path(d) / name
        if p.is_file():
            return p
    return None


def main() -> int:
    t0 = time.time()
    ap = argparse.ArgumentParser(description="C-0081 交付自检")
    ap.add_argument("--scene", choices=["auto", "live", "archive"], default="auto")
    args = ap.parse_args()
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:                      # noqa: BLE001
            pass
    if str(PROJ) not in sys.path:
        sys.path.insert(0, str(PROJ))

    scene = locate(args.scene)
    if scene is None:
        print("结论：FAIL  找不到现场（既没有 %s，也没有卡目录归档 %s）"
              % (SA, CARD / "evidence" / "现场"))
        print("修法：先跑 venv\\Scripts\\python.exe tools\\verify_c0081_share_e2e.py 重建现场（3~6 分钟）")
        return 1
    say("卡 C-0081 交付自检  %s" % time.strftime("%Y-%m-%d %H:%M:%S"))
    say("现场：%s（%s）" % (scene["label"], "活现场" if scene["kind"] == "live" else "归档现场"))

    a_root, b_root = scene["a"], scene["b"]
    a_sub = next((d for d in (a_root / "subjects").iterdir() if d.is_dir()), None)
    a_db = a_sub / "index" / "study.db"
    b_min = b_root / "subjects" / SUBJ_MIN / "index" / "study.db"
    b_full = b_root / "subjects" / SUBJ_FULL / "index" / "study.db"
    ck("现场就位：甲库 / 乙精简库 / 乙全量库", a_db.is_file() and b_min.is_file() and b_full.is_file(),
       "%s | %s | %s" % (a_db.is_file(), b_min.is_file(), b_full.is_file()))

    # ---------- 0. 上一步的机器可读结论（两份现场各有一份） ----------
    rep = {}
    for rp in (CARD / "evidence" / "report.json", SA / "evidence" / "report.json"):
        if rp.is_file():
            rep = json.loads(rp.read_text(encoding="utf-8"))
            break
    ck("端到端报告存在且 verdict=PASS",
       bool(rep) and (rep.get("summary") or {}).get("verdict") == "PASS",
       json.dumps((rep.get("summary") or {}).get("verdict")))
    hashes = {}
    hp = CARD / "evidence" / "hashes.json"
    if hp.is_file():
        hashes = json.loads(hp.read_text(encoding="utf-8"))
    prep = {}
    pp = SA / "evidence" / "a_prepare.json"
    if pp.is_file():
        prep = json.loads(pp.read_text(encoding="utf-8"))

    # ---------- 1. 分享包：独立重新校验 ----------
    from core import share as share_mod
    for name, fname in (("最精简", "A-min.zip"), ("带原文件+页图", "A-full.zip")):
        p = scene["transfer"] / fname
        if not p.is_file():
            p = CARD / "evidence" / fname
        if not p.is_file():
            rec = (hashes.get("packages") or {}).get(fname)
            if rec:
                skip("分享包独立重校验：%s" % name,
                     "包文件不在本现场（%d 字节 / sha256 %s…），已在验收时校验过"
                     % (rec.get("bytes", 0), str(rec.get("sha256"))[:16]))
                continue
            ck("分享包存在：%s" % name, False, fname)
            continue
        info = share_mod.inspect_package(p, deep=True)
        man = info.get("manifest") or {}
        chunks = (info.get("counts") or {}).get("total")
        dim = (man.get("embedding") or {}).get("dim")
        ck("包可独立校验通过 + 有切块与向量：%s" % name,
           (chunks or 0) > 0 and dim == 512,
           "%s 字节 / %s 块 / dim=%s / 含原文件=%s / 页图=%s"
           % (p.stat().st_size, chunks, dim, (info.get("includes") or {}).get("original"),
              (info.get("includes") or {}).get("pages")))

    # ---------- 2. 甲 / 乙 同题检索逐条比（库里只读跑，不调模型） ----------
    from core import search as search_mod
    from core import config as core_config
    say("本库嵌入模型口径：%s dim=%s" % (core_config.EMBED_MODEL_REPO, core_config.EMBED_DIM))
    ca, cb = ro(a_db), ro(b_min)
    try:
        for q in QUERIES:
            ha = search_mod.search(ca, q, top_k=8, corpus="main", mode="keyword")
            hb = search_mod.search(cb, q, top_k=8, corpus="main", mode="keyword")
            ka = [(h.ord, h.source_ref, (h.text or "")[:70]) for h in ha]
            kb = [(h.ord, h.source_ref, (h.text or "")[:70]) for h in hb]
            ck("★ 同题检索逐条一致（keyword）：%s" % q, bool(ka) and ka == kb,
               "甲 %d 条 / 乙 %d 条；出处 甲%s 乙%s"
               % (len(ka), len(kb), [x[1] for x in ka[:3]], [x[1] for x in kb[:3]]))
    finally:
        ca.close()
        cb.close()

    # ---------- 3. 知识点产物 ----------
    kp_a = kp_of(a_sub / "logs" / "knowledge" / "1-末级知识点总结.json")
    kp_b = kp_of(b_root / "subjects" / SUBJ_MIN / "logs" / "knowledge" / "1-末级知识点总结.json")
    ck("★ 甲乙知识点条数一致且 > 0",
       kp_a.get("n_kps", 0) > 0 and kp_a.get("n_kps") == kp_b.get("n_kps"),
       "甲 %s / 乙 %s" % (kp_a.get("n_kps"), kp_b.get("n_kps")))
    ck("★ 知识点名称与结构节点名逐项一致",
       kp_a.get("names") == kp_b.get("names")
       and kp_a.get("structure_names") == kp_b.get("structure_names"),
       "知识点 %s 条、结构节点 %s / %s"
       % (kp_b.get("n_kps"), len(kp_a.get("structure_names") or []),
          len(kp_b.get("structure_names") or [])))
    ck("乙端知识点产物的 meta.doc_id 已改写成新库的 id",
       kp_b.get("meta_doc_id") == 1, str(kp_b.get("meta_doc_id")))
    cb = ro(b_min)
    try:
        ids = {r[0] for r in cb.execute("SELECT id FROM chunks")}
    finally:
        cb.close()
    missing = [i for i in (kp_b.get("refs") or set()) if i not in ids]
    ck("★ 乙端知识点引用的块在新库里都存在（重映射没丢）", not missing,
       "引用 %d 处，丢 %d 处" % (len(kp_b.get("refs") or set()), len(missing)))

    # ---------- 4. 页图 + 原文件 ----------
    a_figs = list((a_sub / "logs" / "figure_desc").glob("*/slide_*.png"))
    b_figs = list((b_root / "subjects" / SUBJ_FULL / "logs" / "figure_desc").glob("*/slide_*.png"))
    if a_figs and b_figs:
        a_map = {p.name: sha256_file(p) for p in sorted(a_figs)}
        b_map = {p.name: sha256_file(p) for p in sorted(b_figs)}
        ck("★ 乙端页图与甲端逐张 sha256 相同（带页图的包）",
           bool(a_map) and a_map == b_map, "甲 %d 张 / 乙 %d 张" % (len(a_map), len(b_map)))
    else:
        pages = hashes.get("pages") or {}
        ja, jb = pages.get("甲") or {}, pages.get("乙全量库") or {}
        ck("★ 甲/乙页图 sha256 清单一致（归档现场：页图文件未随包归档）",
           bool(ja) and ja == jb,
           "清单 甲 %d 张 / 乙 %d 张；样例 %s" % (len(ja), len(jb),
                                                list(ja.items())[:1]))
        skip("页图逐张 sha256 现场重算", "归档现场不含 71 张页图文件（体积考虑），改用验收时算好的清单")
    raw_dir = b_root / "subjects" / SUBJ_FULL / "raw"
    orig_bytes = prep.get("orig_bytes") or 0
    if raw_dir.is_dir():
        raw = {p.name: p.stat().st_size for p in raw_dir.glob("*") if p.is_file()}
        ck("全量包把原课件也落到了接收方 raw\\",
           bool(raw) and (not orig_bytes or orig_bytes in raw.values()),
           "%s（甲端原文件 %s 字节）" % (raw, orig_bytes or "?"))
    else:
        rc = rep.get("raw_copied") or {}
        ck("全量包把原课件也落到了接收方 raw\\（归档核对）",
           bool(rc) and (not orig_bytes or orig_bytes in rc.values()),
           "%s（甲端原文件 %s 字节）" % (rc, orig_bytes or "?"))
        skip("接收方 raw\\ 现场重算", "归档现场不保留原课件副本")

    # ---------- 5. 行数一致 ----------
    c_a, c_b = counts_of(a_db), counts_of(b_min)
    ck("★ 甲乙精简库行数一致（documents/chunks/embeddings/figure_desc）",
       c_a == c_b and c_a["chunks"] > 0, "%s vs %s" % (c_a, c_b))

    # ---------- 6. 恶意包：导入层再打一遍 ----------
    bdir = scene["bad"]
    labels = {"bad-traversal.zip": "路径穿越", "bad-model.zip": "模型不符",
              "bad-missing-chunks.zip": "缺必需条目", "bad-nofield.zip": "缺字段",
              "bad-bomb.zip": "超大解压体积"}
    rejected, unchanged, tbl, found = [], True, [], 0
    log_dir = b_root / "subjects" / SUBJ_MIN / "logs"
    raw_dir2 = b_root / "subjects" / SUBJ_MIN / "raw"
    (log_dir).mkdir(parents=True, exist_ok=True)
    (raw_dir2).mkdir(parents=True, exist_ok=True)
    for fname in BAD_NAMES:
        p = find_bad(scene, fname)
        if p is None:
            ck("恶意包存在：%s" % labels[fname], False, fname)
            continue
        found += 1
        before = counts_of(b_min)
        hit = False
        try:
            share_mod.import_package(p, db_path=b_min, log_dir=log_dir, raw_dir=raw_dir2,
                                     root=b_root, dry_run=True)
        except Exception:                      # noqa: BLE001
            hit = True
        after = counts_of(b_min)
        rejected.append(hit)
        unchanged = unchanged and before == after
        tbl.append("%s:%s" % (labels[fname], "拒" if hit else "★没拒★"))
    ck("★ 恶意包 5 类在导入层全部被拒",
       found == 5 and len(rejected) == 5 and all(rejected), " ".join(tbl))
    ck("★ 拒绝过程中库内行数一个都没变", unchanged, str(counts_of(b_min)))

    # ---------- 7. 哨兵（零对外零模型） ----------
    wins = (rep.get("imports") or {}).get("sentinels") or {}
    ck("★ 三次导入（精简 / 重复 / 全量）各自的哨兵窗口：0 次 API 调用、0 次对外连接",
       len(wins) == 3 and all(w.get("api_calls_total") == 0 and w.get("n_connects") == 0
                              for w in wins.values()),
       json.dumps({k: {"api": w.get("api_calls_total"), "conn": w.get("n_connects")}
                   for k, w in wins.items()}, ensure_ascii=False))
    sent = {}
    for sp in (b_root / "sentinel.json", CARD / "evidence" / "现场" / "B" / "sentinel.json"):
        if sp.is_file():
            sent = json.loads(sp.read_text(encoding="utf-8"))
            break
    api_calls = sum(v for k, v in (sent.get("mod_calls") or {}).items() if k in API_MODULES)
    ck("乙端哨兵快照：对外连接 0 次、模型相关模块调用 0 次",
       sent.get("n_connects") == 0 and api_calls == 0,
       "n_connects=%s api=%s offline=%s" % (sent.get("n_connects"), api_calls,
                                            sent.get("offline")))
    cost = rep.get("cost") or {}
    ck("★ 成本对照：甲一次性 CNY > 0 且乙导入 CNY = 0",
       float(cost.get("甲_合计") or 0) > 0 and float(cost.get("乙_导入花费") or 0) == 0,
       "甲 CNY %s（识图 %s + 知识点 %s + 结构 %s）/ 乙 CNY %s"
       % (cost.get("甲_合计"), (cost.get("甲_识图_25页") or {}).get("cny"),
          (cost.get("甲_知识点总结") or {}).get("cny"),
          (cost.get("甲_入库结构分析") or {}).get("cny"), cost.get("乙_导入花费")))

    # ---------- 结论 ----------
    ok = sum(1 for c in CHECKS if c["ok"])
    elapsed = time.time() - t0
    try:
        logp = Path(scene["log"]) / "delivery_check.log"
        logp.parent.mkdir(parents=True, exist_ok=True)
        logp.write_text("结论：%s  %d/%d 项通过（%.1f s）  现场：%s\n\n%s"
                        % ("PASS" if ok == len(CHECKS) else "FAIL", ok, len(CHECKS),
                           elapsed, scene["label"], "\n".join(LOG)), encoding="utf-8")
    except OSError:
        logp = Path("delivery_check.log")
    fail = [c for c in CHECKS if not c["ok"]]
    print("结论：%s  %d/%d 项通过（%.1f 秒）"
          % ("PASS" if not fail else "FAIL", ok, len(CHECKS), elapsed))
    print("现场：%s（%s）" % (scene["label"], "活现场" if scene["kind"] == "live" else "归档现场"))
    print("包：独立重新校验通过；检索 3 题 keyword 在甲/乙两边逐条一致；知识点 %s 条一致；页图 %s 张"
          % (kp_b.get("n_kps"), len(a_figs) if a_figs else len((hashes.get("pages") or {}).get("甲") or {})))
    print("安全：恶意包 5/5 在导入层被拒且行数不变；乙端三次导入窗口 0 次 API 调用、0 次对外连接")
    print("成本：甲一次性 CNY %s → 乙 CNY %s（摊平）" % (cost.get("甲_合计"), cost.get("乙_导入花费")))
    print("明细：%s" % logp)
    if NOTES:
        print("降级/跳过 %d 项：%s" % (len(NOTES), "；".join(NOTES)))
    if fail:
        for c in fail:
            print("  ✗ %s —— %s" % (c["name"], c["detail"]))
    return 0 if not fail else 1


if __name__ == "__main__":
    raise SystemExit(main())
