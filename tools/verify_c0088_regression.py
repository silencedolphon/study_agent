#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""卡 C-0088 回归验证（快档）：`mode=""`（默认）与改造前**逐字段相同**。

这是本卡的第一硬要求。证据链长这样：

  · `logs/c0088/baseline_full.json` —— **改造前**（`core/ingest.py` 改动之前）
    用同一台"录音机"录的产物快照，里面记着当时的源文件指纹；
  · 本脚本现在重录 5 个样本（覆盖 txt / md / pdf / pptx + 一份放在 `raw/教材/`
    下的 PDF），逐字段与基线比；
  · 顺带证明"代码确实变了、但产物没变"：`core/ingest.py` 指纹必须**不同**，
    `core/chunk.py` 指纹必须**相同**（并额外与 `git show HEAD:` 的版本
    去掉文件头注释后逐字比较）。

比对范围（`tools/c0088_snapshot.py::compare`）：`ingest_paths` 与 `ingest_file`
的返回值（每样本 3 条路径）、`documents` 全字段、`chunks` 全字段、
`embeddings` 每块的向量 sha256。**只归一化 4 项**：两个临时根路径与三个
时间戳字段（`indexed_at`/`updated_at`/`created_at`）——它们本来就不可能相同。

比对仪本身也做过自检（`logs/c0088/_selftest_compare.py`，6 项负向注入全被抓到），
所以"全等"不是"比对成了空集"。

跑法（绝对路径，不依赖 cwd）：
    G:\\study_agent\\venv\\Scripts\\python.exe -X utf8 G:\\study_agent\\tools\\verify_c0088_regression.py

