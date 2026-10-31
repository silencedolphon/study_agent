#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""发布包生成脚本：白名单打包 + 密钥/隐私审计门禁（卡 C-0067）。

用法（在项目根目录执行；解释器用项目自带的 venv）：

    venv\\Scripts\\python.exe -X utf8 tools\\release_pack.py
        打包 -> dist\\study_agent-<版本>-windows.zip
    venv\\Scripts\\python.exe -X utf8 tools\\release_pack.py --audit-only
        只审计、不产出产物
    venv\\Scripts\\python.exe -X utf8 tools\\release_pack.py --verify-package dist\\xxx.zip
        对已产出的包独立复检（路径禁区 + 内容密钥）
    venv\\Scripts\\python.exe -X utf8 tools\\release_pack.py --self-test
        审计门禁自检：在系统临时目录里造沙盒，含反面验证（注入敏感文件须被拦）
    venv\\Scripts\\python.exe -X utf8 tools\\release_pack.py --tools-manifest <out.md>
        把「哪些 tools 脚本进包/不进包」的逐个判定清单写到磁盘

设计要点（与卡 C-0067 的硬要求逐条对应）：

  1. **白名单打包**：只拷显式列出的内容（下面 TOP_FILES / CORE_DIR /
     TOOLS_RELEASE），其余一律不进包。新增的开发脚本不会"顺手"溜进包里。
  2. **审计在写 zip 之前跑**；任何命中都**中止打包并报错**（退出码 2），
     不是警告。命中时不会产出（也不会覆盖旧）产物。
  3. 产物写完后**立即用同一套规则回读复检**，不一致就删掉产物并报错。
  4. 幂等：重复运行覆盖旧产物（先写临时文件再原子替换，不留半成品）；
     全过程**只读**源文件，绝不修改被拷贝的源文件。

退出码：0 成功 / 2 审计未通过 / 3 前置条件不满足（缺必需文件）/ 1 意外错误。
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_VERSION = "0.1.0"
PKG_PREFIX = "study_agent"

# ---------------------------------------------------------------------------
# 白名单：进包内容（**只拷这里列出的东西**）
# ---------------------------------------------------------------------------

# 顶层文件：(文件名, 是否必需, 为什么进包)
TOP_FILES: list[tuple[str, bool, str]] = [
    ("README.md", True, "产品说明与首次配置指引（面向用户的主文档）"),
    ("NOTICE.txt", False, "版权与第三方组件声明"),
    ("LICENSE", False, "开源许可证（由许可卡产出；缺失时只警告并记入报告）"),
    ("requirements.txt", True, "32 个运行时依赖，装环境必需"),
    (".env.example", True, "纯占位符的配置样例（复制成 .env 用）"),
    (".gitignore", False, "忽略规则（含密钥与用户数据，防止用户误提交）"),
]

# core/ 整个目录进包（排除 __pycache__/*.pyc）——业务逻辑层
CORE_DIR = "core"
CORE_REASON = "业务逻辑层，被 CLI 与看板共用（core/*.py 全部进包）"

# tools/ 下的**发布用**脚本：逐个判定后进包的名单。
# 判定口径见 tools 清单（--tools-manifest）里的逐条理由。
TOOLS_RELEASE: dict[str, str] = {
    # —— 运行必需 ——
    "dashboard.py": "R1 学习看板 Web UI 主程序（双击启动器的目标）",
    "study.py": "R1 命令行主入口（check/ingest/search/docs/stats/kp/review/mistake/note）",
    "serve_mcp.py": "R1 MCP server（stdio），把知识库暴露给 DSH 等助手",
    "convert_legacy.ps1": "R1 core/extract.py 运行时**直接调用**：旧版 .doc/.ppt/.xls 转 OOXML",
    "render_pptx_slides.ps1": "R1 core/figures.py 运行时**直接调用**：PowerPoint COM 渲染整页图",
    # —— 安装必需 ——
    "download_model.py": "R2 首次运行下载本地嵌入模型（约 100MB），没有它检索跑不起来",
    # —— 面向用户的 CLI（空库用户用得上，且不依赖开发夹具）——
    "kp_build.py": "R3 AI 知识点整理管线命令行（看板提示语里点名的那条命令）",
    "terms_build.py": "R3 名词解释独立命令行",
    "reimport_all.py": "R3 批量入库（README 点名推荐：按资料提交、中断可续）",
    "reingest_doc.py": "R3 单份资料重建（先存识图块再重入库，用户改过源文件时用）",
    "split_multippt.py": "R3 把一份含多课件的 pptx 拆开（资料准备阶段用）",
    "make_test_files.py": "R3 生成真实格式的测试文件，供用户第一次试跑管线",
    "inventory.py": "R3 库清单（文档/切块/识图统计），排查用",
    "list_docs.py": "R3 列出全部资料并按内容哈希找重复入库",
    "run_figure_desc.py": "R3 整章语境识图驱动（pptx 走 COM、pdf 走 pymupdf）",
    "ingest_figures.py": "R3 把识图结果写回知识库（run_figure_desc 的落库端）",
    "restore_figures.py": "R3 误删识图块后的恢复工具",
    "serve_mcp.cmd": "R1 MCP 启动包装（**包内以相对路径适配版生成**，见下）",
}

# 包内**重新生成**的文件（不拷贝源文件；源文件硬编码了作者本机 G:\\study_agent 路径）
# 用"包内路径"（arc）表示，前缀 tools/ 不能省——判定时必须按 arc 比。
GENERATED_ARCS: tuple[str, ...] = ("看板.cmd", "安装说明.txt", "tools/serve_mcp.cmd")

# ---------------------------------------------------------------------------
# 审计规则：这些内容一个字节都不许进包
# ---------------------------------------------------------------------------

