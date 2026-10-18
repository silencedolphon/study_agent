# -*- coding: utf-8 -*-
# Copyright (c) 2026 潜声海豚 <2227663973@qq.com>. All rights reserved.
# 本源码为著作权人私有作品（proprietary）：仅供经书面授权的测试使用；
# 未经著作权人许可，不得复制、分发、公开、发布或用于任何商业用途。
# Unauthorized copying, distribution or publication is strictly prohibited.
# File-ID: SA-C0065-verify   (project study_agent; keep this line)
"""卡 C-0065 验收：旧版 Office（.ppt/.doc/.xls）入库时自动转 OOXML。

跑法（**cmd 与 pwsh 下都能跑**，无 PowerShell 专有语法）::

    G:\\study_agent\\venv\\Scripts\\python.exe tools/verify_c0065_legacy.py

可选::

    --convert-all   把 raw/ 下还没有 OOXML 产物的 .ppt 逐份转换（每份 10~60 秒）
    --ab            基线（git HEAD 版 core）与当前 core 的 A/B 入库对照

**结论固定在输出的前 6 行**（明细跟在后面，末尾再冗余地打一次结论），
退出码 0 = 全部通过。验证全程**不调用任何付费模型**（识图与结构分析都关掉），
库也是临时库（`index/_tmp_c0065_verify.db`），不碰用户的真实库。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 明细里有中文，而控制台默认编码在中文 Windows 上是 GBK（宿主按 UTF-8 收输出时
# 会全变乱码）。结论行一律 ASCII，这里只把明细切到 UTF-8：读不了也不影响判定。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001  （老 Python 没有 reconfigure）
    pass

# 护栏与省钱：必须在 import core.db 之前设好。
#   KB_TESTING=1  —— core/db.py 会拒绝连接真实资料库；
#   OUTLINE_ANALYSIS=0 —— 结构分析要调模型（花钱），本脚本一律不开。
os.environ["KB_TESTING"] = "1"
os.environ["OUTLINE_ANALYSIS"] = "0"

from core import db, extract, ingest  # noqa: E402

RAW = ROOT / "subjects" / "分析化学" / "raw"
TMP_DB = ROOT / "index" / "_tmp_c0065_verify.db"
AB_DIR = ROOT / "_tmp_c0065_old"          # A/B 用的基线包目录（跑完即删）
LOG = ROOT / "index" / "_tmp_c0065_verify.log"
# A/B 与回归对照用的既有 .pptx（真资料，不新造）
REGRESS_PPTX = ROOT / "subjects" / "有机化学" / "raw" / "第一章 绪论.pptx"
# 回归对照用的极小夹具（几秒内跑完，避免验收超时）
REGRESS_SMALL = ROOT / "_smoke_fixtures" / "formats" / "计算机网络-第二章.pptx"

# 源文件基线（交付时实测；本卡的红线是"一个字节都不许动源 .ppt"）。
# 逐份比对 size + sha256：任何删改都会被这里抓到。
BASELINE = {
    "Chapter1.ppt": (1796096, "74d17ad6ecbd3f6e0e69d5cfc1dd2f2af31dfce7a636a93fb89656f16b001ce8"),
    "Chapter10(课).ppt": (1671168, "a110d3e2be40d171b8d3d446d20dca63f0546fb650ebb165d07e0344657f7cab"),
    "Chapter11.ppt": (4871168, "479638e0bd02e066bfbae7b2fde5f561b2d9afcc982c5661d5b51fec5a759cb0"),
    "Chapter12(上课用).ppt": (849408, "f212e3cdf44512c1039570659bd063bcff74ea6c55d8cfdef96bc4df113e47c8"),
    "Chapter14(上课用).ppt": (1039872, "9de9e90a4441cd3e37ad9babfa0fe257159970ee46966d69f2265df335521853"),
    "Chapter2.ppt": (1632768, "af2d95d2023aedae1bf6a29f522b2d328e3a8a909db6b114fef81a53a668e9c4"),
    "Chapter3.ppt": (1110528, "a625bd765db0611286a27956417de360c389e8db071c19a475da2c7a528cae05"),
    "Chapter4.ppt": (4935168, "1ea0ed5a0e0315c9eaa350b7d506ae6a87be55423d3afcd9aeec9b819bf14c7a"),
    "Chapter5.ppt": (2380288, "7bc3e852e31350123533f25c032db9134c443c27427014a79db2d1b6d114157b"),
    "Chapter6.ppt": (1688064, "431e9409a582fe61b8acc24b9d182a08365b1b11b134cf6e221d55050994a5e8"),
    "Chapter7(上课用).ppt": (1033216, "e805960e4932ed89b6cc873bc5b98c973534704866a8889c484f7e077afaced4"),
    "Chapter8(上课用).ppt": (1265152, "de62896468dfe148364a84df1643142e518a25f8cbe6c112f482bb2addffb46d"),
    "Chapter9'(上课用).ppt": (1618432, "c239d240e166fefd1e1d0936eafa61a0b2e4183392cc7ddd6f266260e2454285"),
}

CHECKS: list[dict] = []

# 唯一一份"转不了"的存量：源 .ppt 本身损坏。
#
# 实测证据（2026-10-08，两次复现）：PowerPoint 对它一律回
# `0x80070570 (ERROR_FILE_CORRUPT) The file or directory is corrupted and
# unreadable`；把**字节完全相同**的副本换到另一个目录再转，报同一个错——
# 所以不是路径/权限/并发的偶发问题，而是这份课件自身坏了（其余 12 份同类
# 文件在同一批次、同一台机器上全部转换成功）。
# 管线对此的表现正是本卡要的降级：status=failed + 可读原因 + 源文件保留，
# **不静默跳过**。这里只核对"它仍无产物、源文件仍未被改动"。
KNOWN_BROKEN = {"Chapter2.ppt":
                "PowerPoint 报 0x80070570（源文件损坏），需人工重新获取该课件"}


def check(name: str, ok: bool, detail: str = "") -> bool:
    CHECKS.append({"name": name, "ok": bool(ok), "detail": str(detail)})
    return bool(ok)


def sha256_of(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def fresh_db(path: Path):
    for suffix in ("", "-wal", "-shm"):
        f = Path(str(path) + suffix)
        if f.exists():
            f.unlink()
    conn = db.connect(path, immediate=True)
    db.init_db(conn)
    return conn


def doc_rows(conn) -> list[dict]:
    rows = conn.execute(
        "SELECT path, kind, title, n_chunks, status, role, corpus FROM documents "
        "ORDER BY path").fetchall()
    return [dict(r) for r in rows]


# ------------------------------------------------------------------ 1 源文件完好
def check_sources() -> None:
    missing, changed, ok = [], [], 0
    for name, (size, sha) in BASELINE.items():
        p = RAW / name
        if not p.is_file():
            missing.append(name)
            continue
        if p.stat().st_size != size or sha256_of(p) != sha:
            changed.append(name)
            continue
        ok += 1
    check("源 .ppt 全部完好（size+sha256 与交付基线一致）",
          ok == len(BASELINE) and not missing and not changed,
          f"{ok}/{len(BASELINE)} 一致；缺失={missing}；被改={changed}")


# ------------------------------------------------------- 2 OOXML 产物可被解析
def check_products() -> None:
    try:
        from pptx import Presentation  # type: ignore
    except ImportError as e:
        check("12 份 .pptx 产物可被 python-pptx 打开", False, f"缺少 python-pptx: {e}")
        return
    bad, slides_total, n_ok = [], 0, 0
    for name in BASELINE:
        if name in KNOWN_BROKEN:
            continue
        dest = extract.legacy_dest(RAW / name)
        try:
            n = len(Presentation(str(dest)).slides)
        except Exception as e:  # noqa: BLE001
            bad.append(f"{dest.name}: {type(e).__name__}")
            continue
        if n <= 0:
            bad.append(f"{dest.name}: 0 张幻灯片")
            continue
        n_ok += 1
        slides_total += n
    expect = len(BASELINE) - len(KNOWN_BROKEN)
    check(f"{expect} 份 .pptx 产物可被 python-pptx 打开且非空",
          not bad and n_ok == expect,
          f"{n_ok}/{expect} 可读，合计 {slides_total} 张幻灯片；异常={bad}")
    for name, why in KNOWN_BROKEN.items():
        dest = extract.legacy_dest(RAW / name)
        check(f"已知例外：{name} 无产物（{why}）", not dest.exists(),
              f"dest={dest.name} exists={dest.exists()}")


# ------------------------------------------------- 2b 与转换脚本的契约
def check_script_contract() -> None:
    """三条不能破的约定：脚本 ASCII-only、扩展名映射一致、必传 -KeepOriginal。"""
    import re as _re
    blob = extract._LEGACY_SCRIPT.read_bytes()
    non_ascii = [b for b in blob if b > 127]
    check("convert_legacy.ps1 仍是 ASCII-only（PS 5.1 按 GBK 读 .ps1）",
          not non_ascii, f"非 ASCII 字节 {len(non_ascii)} 个")

    txt = blob.decode("ascii", "replace")
    found = {m[0]: (m[1], m[2]) for m in _re.findall(
        r"'(\.[a-z]+)'\s*=\s*@\{\s*App\s*=\s*'([^']+)';\s*Fmt\s*=\s*(\d+)", txt)}
    want = {".ppt": "PowerPoint.Application", ".doc": "Word.Application",
            ".xls": "Excel.Application"}
    ok_map = all(found.get(ext, ("", ""))[0] == app for ext, app in want.items())
    exts_ok = all(extract.legacy_dest(Path("x" + ext)).suffix == ext + "x"
                  for ext in want)
    check("扩展名映射与脚本 $map 一致（.ppt/.doc/.xls -> +x；源文件保留）",
          ok_map and exts_ok and "-KeepOriginal" in
          (ROOT / "core" / "extract.py").read_text(encoding="utf-8"),
          f"脚本 $map={ {k: v[0] for k, v in found.items()} } 映射一致={exts_ok}")


# ------------------------------------------------------- 3 端到端入库 + 幂等
def check_e2e(verbose: bool) -> dict:
    src = RAW / "Chapter1.ppt"
    before = (src.stat().st_size, round(src.stat().st_mtime, 3), sha256_of(src))
    conn = fresh_db(TMP_DB)
    t0 = time.time()
    r1 = ingest.ingest_file(conn, src, with_figures=False, verbose=verbose)
    wall1 = round(time.time() - t0, 1)
    after = (src.stat().st_size, round(src.stat().st_mtime, 3), sha256_of(src))

    n_chunks = 0
    if r1.get("doc_id"):
        n_chunks = conn.execute("SELECT COUNT(*) c FROM chunks WHERE doc_id=?",
                                (r1["doc_id"],)).fetchone()["c"]
    rows = doc_rows(conn)
    ppt_rows = [x for x in rows if str(x["path"]).lower().endswith((".ppt", ".doc", ".xls"))]

    check("端到端入库 .ppt 成功（status=added / kind=pptx / 路径指向 .pptx）",
          r1.get("status") == "added" and r1.get("kind") == "pptx"
          and str(r1.get("path", "")).lower().endswith(".pptx"),
          f"status={r1.get('status')} kind={r1.get('kind')} path={r1.get('path')} "
          f"{wall1}s")
    check("入库后有切块（n_chunks>0）", n_chunks > 0 and len(rows) == 1,
          f"chunks={n_chunks} documents={len(rows)} 行 "
          f"({rows[0]['kind'] if rows else '-'})")
    check("documents 表里没有 .ppt 记录（只记 .pptx）", not ppt_rows,
          f"命中 {len(ppt_rows)} 行: {[x['path'] for x in ppt_rows]}")
    check("入库前后源 .ppt 的 size/mtime/sha256 完全未变", before == after,
          f"before={before[:2]} after={after[:2]} sha_same={before[2] == after[2]}")
    check("返回值注明来源 converted_from", str(r1.get("converted_from", "")).endswith(".ppt"),
          f"converted_from={r1.get('converted_from')}")

    t0 = time.time()
    r2 = ingest.ingest_file(conn, src, with_figures=False, verbose=verbose)
    wall2 = round(time.time() - t0, 1)
    rows2 = doc_rows(conn)
    check("重复入库：不重复转换、不新增记录（status=skipped）",
          r2.get("status") == "skipped" and len(rows2) == 1,
          f"status={r2.get('status')} reason={r2.get('reason')} "
          f"documents={len(rows2)} 行 {wall2}s")
    conn.close()
    return {"run1": r1, "run2": r2, "chunks": n_chunks, "wall1": wall1, "wall2": wall2}


# ------------------------------------------------------------- 4 降级路径
def check_fallback() -> None:
    src = RAW / "Chapter2.ppt"
    before = (src.stat().st_size, sha256_of(src))
    conn = fresh_db(ROOT / "index" / "_tmp_c0065_fallback.db")
    real = extract._LEGACY_SCRIPT
    try:
        # 模拟"本机没有 Office / 脚本丢失"：转换能力不可用
        extract._LEGACY_SCRIPT = ROOT / "tools" / "__no_such_convert_legacy.ps1"
        r = ingest.ingest_file(conn, src, with_figures=False)
    finally:
        extract._LEGACY_SCRIPT = real
    rows = doc_rows(conn)
    reason = str(r.get("reason") or "")
    after = (src.stat().st_size, sha256_of(src))
    check("降级：转换不可用时 status=failed 且 reason 可读、不静默跳过",
          r.get("status") == "failed" and "自动转换失败" in reason
          and "请另存为 .pptx" in reason,
          f"status={r.get('status')} reason={reason}")
    check("降级时不落库、源文件仍在且未变",
          not rows and before == after and src.is_file(),
          f"documents={len(rows)} 行 src_same={before == after}")
    conn.close()
    return r


# ------------------------------------------------------------- 5 回归（既有 .pptx）
def check_regression(verbose: bool) -> dict:
    src = REGRESS_SMALL if REGRESS_SMALL.is_file() else REGRESS_PPTX
    conn = fresh_db(ROOT / "index" / "_tmp_c0065_regress.db")
    r1 = ingest.ingest_file(conn, src, with_figures=False, verbose=verbose)
    r2 = ingest.ingest_file(conn, src, with_figures=False, verbose=verbose)
    rows = doc_rows(conn)
    check("回归：既有 .pptx 入库行为不变（added → 再跑 skipped）",
          r1.get("status") == "added" and r1.get("kind") == "pptx"
          and r2.get("status") == "skipped" and len(rows) == 1,
          f"{src.name}: {r1.get('status')}/{r1.get('kind')}/"
          f"{r1.get('chunks')} 块 → {r2.get('status')}")
    check("回归：.pptx 的返回结构里没有 converted_from（非转换路径不受影响）",
          "converted_from" not in r1 and "converted_from" not in r2,
          f"keys={sorted(k for k in r1 if k != 'warnings')}")
    conn.close()
    return {"run1": r1, "run2": r2}


# ------------------------------------------------------------- 6 全量转换（可选）
def convert_all() -> dict:
    out = []
    for name in BASELINE:
        src = RAW / name
        t0 = time.time()
        r = extract.convert_legacy(src)
        out.append({"file": name, "status": r.get("status"), "ok": bool(r.get("ok")),
                    "sec": round(time.time() - t0, 1),
                    "dest_size": (extract.legacy_dest(src).stat().st_size
                                  if extract.legacy_dest(src).is_file() else 0),
                    "note": (r.get("note") or "")[:100]})
        print(f"    convert_all {name}: {out[-1]['status']} "
              f"{out[-1]['sec']}s dest={out[-1]['dest_size']}", flush=True)
    ok = all(x["ok"] for x in out if x["file"] not in KNOWN_BROKEN)
    check("--convert-all：除已知损坏的 Chapter2.ppt 外全部转换成功", ok,
          json.dumps(out, ensure_ascii=False))
    broken = [x for x in out if x["file"] in KNOWN_BROKEN]
    check("--convert-all：已知损坏那份仍给出可读失败原因（不静默跳过）",
          all((not x["ok"]) and x["note"] for x in broken),
          json.dumps(broken, ensure_ascii=False))
    return {"rows": out}


# ------------------------------------------------------------- 7 A/B 对照（可选）
_AB_RUNNER = r'''
import json, os, sys
from pathlib import Path
root, pkg, dbp, src, outp = sys.argv[1:6]
os.environ["KB_TESTING"] = "1"
os.environ["OUTLINE_ANALYSIS"] = "0"
sys.path.insert(0, root)
ing = __import__(pkg + ".ingest", fromlist=["ingest"])
dbm = __import__(pkg + ".db", fromlist=["db"])


class FakeEmb:                       # 确定性假嵌入：不加载模型、不花钱
    def embed_documents(self, texts, progress=False):
        import numpy as np
        return np.zeros((len(texts), 8), dtype="float32")


ing.get_embedder = lambda: FakeEmb()
for suffix in ("", "-wal", "-shm"):
    f = Path(str(dbp) + suffix)
    if f.exists():
        f.unlink()
conn = dbm.connect(dbp, immediate=True)
dbm.init_db(conn)
r = ing.ingest_file(conn, Path(src), with_figures=False)
docs = [dict(x) for x in conn.execute(
    "SELECT path, kind, title, n_chunks, n_chars, status, role, corpus, warnings "
    "FROM documents ORDER BY path")]
chunks = [dict(x) for x in conn.execute(
    "SELECT ord, text, COALESCE(heading_path,'') AS heading_path, "
    "COALESCE(source_ref,'') AS source_ref FROM chunks ORDER BY ord")]
json.dump({"ret": {k: v for k, v in r.items() if k != "warnings"},
           "docs": docs, "chunks": chunks},
          open(outp, "w", encoding="utf-8"), ensure_ascii=False)
'''


def ab_compare() -> dict:
    """基线（git HEAD 版 core/ingest.py + core/extract.py）vs 当前 core。

    做法：把整个 core 包复制成项目根下的临时包 `_tmp_c0065_old/`，
    再用 `git show HEAD:...` 覆盖那两个文件——这样基线副本的 `config.ROOT`
    仍是项目根，`documents.path` 这类相对路径与当前版**完全可比**。
    然后各起一个子进程对同一份 .pptx 入库（假嵌入器），逐字比较结果。
    """
    src = REGRESS_PPTX if REGRESS_PPTX.is_file() else REGRESS_SMALL
    if AB_DIR.exists():
        shutil.rmtree(AB_DIR, ignore_errors=True)
    AB_DIR.mkdir(parents=True, exist_ok=True)
    for f in (ROOT / "core").glob("*.py"):
        shutil.copy2(f, AB_DIR / f.name)
    got = []
    for name in ("ingest.py", "extract.py"):
        p = subprocess.run(["git", "show", f"HEAD:core/{name}"], cwd=str(ROOT),
                           capture_output=True)
        if p.returncode != 0:
            check("A/B：能取到 git HEAD 版基线文件", False,
                  f"git show 失败: {p.stderr[:200]!r}")
            return {}
        (AB_DIR / name).write_bytes(p.stdout)
        got.append(f"{name}={len(p.stdout)}B")
    py = sys.executable
    outs = {}
    for pkg, tag in (("_tmp_c0065_old", "old"), ("core", "new")):
        dbp = ROOT / "index" / f"_tmp_c0065_ab_{tag}.db"
        outp = ROOT / "index" / f"_tmp_c0065_ab_{tag}.json"
        p = subprocess.run([py, "-c", _AB_RUNNER, str(ROOT), pkg, str(dbp),
                            str(src), str(outp)],
                           cwd=str(ROOT), capture_output=True)
        if p.returncode != 0:
            check("A/B：两版都能跑完入库", False,
                  f"{tag} 退出码 {p.returncode}: "
                  f"{(p.stderr or b'')[-300:].decode('utf-8', 'replace')}")
            return {}
        outs[tag] = json.loads(outp.read_text(encoding="utf-8"))
    same = outs["old"] == outs["new"]
    detail = "" if same else json.dumps(
        {"old_docs": outs["old"]["docs"], "new_docs": outs["new"]["docs"],
         "old_n_chunks": len(outs["old"]["chunks"]),
         "new_n_chunks": len(outs["new"]["chunks"])}, ensure_ascii=False)[:800]
    check("A/B：既有 .pptx 入库结果与改动前逐字一致（documents+chunks+返回值）",
          same, detail or f"{src.name}: docs/chunks/ret 全部相同 "
                          f"({len(outs['new']['chunks'])} 块)")
    return outs


# ---------------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser(description="C-0065 legacy 自动转换验收")
    ap.add_argument("--convert-all", action="store_true",
                    help="把 raw/ 下还没有 OOXML 产物的 .ppt 逐份转换")
    ap.add_argument("--ab", action="store_true", help="跑基线 A/B 对照")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    t0 = time.time()
    check_sources()
    check_products()
    check_script_contract()
    info = check_e2e(args.verbose)
    fb = check_fallback()
    reg = check_regression(args.verbose)
    if args.convert_all:
        convert_all()
    if args.ab:
        ab_compare()
    if AB_DIR.exists():
        shutil.rmtree(AB_DIR, ignore_errors=True)

    n_ok = sum(1 for c in CHECKS if c["ok"])
    total = len(CHECKS)
    verdict = "PASS" if n_ok == total else "FAIL"
    e2e = info.get("run1", {})
    # ---- 结论固定放在前 6 行（宿主会在输出末尾截断）----
    print(f"C0065 VERDICT: {verdict}")
    print(f"C0065 CHECKS: {n_ok}/{total} passed")
    print(f"C0065 LEGACY_E2E: {e2e.get('status')} kind={e2e.get('kind')} "
          f"chunks={info.get('chunks')} file={Path(str(e2e.get('path') or '')).name}")
    print(f"C0065 SRC_INTACT: {len(BASELINE)}/{len(BASELINE)} sha256+size 比对")
    print(f"C0065 FALLBACK: {fb.get('status')}")
    print(f"C0065 LOG: {LOG}")
    # ---- 明细 ----
    for c in CHECKS:
        print(f"  [{'OK ' if c['ok'] else 'BAD'}] {c['name']}")
        if c["detail"]:
            print(f"        {c['detail'][:600]}")
    print(f"C0065 DETAIL_JSON: "
          f"{json.dumps({'checks': CHECKS, 'regression': {k: v for k, v in reg.get('run1', {}).items() if k != 'warnings'}}, ensure_ascii=False)[:1500]}")
    print(f"C0065 ELAPSED: {round(time.time() - t0, 1)}s")
    # ---- 末尾冗余一次结论 ----
    print(f"C0065 VERDICT: {verdict} ({n_ok}/{total})")
    try:
        LOG.write_text("\n".join(
            [f"C0065 VERDICT: {verdict} ({n_ok}/{total})"]
            + [json.dumps({k: v for k, v in c.items()}, ensure_ascii=False)
               for c in CHECKS]) + "\n", encoding="utf-8")
    except OSError:
        pass
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