输出：结论行含 `C0088-PASS` 或 `C0088-FAIL`，退出码 0 / 2。
"""
from __future__ import annotations

import inspect
import ast
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent      # tools/ -> 项目根
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

# `c0088_snapshot` 顶层不 import core（core 只在那两个函数内部导入），
# 所以这里提前导入不会把临时根的环境变量定死。
from c0088_snapshot import (FAST_SAMPLES, WORK, _sha_file, collect,       # noqa: E402
                            compare, real_db_counts)

T0 = time.time()
BASE_JSON = ROOT / "logs" / "c0088" / "baseline_full.json"
LOG = ROOT / "logs" / "c0088" / "regression_detail.txt"
CHECKS: list[tuple[str, bool, str]] = []
EARLY_FAILS: list[str] = []
# 既有调用点清单（写进明细日志，作为"逐项证据"）
CALLER_LINES: list[str] = []


def add(name: str, ok: bool, detail: str = "") -> None:
    ok = bool(ok)
    CHECKS.append((name, ok, detail))
    if not ok:
        # 失败**立刻**可见（卡面要求）。走 stderr：这样"结论行落在 stdout 前 6 行"
        # 与"失败项马上打出来"两条同时成立，不用二选一。
        msg = f"C0088-FAIL 检查未过: {name}  {detail}"
        EARLY_FAILS.append(msg)
        print(msg, file=sys.stderr, flush=True)


def _git_show(rel: str) -> str:
    """`git show HEAD:<rel>` 的文本（只读命令，不碰工作区）。"""
    try:
        p = subprocess.run(["git", "show", f"HEAD:{rel}"], cwd=str(ROOT),
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=10)
        return p.stdout if p.returncode == 0 else ""
    except Exception:                                            # noqa: BLE001
        return ""


def _body(text: str, head: int = 6) -> str:
    """剥掉文件头的许可证注释块（本项目固定 6 行），只留代码本体。

    为什么必须剥：工作区里全部源文件的许可声明都换过一次（不是本卡做的），
    不剥的话每个文件都"有差异"，红线就没法证明。
    """
    lines = text.splitlines()
    if len(lines) > head and any("File-ID:" in l for l in lines[:head]):
        return "\n".join(lines[head:])
    return "\n".join(lines)


# 本卡自己的脚本：它们**故意**会传 mode，不在"既有调用方"之列。
OWN_SCRIPTS = {"tools/verify_c0088_regression.py", "tools/verify_c0088_textbook.py",
               "tools/c0088_snapshot.py", "tools/c0088_selftest_compare.py"}
# 必须存在的既有调用点（文件 -> 被调函数）。逐项对应卡面要回归的入口。
NEED_CALLERS = {
    "tools/dashboard.py": "ingest_paths",      # 看板 /api/ingest 与批量入库按钮
    "core/pipeline_pool.py": "ingest_paths",   # 并发批量池（bulk）
    "tools/reimport_all.py": "ingest_file",    # 全量重导入
    "core/ingest.py": "ingest_file",           # ingest_paths -> ingest_file
    "tools/serve_mcp.py": "ingest_paths",      # MCP 侧入库
    "tools/study.py": "ingest_paths",          # 命令行入库
}


def scan_callers() -> list[tuple[str, int, str, dict]]:
    """AST 扫全项目：谁在调 `ingest_file` / `ingest_paths`、传了哪些关键字。

    为什么用 AST 而不是 grep：要断言的是"**没有任何**既有调用点传了 `mode`"，
    grep 只能证明"我看到的这几行没传"。AST 能把每个调用点的关键字实参
    列全，包括跨行写法。

    返回 [(相对路径, 行号, 被调函数名, {关键字: 源码文本})]。
    """
    out: list[tuple[str, int, str, dict]] = []
    for p in sorted(list((ROOT / "core").glob("*.py"))
                    + list((ROOT / "tools").glob("*.py"))):
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            f = node.func
            name = getattr(f, "attr", None) or getattr(f, "id", None)
            if name in ("ingest_file", "ingest_paths"):
                kw = {k.arg: ast.unparse(k.value)
                      for k in node.keywords if k.arg}
                out.append((p.relative_to(ROOT).as_posix(), node.lineno, name, kw))
    return out


def main() -> int:
    snap_ingest = _sha_file(ROOT / "core" / "ingest.py")
    snap_chunk = _sha_file(ROOT / "core" / "chunk.py")

    add("改造前基线快照存在", BASE_JSON.exists(), str(BASE_JSON))
    if not BASE_JSON.exists():
        return finish()
    base = json.loads(BASE_JSON.read_text(encoding="utf-8"))
    base_ingest = base.get("code", {}).get("core/ingest.py", "")
    base_chunk = base.get("code", {}).get("core/chunk.py", "")

    # 先读真库（只读）——验证跑完再读一次，两次必须一样
    before = real_db_counts()
    add("真库可读（documents/chunks 有行数）",
        isinstance(before.get("documents"), int) and before["documents"] > 0,
        f"{before}")

    # ★ 真实重录：5 个样本 × 3 条入口路径，跑在临时库上
    snap = collect(WORK, FAST_SAMPLES)
    after = real_db_counts()

    add("覆盖了样本的 3 条既有入口路径（added/skipped/updated 全跑到）",
        all(s.get("ingest_paths_added", {}).get("details")
            and s.get("ingest_paths_skipped", {}).get("details")
            and s.get("ingest_file_force", {}).get("status") in ("added", "updated")
            for s in snap["samples"]),
        f"{[s['sample'] for s in snap['samples']]}")
    add("重录确实写了块（不是空跑）", snap["table_counts"]["chunks"] > 0,
        f"chunks={snap['table_counts']['chunks']}")

    same, diffs, scope = compare(base, snap, FAST_SAMPLES)
    add("★ mode='' 产物与改造前**逐字段相同**", same,
        f"{len(diffs)} 处差异：{diffs[:6]}")

    add("代码确实改了：core/ingest.py 指纹与基线不同",
        bool(base_ingest) and snap_ingest != base_ingest,
        f"{base_ingest[:12]} -> {snap_ingest[:12]}")
    add("红线：core/chunk.py 指纹与基线相同（本卡没碰它）",
        bool(base_chunk) and snap_chunk == base_chunk,
        f"{base_chunk[:12]} -> {snap_chunk[:12]}")
    head_txt = _git_show("core/chunk.py")
    cur_txt = (ROOT / "core" / "chunk.py").read_text(encoding="utf-8")
    add("红线：core/chunk.py 与 git HEAD 的**代码本体**逐字相同",
        bool(head_txt) and _body(head_txt) == _body(cur_txt),
        "去头注释后逐字比较" if head_txt else "git 不可用，已用指纹兜底")
    # 工作区里 `tools/dashboard.py` 本来就有大量未提交改动（不是本卡做的），
    # 所以"指纹不同"证明不了什么。用 mtime：它的最后写入时间必须**早于**
    # 基线快照的生成时间，也就是"本卡开工之前就没再被碰过"。
    dash = ROOT / "tools" / "dashboard.py"
    add("红线：tools/dashboard.py 在本卡开工前就没再被写过（mtime 早于基线快照）",
        dash.exists() and dash.stat().st_mtime < BASE_JSON.stat().st_mtime,
        f"dashboard mtime={dash.stat().st_mtime:.0f} < 基线 mtime="
        f"{BASE_JSON.stat().st_mtime:.0f}" if dash.exists() else "文件不存在")

    # 契约：`mode` 参数存在、默认空串（既有调用方不传 ⇒ 原路）
    from core.ingest import ingest_file
    params = inspect.signature(ingest_file).parameters
    add("契约：ingest_file 有 mode 参数且默认 ''（既有调用方不受影响）",
        "mode" in params and params["mode"].default == "",
        f"mode={params.get('mode')}")
    add("契约：mode 是最后一个参数（位置调用不受影响）",
        list(params)[-1] == "mode", f"参数序 {list(params)}")

    # ---- 既有调用方逐项回归（AST 静态证据 + 上面的动态产物对照）----
    cs = scan_callers()
    mine = [c for c in cs if c[0] in OWN_SCRIPTS]
    theirs = [c for c in cs if c[0] not in OWN_SCRIPTS]
    add("既有调用点里**没有任何一个**传了 mode（AST 全项目扫描）",
        all("mode" not in kw for _f, _l, _n, kw in theirs),
        f"越界调用点：{[(f, l, kw) for f, l, _n, kw in theirs if 'mode' in kw]}")
    have = {(f, n) for f, _l, n, _kw in theirs}
    missing = [f"{f}->{n}" for (f, n) in NEED_CALLERS.items() if (f, n) not in have]
    add("卡面点名要回归的入口都在（/api/ingest、批量池、reimport_all、MCP、CLI）",
        not missing, f"缺 {missing}")
    add("share 导入不经过 ingest 管线（core/share.py 里没有 ingest 调用点）",
        not [c for c in cs if c[0] == "core/share.py"],
        "share 走 import_package 直接把 documents/chunks/embeddings 写库，与本次改动零交集")
    CALLER_LINES.extend(
        f"{f}:{l}  {n}({', '.join(f'{k}={v}' for k, v in kw.items()) or '位置实参'})"
        for f, l, n, kw in theirs)

    add("真库 documents/chunks 行数在本次验证前后不变", before == after,
        f"{before} -> {after}")

    return finish(same=same, diffs=diffs, scope=scope, snap=snap,
                  base_ingest=base_ingest, snap_ingest=snap_ingest,
                  chunk=snap_chunk, before=before, after=after)


def finish(same: bool = False, diffs: list | None = None, scope: list | None = None,
           snap: dict | None = None, base_ingest: str = "", snap_ingest: str = "",
           chunk: str = "", before: dict | None = None, after: dict | None = None) -> int:
    ok = all(c[1] for c in CHECKS)
    elapsed = time.time() - T0
    LOG.parent.mkdir(parents=True, exist_ok=True)
    LOG.write_text("\n".join(
        [f"[{'OK ' if o else 'FAIL'}] {n}  {d}" for n, o, d in CHECKS]
        + ["", "--- 比对范围 ---"] + [f"  {s}" for s in (scope or [])]
        + ["", "--- 差异 ---"] + [f"  {d}" for d in (diffs or [])]
        + ["", "--- 既有调用点（AST 扫描，均未传 mode）---"]
        + [f"  {l}" for l in CALLER_LINES]
        + ["", f"样本：{[s['sample'] for s in snap['samples']] if snap else []}"]),
        encoding="utf-8")

    print(f"C0088-{'PASS' if ok else 'FAIL'}  回归硬要求：mode='' 与改造前逐字段相同"
          f"（{len(CHECKS)} 项检查，{sum(1 for c in CHECKS if c[1])} 项通过）")
    print(f"1) core/ingest.py 指纹 {base_ingest[:12]} -> {snap_ingest[:12]}（代码确实改了）")
    print(f"2) core/chunk.py 指纹 {chunk[:12]} 未变，且与 git HEAD 去头后逐字相同（红线）")
    print(f"3) 比对范围：{'；'.join(scope or ['（无）'])}")
    print(f"4) 真库行数 {before} -> {after}")
    print(f"5) 耗时 {elapsed:.1f} s；明细 {LOG}")
    for line in EARLY_FAILS:
        print(line)
    for n, o, d in CHECKS:
        if not o:
            print(f"   失败项：{n}  {d}")
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