FORBIDDEN_DIRS = {
    "subjects", "index", "raw", "logs", "venv", "models", "cards",
    ".git", "__pycache__", "backup", "_smoke_fixtures", ".dsh",
    "dist", "node_modules", ".idea", ".vscode",
}
FORBIDDEN_NAMES = {
    ".env", ".env.local", "config.local.json", "config.local.json.tmp",
    ".credentials.yaml", "credentials.yaml", "credentials.json",
}
FORBIDDEN_SUFFIXES = (
    ".db", ".db-wal", ".db-shm", ".db-journal", ".sqlite", ".sqlite3",
    ".pyc", ".pyo", ".key", ".pem", ".pfx", ".p12", ".onnx", ".log",
    ".pdf", ".pptx", ".docx", ".doc", ".ppt", ".xls", ".xlsx",
)

# 密钥/凭据模式：(代号, 正则, 取哪一组做"占位符"判定)
SECRET_PATTERNS: list[tuple[str, re.Pattern[str], int]] = [
    ("sk_key", re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}"), 0),
    ("pem_private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), 0),
    # 注意：这里**不写左词边界**。`DEEPSEEK_API_KEY=` 这种最常见的形态，
    # `API` 前面是下划线（属于 \w），带 \b 会整条漏检——实测踩过。
    ("assigned_secret", re.compile(
        r"(?i)(?:api[_-]?key|apikey|secret[_-]?key|client[_-]?secret|"
        r"access[_-]?token|auth[_-]?token|password|passwd)\s*[:=]\s*"
        r"[\"']?([A-Za-z0-9_\-/+]{16,})"), 1),
]
# 命中里带这些词的一律算占位符（排除文档与样例文件的误伤）
PLACEHOLDER_OK = re.compile(
    r"(?i)(replace[_-]?me|change[_-]?me|your[_-]|placeholder|example|dummy|"
    r"fake|sample|redacted|xxx+|\*{3,}|<[^>]*>|\.\.\.|sk-REPLACE)")

# 源码里的标识符赋值也会撞上 `api_key = ...` 这类模式（实测：core/config.py 的
# `ch.load_api_key = _patched_load_api_key`）。判定口径：
#   形如 Python 标识符 **且** 下划线 ≥ 2 个 → 是变量/函数名，不是密钥。
# 只对 assigned_secret 生效；`sk-xxxx` 与 PEM 私钥两条规则不豁免。
IDENT_LIKE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _is_benign(code: str, probe: str) -> bool:
    if PLACEHOLDER_OK.search(probe):
        return True
    if code == "assigned_secret" and probe.count("_") >= 2 and IDENT_LIKE.match(probe):
        return True
    return False

# ---------------------------------------------------------------------------
# tools 判定：不进包的原因（前缀/后缀规则，按顺序匹配；白名单优先级最高）
# ---------------------------------------------------------------------------

TOOLS_EXCLUDE_RULES: list[tuple[str, str, str]] = [
    ("author_mark.py", "精确", "著作权人侧工具（源码交付前打版权标记），卡里明确不进包"),
    ("audit_source.py", "精确", "源码/知识库一致性审计（内部合规工具），非运行必需"),
    ("smoke_test.py", "精确", "端到端验收（228 项，依赖开发夹具），属验收而非运行"),
    ("verify_", "前缀", "验收脚本：卡验收 / 大管家独立复核用，非运行必需"),
    ("probe_", "前缀", "探针脚本：一次性根因、性能、接口探测"),
    ("diag_", "前缀", "诊断脚本：开发期调参与口径确认"),
    ("check_", "前缀", "静态/接口自检脚本：开发验收用"),
    ("bench_", "前缀", "基准脚本：模型与管线选型实测"),
    ("measure_", "前缀", "量化实验脚本：度量增量与收益"),
    ("pilot_", "前缀", "试点实验脚本：先行试跑用"),
    ("compare_", "前缀", "对比实验脚本：模型/版本对照"),
    ("investigate_", "前缀", "调查脚本：一次性定位问题"),
    ("dump_", "前缀", "调试导出脚本：把内部结构打印出来看"),
    ("show_", "前缀", "调试展示脚本：看单块/单表/单页内容"),
    ("trace_", "前缀", "调试追踪脚本"),
    ("test_", "前缀", "测试脚本（含 DOM/浏览器测试），开发验收用"),
    ("find_", "前缀", "运维查找脚本：历史数据的一次性修补"),
    ("backfill_", "前缀", "运维回填脚本：给历史产物补字段"),
    ("classify_", "前缀", "一次性分析脚本（结论已写进代码）"),
    ("profile_", "前缀", "性能/形状剖析脚本（一次性定阈值）"),
    ("tune_", "前缀", "调参脚本（一次性）"),
    ("_c00", "前缀", "卡内一次性施工脚本（某张卡的过程产物）"),
    ("steward_", "前缀", "大管家（内部管理）工具，与产品运行无关"),
    ("edge_", "前缀", "浏览器探针：headless Edge + CDP 实验"),
    (".mjs", "后缀", "浏览器/DOM 探针脚本（Node 侧实验）"),
    ("split_pages", "前缀", "一次性分档脚本（edge 序列分档实验）"),
    ("reingest_pptx.py", "精确", "pptx 专用重入库（会连带删识图块），风险高于收益"),
    ("dashboard.cmd", "精确", "转发到顶层 看板.cmd 的旧包装，且硬编码本机路径；包内用生成的启动器"),
    ("parse_desc_log.py", "精确", "识图日志解析（离线实验链中间件）"),
    ("merge_figure_summary.py", "精确", "识图 summary 合并（离线实验链中间件）"),
    ("figure_progress.py", "精确", "识图进度查看（看板已有进度显示）"),
    ("outline_deck.py", "精确", "只看层级的实验入口（产品走 core/outline.py）"),
    ("which_pages_need_vision.py", "精确", "选页实验脚本（一次性）"),
    ("vision_bench.py", "精确", "单页视觉描述实测（选型用）"),
    ("blocks_of_page.py", "精确", "调试：看某页切块"),
    ("peek_db.py", "精确", "调试：直接看库"),
    ("show_chunk.py", "精确", "调试：看单块（CLI 有 study.py show）"),
    ("preview_extract.py", "精确", "调试：预览提取结果"),
    ("scan_table_pages.py", "精确", "调试：扫描表格页"),
    ("show_table_overview.py", "精确", "调试：表格总览"),
    ("show_table_recovery.py", "精确", "调试：表格二维还原演示"),
    ("show_deck_tree.py", "精确", "调试：看课件结构树"),
    ("find_table_chunks.py", "精确", "调试：找表格块"),
    ("test_table_ingest.py", "精确", "表格入库测试"),
    ("check_reuse_hash.py", "精确", "复用哈希自检"),
    ("parse_desc_log_", "前缀", "识图日志解析配套"),
    ("release_pack.py", "精确", "发布流水线脚本本身（开发侧工具，不进包）"),
]

# 生成文件模板 -------------------------------------------------------------

LAUNCHER_CMD = r"""@echo off
setlocal
title study_agent - Study Dashboard

REM ============================================================
REM  Study Dashboard launcher for the distributed package.
REM  Every path is resolved relative to this file (%~dp0), so the
REM  folder can be unzipped anywhere and still work.
REM  Default URL: http://127.0.0.1:8756/
REM  Closing this window stops the dashboard.
REM
REM  NOTE: keep this file ASCII-only.  cmd.exe parses .cmd with the
REM  system OEM code page (GBK on Chinese Windows), so non-ASCII text
REM  here gets mis-decoded byte-by-byte and corrupts command parsing.
REM  NOTE: avoid round brackets inside if-blocks, for the same reason.
REM ============================================================

set "PY=%~dp0venv\Scripts\python.exe"
set "APP=%~dp0tools\dashboard.py"
set "PORT=8756"

if not exist "%PY%" (
    echo.
    echo   [ERROR] Python interpreter not found:
    echo           %PY%
    echo.
    echo   Create the environment first:
    echo     python -m venv venv
    echo     venv\Scripts\python.exe -m pip install -r requirements.txt
    echo     venv\Scripts\python.exe -X utf8 tools\download_model.py
    echo.
    echo   See README.md for details.
    echo.
    pause
    exit /b 1
)

if not exist "%APP%" (
    echo.
    echo   [ERROR] Dashboard program not found:
    echo           %APP%
    echo.
    pause
    exit /b 1
)

REM Exit code 0 means the port is already listening.
powershell -NoProfile -ExecutionPolicy Bypass -Command "if (Get-NetTCPConnection -State Listen -LocalPort %PORT% -ErrorAction SilentlyContinue) { exit 0 } else { exit 1 }" >nul 2>&1
if not errorlevel 1 (
    echo.
    echo   Port %PORT% is already in use. The dashboard is probably already running.
    echo   Opening: http://127.0.0.1:%PORT%/
    echo.
    echo   To run a second instance on another port:
    echo     "%PY%" "%APP%" --port 8757
    echo.
    start "" "http://127.0.0.1:%PORT%/"
    echo   Press any key to close this window. The running dashboard is unaffected.
    pause >nul
    exit /b 0
)

echo.
echo   Starting the study dashboard...
echo   URL: http://127.0.0.1:%PORT%/
echo.
echo   Your browser will open automatically.
echo   Close this window to stop the dashboard.
echo   --------------------------------------------------
echo.

REM dashboard.py opens the browser itself.
"%PY%" -X utf8 "%APP%" --port %PORT%

echo.
echo   Dashboard stopped.
pause
endlocal
"""

MCP_CMD = r"""@echo off
REM study_agent MCP server launcher (generated for the distributed package).
REM Paths are relative to this file, so the folder can live anywhere.
REM NOTE: stdout is the MCP protocol channel - never print anything else there.
REM Keep this file pure ASCII: cmd.exe parses .cmd as GBK, so non-ASCII bytes
REM in comments get misread and fragments can be executed as bogus commands.
"%~dp0..\venv\Scripts\python.exe" -X utf8 "%~dp0serve_mcp.py" %*
"""

INSTALL_NOTE = """study_agent —— 分发包安装说明（Windows）
================================================

这是一个**空程序**：不含任何示例课件、不含任何用户数据、不含任何密钥。

一、准备 Python
------------------------------------------------
装 Python 3.11 或更高版本（安装时勾选 "Add python.exe to PATH"）。

二、建独立环境并装依赖
------------------------------------------------
在本文件夹下打开命令行（地址栏输入 cmd 回车），依次执行：

    python -m venv venv
    venv\\Scripts\\python.exe -m pip install -r requirements.txt

国内网络慢的话，给 pip 加个镜像：

    venv\\Scripts\\python.exe -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

三、下载本地嵌入模型（约 100MB，只需一次，之后完全离线）
------------------------------------------------
    venv\\Scripts\\python.exe -X utf8 tools\\download_model.py

四、填大模型密钥（要做 AI 问答/整理/识图才需要）
------------------------------------------------
复制 .env.example 为 .env，把 DEEPSEEK_API_KEY 换成你自己的密钥；
也可以先跳过，启动看板后在右上角「设置」里填（保存即生效）。

五、启动
------------------------------------------------
双击本文件夹里的「看板.cmd」，浏览器会自动打开 http://127.0.0.1:8756/ 。

关掉那个黑窗口就等于停止看板；看板是本地服务，不联网、不对外暴露。

六、把资料放进去
------------------------------------------------
    venv\\Scripts\\python.exe -X utf8 tools\\study.py ingest

或者打开看板的「资料」页直接上传再点「入库」。

更详细的说明见 README.md。
"""


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass
class Entry:
    """一个待打包条目。"""

    arc: str                 # zip 内相对路径（不含顶层包目录），用 / 分隔
    reason: str              # 为什么进包（写进清单）
    src: Path | None = None  # 源文件（只读）
    data: bytes | None = None  # 生成内容（src 为 None 时用）

    def read(self) -> bytes:
        if self.data is not None:
            return self.data
        assert self.src is not None
        return self.src.read_bytes()

    def origin(self) -> str:
        return "generated" if self.src is None else "source"


@dataclass
class Finding:
    """一条审计发现。"""

    code: str      # FORBIDDEN_PATH / SECRET_CONTENT
    where: str     # 包内路径
    detail: str

    def render(self) -> str:
        return "[BLOCK] %-16s %s | %s" % (self.code, self.where, self.detail)


class PackError(RuntimeError):
    """前置条件不满足。"""


# ---------------------------------------------------------------------------
# 白名单收集
# ---------------------------------------------------------------------------

def _is_core_file(p: Path) -> bool:
    parts = set(p.parts)
    if "__pycache__" in parts:
        return False
    if p.suffix.lower() in (".pyc", ".pyo"):
        return False
    if ".bak" in p.name.lower() or p.name.endswith("~"):
        return False
    return p.is_file()


def collect(src: Path) -> tuple[list[Entry], list[str]]:
    """按白名单收集待打包条目。返回 (条目, 警告)。"""

    if not src.is_dir():
        raise PackError("源目录不存在：%s" % src)

    entries: list[Entry] = []
    warnings: list[str] = []
    missing_required: list[str] = []

    for name, required, reason in TOP_FILES:
        p = src / name
        if p.is_file():
            entries.append(Entry(arc=name, reason=reason, src=p))
        elif required:
            missing_required.append(name)
        else:
            warnings.append("可选文件缺失，未进包：%s（%s）" % (name, reason))

    core = src / CORE_DIR
    if not core.is_dir():
        missing_required.append(CORE_DIR + "/")
    else:
        for p in sorted(core.rglob("*")):
            if _is_core_file(p):
                arc = p.relative_to(src).as_posix()
                entries.append(Entry(arc=arc, reason=CORE_REASON, src=p))

    for name, reason in TOOLS_RELEASE.items():
        if ("tools/" + name) in GENERATED_ARCS:
            continue  # 包内用生成版（见下），不拷源文件
        p = src / "tools" / name
        if p.is_file():
            entries.append(Entry(arc="tools/" + name, reason=reason, src=p))
        else:
            warnings.append("白名单里的 tools 脚本不存在，已跳过：tools/%s" % name)

    for name in ("dashboard.py", "study.py"):
        if not (src / "tools" / name).is_file():
            missing_required.append("tools/" + name)

    # 包内生成的文件（不拷源文件：源启动脚本硬编码了作者本机绝对路径）
    entries.append(Entry(arc="看板.cmd", reason="包内生成的启动器（路径改为 %~dp0 相对定位）",
                         data=LAUNCHER_CMD.replace("\n", "\r\n").encode("ascii")))
    entries.append(Entry(arc="安装说明.txt",
                         reason="包内生成的上手指引（UTF-8 BOM，Windows 记事本可读）",
                         data=b"\xef\xbb\xbf" + INSTALL_NOTE.replace("\n", "\r\n").encode("utf-8")))
    entries.append(Entry(arc="tools/serve_mcp.cmd",
                         reason="包内生成的 MCP 启动包装（路径改为 %~dp0 相对定位）",
                         data=MCP_CMD.replace("\n", "\r\n").encode("ascii")))

    if missing_required:
        raise PackError("缺少必需文件，无法打包：" + ", ".join(missing_required))

    entries.sort(key=lambda e: e.arc)
    return entries, warnings


def scan_source_isolation(src: Path) -> list[str]:
    """报告源目录里存在、但**已被白名单隔离**的敏感实体（信息性，不阻断）。"""

    out: list[str] = []
    for name in sorted(FORBIDDEN_DIRS):
        p = src / name
        if p.is_dir():
            if name == "venv":
                out.append("venv/  (文件数未统计：体积大且与打包无关)")
                continue
            n = sum(1 for _ in p.rglob("*") if _.is_file())
            try:
                size = "%.1f MB" % (sum(f.stat().st_size for f in p.rglob("*")
                                         if f.is_file()) / 1048576)
            except OSError:
                size = "?"
            out.append("%s/  (%d 个文件, %s)" % (name, n, size))
    for name in sorted(FORBIDDEN_NAMES):
        p = src / name
        if p.is_file():
            out.append("%s  (%d 字节)" % (name, p.stat().st_size))
    for p in src.glob("*.db*"):
        out.append("%s  (%d 字节)" % (p.name, p.stat().st_size))
    return out


# ---------------------------------------------------------------------------
# 审计
# ---------------------------------------------------------------------------

def path_findings(arc: str) -> list[str]:
    """包内路径是否踩禁区。"""

    reasons: list[str] = []
    parts = arc.split("/")
    for seg in parts[:-1]:
        if seg.lower() in FORBIDDEN_DIRS:
            reasons.append("路径中含禁区目录 '%s/'" % seg)
    name = parts[-1]
    low = name.lower()
    if low in FORBIDDEN_NAMES:
        reasons.append("禁区文件名 '%s'" % name)
    for suf in FORBIDDEN_SUFFIXES:
        if low.endswith(suf):
            reasons.append("禁区后缀 '%s'" % suf)
    if ".bak" in low or low.endswith("~"):
        reasons.append("备份文件")
    return reasons


def _looks_text(data: bytes) -> bool:
    if b"\x00" in data[:4096]:
        return False
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def content_findings(arc: str, data: bytes) -> list[Finding]:
    """包内文件内容是否含真实密钥。"""

    if not _looks_text(data):
        return []
    text = data.decode("utf-8", errors="replace")
    out: list[Finding] = []
    for code, rx, grp in SECRET_PATTERNS:
        for m in rx.finditer(text):
            probe = m.group(grp)
            if _is_benign(code, probe):
                continue
            line = text.count("\n", 0, m.start()) + 1
            head = probe[:8]
            out.append(Finding("SECRET_" + code, arc,
                               "第 %d 行命中密钥模式：%s***（已打码）" % (line, head)))
    return out


def audit_entries(entries: list[Entry]) -> list[Finding]:
    """对待打包条目做全量审计（路径 + 内容）。"""

    findings: list[Finding] = []
    seen: set[str] = set()
    for e in entries:
        if e.arc in seen:
            findings.append(Finding("FORBIDDEN_PATH", e.arc, "重复条目"))
        seen.add(e.arc)
        for r in path_findings(e.arc):
            findings.append(Finding("FORBIDDEN_PATH", e.arc, r))
        for f in content_findings(e.arc, e.read()):
            findings.append(f)
    return findings


def audit_zip(zip_path: Path) -> tuple[list[Finding], int, int]:
    """对一个已产出的 zip 做独立复检。返回 (发现, 文件数, 字节数)。"""

    findings: list[Finding] = []
    count = 0
    total = 0
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            count += 1
            total += info.file_size
            arc = info.filename
            top = arc.split("/", 1)
            rel = top[1] if len(top) == 2 else arc
            for r in path_findings(rel):
                findings.append(Finding("FORBIDDEN_PATH", rel, r))
            data = zf.read(info)
            findings.extend(content_findings(rel, data))
    return findings, count, total


# ---------------------------------------------------------------------------
# 产出
# ---------------------------------------------------------------------------

def write_zip(entries: list[Entry], out_zip: Path, top: str) -> tuple[int, int]:
    """写 zip（先写临时文件再原子替换，保证幂等且不留半成品）。"""

    out_zip.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_zip.with_suffix(out_zip.suffix + ".tmp")
    count = 0
    total = 0
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for e in entries:
            data = e.read()
            zi = zipfile.ZipInfo(top + "/" + e.arc, date_time=_zip_time(e))
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.external_attr = 0o644 << 16
            if not e.arc.isascii():
                zi.flag_bits |= 0x800  # 显式声明 UTF-8 文件名（中文名靠它）
            zf.writestr(zi, data)
            count += 1
            total += len(data)
    os.replace(tmp, out_zip)
    return count, total


def _zip_time(e: Entry) -> tuple[int, int, int, int, int, int]:
    try:
        st = (e.src or Path(__file__)).stat()
        t = time.localtime(st.st_mtime)
    except OSError:
        t = time.localtime()
    return (t.tm_year, t.tm_mon, t.tm_mday, t.tm_hour, t.tm_min, t.tm_sec)


def write_manifest(entries: list[Entry], path: Path, top: str, version: str, src: Path) -> None:
    lines = [
        "# study_agent %s —— Windows 分发包文件清单" % version,
        "# 生成时间：%s" % time.strftime("%Y-%m-%d %H:%M:%S"),
        "# 源目录：%s" % src,
        "# 包顶层目录：%s/" % top,
        "# 合计：%d 个文件，%.2f MB" % (len(entries), sum(len(e.read()) for e in entries) / 1048576),
        "# 本文件供人工复核：任何不在这个清单里的路径都不应出现在 zip 里。",
        "# 列：相对路径 <TAB> 字节 <TAB> sha256前16位 <TAB> 来源 <TAB> 进包理由",
        "#" + "-" * 100,
    ]
    for e in entries:
        data = e.read()
        h = hashlib.sha256(data).hexdigest()[:16]
        lines.append("%-46s\t%8d\t%s\t%-9s\t%s" % (e.arc, len(data), h, e.origin(), e.reason))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\xef\xbb\xbf" + ("\n".join(lines) + "\n").encode("utf-8"))


def write_audit_report(path: Path, version: str, src: Path, entries: list[Entry],
                       findings: list[Finding], warnings: list[str],
                       isolated: list[str], result: str) -> None:
    lines = [
        "# 打包审计报告 —— study_agent %s" % version,
        "",
        "结论：**%s**" % result,
        "源目录：%s" % src,
        "audit-only/打包时间：%s" % time.strftime("%Y-%m-%d %H:%M:%S"),
        "待打包条目：%d" % len(entries),
        "审计发现：%d" % len(findings),
        "",
        "## 一、禁区规则（命中即中止打包，退出码 2）",
        "",
        "- 禁区目录段：%s" % ", ".join(sorted(FORBIDDEN_DIRS)),
        "- 禁区文件名：%s" % ", ".join(sorted(FORBIDDEN_NAMES)),
        "- 禁区后缀：%s" % ", ".join(FORBIDDEN_SUFFIXES),
        "- 内容密钥模式：%s" % ", ".join(c for c, _, _ in SECRET_PATTERNS),
        "",
        "## 二、审计发现",
        "",
    ]
    lines += (["- 无（全部通过）"] if not findings else ["- " + f.render() for f in findings])
    lines += ["", "## 三、源目录里已被隔离、**未**进包的敏感实体", ""]
    lines += (["- 无"] if not isolated else ["- " + s for s in isolated])
    lines += ["", "## 四、警告（不阻断）", ""]
    lines += (["- 无"] if not warnings else ["- " + w for w in warnings])
    lines += ["", "## 五、包内条目", ""]
    lines += ["- %s  (%d 字节, %s)" % (e.arc, len(e.read()), e.origin()) for e in entries]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\xef\xbb\xbf" + ("\n".join(lines) + "\n").encode("utf-8"))


# ---------------------------------------------------------------------------
# tools 逐个判定清单
# ---------------------------------------------------------------------------

def tools_verdict(name: str) -> tuple[str, str]:
    """返回 (进包/不进包, 理由)。"""

    if name in TOOLS_RELEASE:
        return "进包", TOOLS_RELEASE[name]
    if ("tools/" + name) in GENERATED_ARCS:
        return "进包（包内重新生成）", "路径硬编码作者本机，包内以 %~dp0 相对版生成"
    for pat, kind, reason in TOOLS_EXCLUDE_RULES:
        if kind == "精确" and name == pat:
            return "不进包", reason
        if kind == "前缀" and name.startswith(pat):
            return "不进包", reason
        if kind == "后缀" and name.endswith(pat):
            return "不进包", reason
    return "不进包", "未列入发布白名单（开发/运维侧脚本，空库用户用不到）"


def write_tools_manifest(tools_dir: Path, path: Path, src: Path) -> tuple[int, int]:
    files = sorted(p for p in tools_dir.iterdir() if p.is_file())
    keep = 0
    lines = [
        "# tools/ 脚本取舍清单（卡 C-0067）",
        "",
        "生成时间：%s" % time.strftime("%Y-%m-%d %H:%M:%S"),
        "目录：%s（共 %d 个文件）" % (tools_dir, len(files)),
        "",
        "判定口径（优先级从高到低）：",
        "",
        "1. **R1 运行必需**：core/ 或看板在运行时直接调用/依赖 → 必进。",
        "2. **R2 安装必需**：把空程序变成可运行环境（下载模型）→ 必进。",
        "3. **R3 面向用户的 CLI**：空库用户用得上、不依赖开发夹具 → 进。",
        "4. **R4 开发/验收/探针/一次性诊断**（verify_/probe_/diag_/check_/bench_/",
        "   measure_/pilot_/compare_/dump_/show_/trace_/_c00xx_/test_/smoke_test）→ 不进。",
        "5. **R5 本机专用/内部管理**（steward_*/author_mark/硬编码本机路径的启动器）→ 不进。",
        "",
        "## 汇总",
        "",
        "| 判定 | 个数 |",
        "|---|---|",
    ]
    rows: list[tuple[str, str, str, str]] = []
    for p in files:
        verdict, reason = tools_verdict(p.name)
        if verdict.startswith("进包"):
            keep += 1
        rows.append((p.name, verdict, reason, "%d" % p.stat().st_size))
    lines.append("| 进包 | %d |" % keep)
    lines.append("| 不进包 | %d |" % (len(files) - keep))
    lines.append("| 合计 | %d |" % len(files))
    lines += ["", "## 逐文件判定", "",
              "| 文件 | 字节 | 判定 | 理由 |", "|---|---|---|---|"]
    for name, verdict, reason, size in rows:
        lines.append("| `%s` | %s | %s | %s |" % (name, size, verdict, reason))
    lines += ["", "## 已知不一致（供后续卡决策）", "",
              "- 包内 `README.md` 的「命令一览」仍提到 `tools/smoke_test.py` 与",
              "  `tools/bench_embed.py`：这两个被判为开发/验收工具，未进包，",
              "  因此分发包里这两处引用会指向不存在的文件。",
              "- 提示词真源 `logs/knowledge/提示词-*.md` 属 `logs/` 禁区，按红线未进包；",
              "  但 `core/kp_build.py::load_prompts()` 明确「本模块不做任何内置默认提示词」，",
              "  即分发包里「AI 知识点总结 / 入库结构分析」会因找不到真源而失败。",
              "  这需要后续卡决策（把真源迁到非 logs 路径并改代码，或给出显式例外）。"]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return keep, len(files)


# ---------------------------------------------------------------------------
# 三个动作
# ---------------------------------------------------------------------------

def do_pack(src: Path, out_dir: Path, version: str, tools_manifest: Path | None,
            audit_only: bool) -> int:
    top = "%s-%s" % (PKG_PREFIX, version)
    out_zip = out_dir / ("%s-windows.zip" % top)
    manifest = out_dir / ("%s-files.txt" % top)
    report = out_dir / ("%s-audit.txt" % top)

    entries, warnings = collect(src)
    isolated = scan_source_isolation(src)
    findings = audit_entries(entries)

    if findings:
        print("RELEASE PACK: ABORTED - AUDIT FAILED")
        print("findings: %d" % len(findings))
        print("source: %s" % src)
        print("artifact: 未产出（审计未通过，旧产物保持原样）")
        print("-" * 72)
        for f in findings[:40]:
            print(f.render())
        if len(findings) > 40:
            print("... 其余 %d 条见审计报告" % (len(findings) - 40))
        write_audit_report(report, version, src, entries, findings, warnings,
                           isolated, "FAIL —— 已中止打包")
        print("audit report: %s" % report)
        return 2

    if audit_only:
        print("RELEASE AUDIT: PASS")
        print("entries: %d" % len(entries))
        print("findings: 0")
        print("source: %s" % src)
        print("isolated: %d 个敏感实体已排除" % len(isolated))
        print("-" * 72)
        for s in isolated:
            print("  [isolated] %s" % s)
        print("audit report: %s" % report)
        write_audit_report(report, version, src, entries, findings, warnings,
                           isolated, "PASS（仅审计，未打包）")
        if tools_manifest:
            k, n = write_tools_manifest(src / "tools", tools_manifest, src)
            print("tools manifest: %s (%d/%d 进包)" % (tools_manifest, k, n))
        return 0

    count, total = write_zip(entries, out_zip, top)

    # 写完立即回读复检：产物必须与审计结论一致
    re_findings, re_count, re_total = audit_zip(out_zip)
    if re_findings or re_count != count:
        try:
            out_zip.unlink()
        except OSError:
            pass
        print("RELEASE PACK: ABORTED - POST-WRITE VERIFY FAILED")
        print("findings: %d" % len(re_findings))
        print("artifact: 已删除（回读复检不通过）")
        print("-" * 72)
        for f in re_findings[:40]:
            print(f.render())
        return 2

    write_manifest(entries, manifest, top, version, src)
    write_audit_report(report, version, src, entries, findings, warnings,
                       isolated, "PASS —— 已产出 %s" % out_zip.name)

    print("RELEASE PACK: OK")
    print("artifact: %s" % out_zip)
    zip_bytes = out_zip.stat().st_size
    print("size: %d bytes (%.2f MB, zip 文件)" % (zip_bytes, zip_bytes / 1048576))
    print("content: %d bytes (%.2f MB, 解压后)" % (total, total / 1048576))
    print("files: %d" % count)
    print("audit: PASS (0 findings, %d 个敏感实体已隔离)" % len(isolated))
    print("-" * 72)
    print("manifest: %s" % manifest)
    print("audit report: %s" % report)
    print("已隔离（未进包）：")
    for s in isolated:
        print("  [isolated] %s" % s)
    for w in warnings:
        print("  [warn] %s" % w)
    if tools_manifest:
        k, n = write_tools_manifest(src / "tools", tools_manifest, src)
        print("tools manifest: %s (%d/%d 进包)" % (tools_manifest, k, n))
    print("-" * 72)
    print("复核命令：%s -X utf8 %s --verify-package \"%s\""
          % (sys.executable, Path(__file__).resolve(), out_zip))
    return 0


def do_verify(zip_path: Path) -> int:
    if not zip_path.is_file():
        print("PACKAGE VERIFY: FAILED")
        print("原因：文件不存在 %s" % zip_path)
        return 3
    findings, count, total = audit_zip(zip_path)
    if findings:
        print("PACKAGE VERIFY: FAILED")
        print("findings: %d" % len(findings))
        print("files: %d" % count)
        print("-" * 72)
        for f in findings[:40]:
            print(f.render())
        return 2
    print("PACKAGE VERIFY: OK")
    print("artifact: %s" % zip_path)
    print("files: %d" % count)
    print("content: %d bytes (%.2f MB, 解压后)" % (total, total / 1048576))
    print("findings: 0（无禁区路径、无真实密钥）")
    print("-" * 72)
    with zipfile.ZipFile(zip_path) as zf:
        tops = sorted({n.split("/", 1)[0] for n in zf.namelist()})
        print("顶层目录: %s" % ", ".join(tops))
    return 0


# ---------------------------------------------------------------------------
# 审计门禁自检（含反面验证）
# ---------------------------------------------------------------------------

SELFTEST_FAKE_KEY = 'DEEPSEEK_API_KEY = "sk-selftest0123456789abcdefghijklmn"\n'


def _make_sandbox_src(root: Path) -> Path:
    """造一个最小可打包项目 + 一堆敏感实体（真实项目同构）。"""

    src = root / "src"
    (src / "core").mkdir(parents=True)
    (src / "tools").mkdir(parents=True)

    (src / "README.md").write_text("# selftest project\n", encoding="utf-8")
    (src / "requirements.txt").write_text("numpy==1.0\n", encoding="utf-8")
    (src / "NOTICE.txt").write_text("selftest notice\n", encoding="utf-8")
    (src / ".gitignore").write_text(".env\n", encoding="utf-8")
    (src / ".env.example").write_text("DEEPSEEK_API_KEY=sk-REPLACE_ME\n", encoding="utf-8")
    (src / "core" / "__init__.py").write_text("", encoding="utf-8")
    (src / "core" / "config.py").write_text("ROOT = 1\n", encoding="utf-8")
    for name in TOOLS_RELEASE:
        if ("tools/" + name) in GENERATED_ARCS:
            continue
        (src / "tools" / name).write_text("", encoding="utf-8")

    # 源根上的敏感实体：应被白名单隔离（不进包），但不阻断打包
    (src / ".env").write_text(
        'DEEPSEEK_API_KEY=sk-realselftest0123456789abcdef\n', encoding="utf-8")
    (src / "config.local.json").write_text('{"key":"sk-selftest0123456789abcdef"}\n',
                                           encoding="utf-8")
    (src / "subjects" / "x" / "index").mkdir(parents=True)
    (src / "subjects" / "x" / "index" / "study.db").write_bytes(
        b"SQLite format 3\x00" + b"\x00" * 512)
    (src / "index").mkdir()
    (src / "index" / "study.db-wal").write_bytes(b"\x00" * 64)
    (src / "raw").mkdir()
    (src / "raw" / "a.pdf").write_bytes(b"%PDF-1.4\n")
    (src / "logs").mkdir()
    (src / "logs" / "run.log").write_text("ok\n", encoding="utf-8")
    (src / "models").mkdir()
    (src / "models" / "m.onnx").write_bytes(b"\x00\x01")
    (src / "venv" / "Scripts").mkdir(parents=True)
    (src / "venv" / "Scripts" / "python.exe").write_bytes(b"MZ")
    (src / "cards" / "C-0001").mkdir(parents=True)
    (src / "cards" / "C-0001" / "note.md").write_text("card\n", encoding="utf-8")
    (src / ".git").mkdir()
    (src / ".git" / "config").write_text("[core]\n", encoding="utf-8")
    return src


def _run_pack(src: Path, out: Path) -> subprocess.CompletedProcess:
    cmd = [sys.executable, "-X", "utf8", str(Path(__file__).resolve()),
           "--src", str(src), "--out", str(out)]
    return subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def do_self_test() -> int:
    root = Path(tempfile.mkdtemp(prefix="c0067_selftest_"))
    cases: list[tuple[str, bool, str]] = []
    fails: list[str] = []

    def check(name: str, ok: bool, detail: str, proc) -> None:
        cases.append((name, ok, detail))
        if not ok:
            fails.append("[%s] rc=%s\n--- stdout ---\n%s\n--- stderr ---\n%s"
                         % (name, proc.returncode, (proc.stdout or "")[-1200:],
                            (proc.stderr or "")[-600:]))

    try:
        src = _make_sandbox_src(root)
        out = root / "dist"

        # T1：源根上的 .env / config.local.json / subjects/ / index/ / logs/ /
        #     models/ / venv/ / cards/ / .git 必须被白名单隔离 → 打包仍成功，
        #     且 zip 内零禁区命中、零密钥文件。
        r = _run_pack(src, out)
        zips = list(out.glob("*.zip"))
        ok = r.returncode == 0 and len(zips) == 1
        detail = "rc=%d" % r.returncode
        if ok:
            with zipfile.ZipFile(zips[0]) as zf:
                names = zf.namelist()
                bad = [n for n in names
                       if path_findings(n.split("/", 1)[1] if "/" in n else n)]
                leaked = [n for n in names if n.split("/")[-1] in FORBIDDEN_NAMES]
                ok = not bad and not leaked
                detail = "包内 %d 个文件，禁区命中 %d，密钥文件泄漏 %d" % (
                    len(names), len(bad), len(leaked))
        check("T1 源根敏感物被隔离（打包仍成功）", ok, detail, r)

        # T2：把含假密钥的 .env 塞进 core/（即"它真的会进包"）→ 必须中止
        (src / "core" / ".env").write_text("DEEPSEEK_API_KEY=sk-x\n", encoding="utf-8")
        r = _run_pack(src, out)
        check("T2 core/.env 注入 → 中止", r.returncode == 2 and "ABORTED" in r.stdout,
              "rc=%d" % r.returncode, r)
        (src / "core" / ".env").unlink()

        # T3：把假 subjects/x/index/study.db 塞进 core/ → 必须中止
        (src / "core" / "subjects" / "x" / "index").mkdir(parents=True)
        (src / "core" / "subjects" / "x" / "index" / "study.db").write_bytes(b"\x00" * 32)
        r = _run_pack(src, out)
        check("T3 core/subjects/x/index/study.db 注入 → 中止",
              r.returncode == 2 and "FORBIDDEN_PATH" in r.stdout,
              "rc=%d" % r.returncode, r)
        shutil.rmtree(src / "core" / "subjects")

        # T4：往白名单文件里写假密钥 → 必须中止（内容审计，不是只看路径）
        (src / "core" / "config.py").write_text(SELFTEST_FAKE_KEY, encoding="utf-8")
        r = _run_pack(src, out)
        check("T4 core/config.py 含假密钥 → 中止",
              r.returncode == 2 and "SECRET_sk_key" in r.stdout,
              "rc=%d" % r.returncode, r)
        (src / "core" / "config.py").write_text("ROOT = 1\n", encoding="utf-8")

        # T5：清理后重新打包必须成功，且产物经独立复检通过（幂等）
        r = _run_pack(src, out)
        zips = list(out.glob("*.zip"))
        ok = r.returncode == 0 and "RELEASE PACK: OK" in r.stdout and len(zips) == 1
        detail = "rc=%d" % r.returncode
        if ok:
            rv = subprocess.run(
                [sys.executable, "-X", "utf8", str(Path(__file__).resolve()),
                 "--verify-package", str(zips[0])],
                capture_output=True, text=True, encoding="utf-8", errors="replace")
            ok = rv.returncode == 0 and "PACKAGE VERIFY: OK" in rv.stdout
            detail = "rc=%d, verify rc=%d" % (r.returncode, rv.returncode)
        check("T5 清理后重新打包成功 + 产物复检通过", ok, detail, r)

        passed = sum(1 for _, ok, _ in cases if ok)
        total = len(cases)
        print("AUDIT SELF-TEST: %s %d/%d" % ("PASS" if passed == total else "FAIL", passed, total))
        for name, ok, detail in cases:
            print("%-46s %s (%s)" % (name, "OK" if ok else "FAIL", detail))
        if fails:
            print("-" * 72)
            for blob in fails:
                print(blob)
        return 0 if passed == total else 2
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="生成不含用户数据与密钥的干净分发包（白名单 + 审计门禁）")
    ap.add_argument("--src", default=None, help="源目录（默认：本脚本所在项目的根）")
    ap.add_argument("--out", default=None, help="产物目录（默认：<src>/dist）")
    ap.add_argument("--version", default=DEFAULT_VERSION, help="版本号（默认 %s）" % DEFAULT_VERSION)
    ap.add_argument("--audit-only", action="store_true", help="只审计，不产出 zip")
    ap.add_argument("--verify-package", default=None, metavar="ZIP", help="对已产出的 zip 独立复检")
    ap.add_argument("--self-test", action="store_true", help="审计门禁自检（沙盒 + 反面验证）")
    ap.add_argument("--tools-manifest", default=None, metavar="PATH",
                    help="把 tools 逐个判定清单写到该路径")
    args = ap.parse_args(argv)

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

    if args.self_test:
        return do_self_test()
    if args.verify_package:
        return do_verify(Path(args.verify_package).resolve())

    root = Path(args.src).resolve() if args.src else Path(__file__).resolve().parent.parent
    out_dir = Path(args.out).resolve() if args.out else root / "dist"
    tm = Path(args.tools_manifest).resolve() if args.tools_manifest else None

    try:
        return do_pack(root, out_dir, args.version, tm, args.audit_only)
    except PackError as exc:
        print("RELEASE PACK: ABORTED - PRECONDITION FAILED")
        print("原因：%s" % exc)
        print("source: %s" % root)
        return 3


if __name__ == "__main__":
    sys.exit(main())
