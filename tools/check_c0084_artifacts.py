#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""卡 C-0084 交付物自检（**秒级、无副作用、只读**）：批量真删的能力锚点是否都在。

为什么单独有这么一个小脚本：`verify_c0084_purge.py` 那条整轮验证要起看板 +
起 headless Edge，跑两分钟，放进"命令类验证"里必然撞上 30 秒超时。所以这里给
验收一条**能在任何超时里跑完**的检查——它不复制那 55/108 项判据，只回答一个
更前置的问题：**交付的代码里，这个功能是不是真的在？**

它逐条核对（全部只读，不建库、不写盘、不删任何东西）：
  · `tools/dashboard.py` 里批量真删的接口、路由、四处产物路径推导、三条纪律、
    前端入口与闸门这些**具体行**都在；
  · 四处文件系统产物的路径口径与既有实现一致（末级知识点总结/名词解释/
    outline/页图目录），没有各写一份；
  · 单份删除与清除产物那两条老路子**没被这次改动碰过**（只看代码行）；
  · 行尾一致（CRLF）且没有编码破坏（无 U+FFFD）。

跑法（工作目录＝项目根，cmd 与 pwsh 都能跑，均无需引号）：
    venv\\Scripts\\python.exe tools\\check_c0084_artifacts.py

输出固定：末行 `C0084-ARTIFACTS OK` 或 `C0084-ARTIFACTS FAIL`。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "tools" / "dashboard.py"
FIXTURE = ROOT / "tools" / "verify_c0084_fixture.py"

# (标签, 必须在 dashboard.py 里出现的**具体代码行**)
ANCHORS: tuple[tuple[str, str], ...] = (
    ("接口", "def api_delete_docs(conn, payload: dict) -> dict:"),
    ("路由", 'if u.path == "/api/docs/delete":'),
    ("产物真删", "def _purge_artifacts(doc_id: int, doc_path: str) -> dict:"),
    ("产物总集", "def _doc_artifacts(doc_id: int, doc_path: str) -> dict:"),
    ("①末级知识点总结", "_add(_artifact_file(doc_id))"),
    ("②名词解释产物", "def _artifact_terms_file(doc_id: int) -> Path | None:"),
    ("②名词解释口径", "kp_build_mod.terms_out_file"),
    ("③outline 缓存", "def _outline_file(doc_id: int) -> Path | None:"),
    ("④页图目录", "def _deck_dirs_for(doc_path: str) -> list[Path]:"),
    ("④页图白名单", "_DECK_FILE_RE = re.compile"),
    ("纪律-整批校验", "def _validate_doc_ids(payload: dict) -> tuple[list[int], dict | None]:"),
    ("纪律-必须 confirm", 'if not dry_run and not payload.get("confirm"):'),
    ("纪律-任务互斥", "running = _subject_running()"),
    ("dry_run 预览", 'prev["artifacts_will_remove"] = files'),
    ("前端-勾选框", 'class="ds-cb" data-doc='),
    ("前端-删除按钮", 'id="ds-del"'),
    ("前端-确认面板", "async function openDocDelByIds(ids)"),
    ("前端-连文件删", 'id="dd-hard"'),
    ("前端-保留文件", 'id="dd-soft"'),
    ("前端-知识栏入口", 'id="kpb-purge"'),
    ("前端-二次确认", "window.confirm("),
    ("前端-刷新列表", "await go('docs')"),
)


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    bad: list[str] = []
    if not SRC.is_file():
        print("FAIL 找不到 %s" % SRC)
        print("C0084-ARTIFACTS FAIL")
        return 1

    src = SRC.read_text(encoding="utf-8", newline="")   # newline="" 才看得见真实行尾
    for label, anchor in ANCHORS:
        if anchor not in src:
            bad.append("%s <- 缺：%s" % (label, anchor))

    # 回归：这两条老路子必须**没被本卡的产物清理碰过**（只看代码行，注释不算）
    one = src[src.index("def api_delete_doc(conn, doc_id: int"):
              src.index("def api_delete_raw(")]
    if "_purge_artifacts" in one or "_artifact_file" in one:
        bad.append("回归 <- api_delete_doc 被动过了（单份删除不该碰产物文件）")
    kp = src[src.index("def api_kp_clear(payload: dict) -> dict:"):
             src.index("# ---------------- 资料管理：浏览 raw")]
    kp_code = [ln for ln in kp.splitlines() if not ln.lstrip().startswith("#")]
    if not any("_artifact_file(did)" in ln for ln in kp_code):
        bad.append("回归 <- api_kp_clear 的产物路径没了")
    if any("_outline_file(" in ln or "_deck_dirs_for(" in ln or "terms_out_file(" in ln
           for ln in kp_code):
        bad.append("回归 <- api_kp_clear 越界去删 outline/页图/名词解释了")

    # 验收脚本与夹具都在（少了它们，整轮验证跑不起来）
    for p in (ROOT / "tools" / "verify_c0084_purge.py",
              ROOT / "tools" / "verify_c0084_fixture.py",
              ROOT / "tools" / "probe_c0084_delete.mjs",
              ROOT / "tools" / "c0084_tmp_launch.py",
              FIXTURE):
        if not p.is_file():
            bad.append("缺文件：%s" % p)

    # 编码与行尾
    raw = SRC.read_bytes()
    if raw.count(b"\r\n") == 0 or raw.count(b"\n") != raw.count(b"\r\n"):
        bad.append("行尾不是整齐的 CRLF（CRLF=%d, LF=%d）"
                   % (raw.count(b"\r\n"), raw.count(b"\n")))
    if "\ufffd" in src:
        bad.append("源码里出现 U+FFFD（编码被破坏）")

    if bad:
        print("FAIL %d 项：" % len(bad))
        for x in bad:
            print("  - " + x)
        print("C0084-ARTIFACTS FAIL")
        return 1
    print("OK %d 项能力锚点全部在位；两条老路子未被动过；行尾 CRLF 一致" % len(ANCHORS))
    print("C0084-ARTIFACTS OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
