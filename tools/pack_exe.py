# Copyright (c) 2026 潜声海豚 <2227663973@qq.com>. All rights reserved.
# 本源码为著作权人私有作品（proprietary）：仅供经书面授权的测试使用；
# 未经著作权人许可，不得复制、分发、公开、发布或用于任何商业用途。
# Unauthorized copying, distribution or publication is strictly prohibited.
# File-ID: SA-26-c0073pe   (project study_agent; keep this line)
"""卡 C-0073：把学习看板打包成一个可分发的 Windows 程序（onedir）。

## 这个脚本做什么

1. **备料**（`stage_assets`）：把"要随包走的只读资源"挑到一个干净的暂存目录里。
   挑，不是整个目录拷——`logs/knowledge/` 下混着用户的产物 json 和内部
   说明 md，那些**一个字节都不能进包**。
2. **审计门禁**（`audit_*`）：打包前后各扫一遍，扫到密钥特征或用户数据目录
   就**中止**，绝不产出可疑的包。
3. **构建**（`build`）：调 PyInstaller 出 `--onedir` 产物。
4. **打包后审计**：扫 dist，确认没有 `.py` 明文、没有 `.env` /
   `config.local.json` / `*.db` / `subjects/` / `index/` / `raw/`。

## 为什么是 onedir 而不是 onefile

- 体积（含 OCR 与向量模型）在 300MB 量级，`--onefile` **每次启动**都要把
  整包解压到 `%TEMP%\\_MEIxxxx`，首启动实测要一两分钟，且临时目录里会散落
  可被翻出来的 `.pyc`；
- `--onedir` 启动快，纯 Python 模块仍以 `.pyc` 编进 exe 内的 PYZ 归档
  （**不会**在产物目录里留 `.py`），源码暴露面并不比 onefile 大。
  内部文件集中在 `_internal/`，比 onefile 的"临时目录摊一地被看到"更收敛。

## 用法

    venv\\Scripts\\python.exe tools\\pack_exe.py            # 完整包
    venv\\Scripts\\python.exe tools\\pack_exe.py --minimal   # 预检包（不带模型）
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
WORK = ROOT / "_tmp_c0073"
STAGE = WORK / "stage"
# 交付位置刻意**不放在 `_tmp_` 下**：那是构建过程的临时目录，产物要有个
# 明确落点，验收脚本与被分享的人才知道去哪里拿。
DIST = ROOT / "release" / "C-0073"
BUILD = WORK / "build"
SPEC = WORK / "spec"
PYLIBS = WORK / "pylib"
LOGDIR = WORK / "logs"

APP_NAME = "StudyDashboard"

# 随包的 .ps1：`core/extract.py` 与 `core/figures.py` 按
# `Path(__file__).parent.parent/tools/xxx.ps1` 找它们，打包后这个路径正是
# `_internal/tools/`，所以放进 `tools/` 即可，那两处代码一个字都不用改。
PS1_FILES = ("convert_legacy.ps1", "render_pptx_slides.ps1")

# 提示词真源（**管线资产**，不是用户数据）。卡 C-0073 补料：必须**全部 5 个**
# 都随包走——`core/kp_build.py` 没有内置默认提示词，缺了它们
# 「AI 知识点总结 / 结构分析」这条核心管线直接不可用。
#
# ⚠ 同目录下的 `*-末级知识点总结.json` / `*-名词解释.json` 是**用户产物**，
#   一个都不带。这里**精确到文件**，绝不用"整个 logs/ 排除"这种目录级红线——
#   那正是 C-0067 把产品资源一起误伤的根因。
PROMPT_FILES = (
    "提示词-知识点总结.md",
    "提示词-知识点总结-v2.md",
    "提示词-思维导图结构.md",
    "提示词-名词解释.md",
    "README.md",
)

# 面向**外部用户**的说明文档（卡 C-0074 产出）。
#
# ⚠ 为什么必须写进这张清单（大管家 2026-10-09 实测的坑）：
#   打包时 `stage_assets` 会**清空**暂存目录、`main` 也会**删掉旧的产物目录**
#   ⇒ **不在这份清单里的文件，重打包时会被直接清掉**。C-0074 交付的这份文档
#   放在 `release/C-0073/` 下，第一次重打包就被删了。
#   写进清单后它会进 `_internal/`，并在第 [4/4] 步被复制到 **exe 同级**，
#   这样：① 不会被重打包清掉；② 用户双击 exe 就能在同级目录看到说明书。
USER_DOC = "使用说明.md"

# 许可与第三方声明（卡 C-0078 补）。
#
# ⚠ 为什么必须补：C-0077 交付后实测发布目录只有 `StudyDashboard.exe`、
#   `_internal/`、`使用说明.md` —— **一份许可都没有**。把专有软件连同几十个
#   开源组件（onnxruntime / PyMuPDF / tokenizers / OpenCV / rapidocr …）
#   打包再分发，却不带 `LICENSE` 与第三方声明，是**合规硬伤**，发布前必须补齐。
#
# 手法与 `USER_DOC` 完全一致：进 `_internal/` ⇒ 重打包不会被清掉；
# 第 [4/4] 步再复制到 **exe 同级**，用户解压后一眼就能看到。
#
# ★ 这几份是**法律文本，一个字节都不许改**，所以走原样复制（不做脱敏改写，
#   与提示词那批走 `_copy_sanitized` 不同）；它们仍要过一遍 `audit_tree`
#   的密钥/内部痕迹扫描 —— 真扫出东西说明源文件本身有问题，那时应当拦下。
# ★ 缺失的文件**跳过并逐条写进日志，绝不报错中断**：某份声明暂时没有，
#   不该让整个包打不出来，但也绝不允许静默消失。
LEGAL_DOCS = (
    "LICENSE",                    # 本项目专有许可
    "NOTICE.txt",                 # 版权与免责声明
    "THIRD-PARTY-NOTICES.md",     # 第三方组件声明
    "THIRD-PARTY-LICENSES.txt",   # 第三方许可全文
)
# 全部"要随包并落到 exe 同级"的文档：先是使用说明，再是许可与声明。
PACK_DOCS = (USER_DOC,) + LEGAL_DOCS

MODEL_DIRNAME = "bge-small-zh-v1.5"
# 只带默认配置真正加载的那一份量化模型（22.9MB）。
# `onnx/model.onnx` 是同一个模型的 fp32 版（90MB），`EMBED_ONNX_FILE` 的默认值
# 指向前者，带它只是白白让体积多 90MB——想用 fp32 的人可以自己在 exe 同级放一份。
MODEL_KEEP = (
    "config.json",
    "special_tokens_map.json",
    "tokenizer.json",
    "tokenizer_config.json",
    "vocab.txt",
    "onnx/model_quantized.onnx",
)

CORE_MODULES = (
    "chat", "chunk", "config", "db", "embed", "extract", "figures", "ingest",
    "kp_batch", "kp_build", "layout", "multippt", "outline", "pipeline_pool",
    "progress", "search", "study", "subjects", "tables",
    # 卡 C-0086 补：`share`（课件分享包，C-0079/80/81 落地）此前只靠 modulegraph
    # 从 `tools/dashboard.py` 的 `from core import share` 自动收集——能收得到，
    # 但「能收得到」是静态分析的结果、不是承诺。这里显式点名，与其余 19 个
    # core 模块一个待遇：**发布包里少了它，「分享」按钮一点就报 ImportError**，
    # 而这种缺失在源码模式永远复现不出来。
    "share",
)

THIRD_PARTY = (
    "onnxruntime", "tokenizers", "rapidocr_onnxruntime", "cv2", "shapely",
    "pyclipper", "numpy", "PIL", "fitz", "lxml", "docx", "pptx", "xlsxwriter",
    "yaml",
)

# ---------------------------------------------------------------- 审计门禁
#
# 这两组规则就是"红线"的可执行化：**用户的真实数据与密钥一个字节都不进包**。
_FORBIDDEN_NAMES = {
    ".env", ".env.local", "config.local.json", "config.local.json.tmp",
    "credentials.yaml", ".credentials.yaml",
}
_FORBIDDEN_SUFFIX = (".db", ".db-wal", ".db-shm", ".sqlite", ".sqlite3", ".env")
_FORBIDDEN_DIRS = {"subjects", "index", "raw", "cards", ".git", "venv",
                   "__pycache__", "backup"}

_SECRET_PATTERNS = (
    ("OpenAI 风格密钥 sk-…",
     re.compile(rb"sk-[A-Za-z0-9_\-]{20,}")),
    ("赋值形式的密钥（api_key/secret/token = 长串）",
     re.compile(rb"(?i)(api[_-]?key|secret[_-]?key|access[_-]?token)"
                rb"\s*[:=]\s*[\"']?[A-Za-z0-9_\-]{24,}")),
    ("DeepSeek/智谱 真实密钥形状",
     re.compile(rb"\b[0-9a-f]{32}\.[0-9A-Za-z_\-]{16,}\b")),
)

# 只对**我们能控制的文本资源**扫这一条：本机绝对路径（含开发者的 Windows 用户名）
# 与内部运维痕迹，不该随包发给外人。
# ⚠ 刻意**不**扫 .exe/.pyc/二进制：Python 打包会把构建机的源文件路径写进 pyc 的
#   `co_filename` 元数据，那是这门技术的固有现象、去不掉，也不构成源码外流。
# ⚠ 规则刻意收窄：产物里还有第三方库自带的文本配置，拿"任意盘符路径"去卡它们
#   会制造一堆假违规。宽进（脱敏）严出（审计）——见 `_SANITIZE_PATTERNS`。
_PATH_LEAK_PATTERNS = (
    ("本机用户目录绝对路径",
     re.compile(rb"[A-Za-z]:[\\/]+Users[\\/]+[^\\/\s\"'`]+")),
    ("内部运维痕迹（卡目录 / 大管家会话）",
     re.compile(rb"steward-lite|\.dsh[\\/]+consoles|cards[\\/]+C-\d+")),
)

# 只用于**备料脱敏**：从盘符一路吃到行尾分隔符，把整条本机路径连内部目录一起换掉。
# 第一版只换 `C:\Users\<用户名>`，结果同一条路径被两条规则各命中一次，
# 换完剩下 `<本机路径>\.dsh\<本机路径>\consoles\session-…\cards\C-0027\…`——
# 会话 UUID、卡号、目录结构全都还在，等于没脱敏（实测踩过）。
_SANITIZE_PATTERNS = (
    re.compile(rb"[A-Za-z]:[\\/][^\s`'\"<>|\r\n]*"),
    re.compile(rb"steward-lite|\.dsh[\\/]+consoles|cards[\\/]+C-\d+"),
)

_TEXT_SUFFIX = (".md", ".txt", ".json", ".yaml", ".yml", ".cfg", ".ini", ".env",
                ".cmd", ".ps1")


class AuditError(SystemExit):
    def __init__(self, msg: str) -> None:
        super().__init__("★ 审计门禁拦下：%s" % msg)


def _rel(p: Path, base: Path) -> str:
    try:
        return p.relative_to(base).as_posix()
    except ValueError:
        return str(p)


def scan_secrets(path: Path) -> list[str]:
    """在一个文件里找密钥特征；返回命中的规则名列表。"""
    try:
        blob = path.read_bytes()
    except OSError:
        return []
    hits = []
    for name, pat in _SECRET_PATTERNS:
        m = pat.search(blob)
        if m:
            hits.append("%s（命中：%s…）" % (name, m.group(0)[:24].decode(
                "utf-8", "replace")))
    return hits


def scan_text_leaks(path: Path) -> list[str]:
    """只看**我们能控制的文本资源**里的内部痕迹（本机绝对路径 / 卡目录）。"""
    try:
        blob = path.read_bytes()
    except OSError:
        return []
    hits = []
    for name, pat in _PATH_LEAK_PATTERNS:
        m = pat.search(blob)
        if m:
            hits.append("%s（命中：%s…）" % (name, m.group(0)[:40].decode(
                "utf-8", "replace")))
    return hits


def _is_our_source(rel: str) -> bool:
    """这个 .py 是不是**本项目**的源码？

    产物里出现第三方库的 `.py`（`cv2/config-3.py`、`onnxruntime/**`）不算源码外流——
    那是开源库自己的源码，本来就公开；而且 `cv2/__init__.py` 运行时要
    `exec` 自己的 `config-3.py`，删不得。本项目自己的模块只会出现在
    `_internal/core/` 或 `_internal/tools/` 下，只有那种才拦。
    """
    parts = rel.split("/")
    if parts and parts[0] == "_internal":
        parts = parts[1:]
    return len(parts) >= 2 and parts[0] in ("core", "tools")


def audit_tree(base: Path, *, what: str) -> list[str]:
    """扫一棵树：禁名 / 禁目录 / 禁后缀 / 本项目源码明文 / 密钥 / 内部路径。"""
    problems: list[str] = []
    if not base.exists():
        return ["%s 不存在：%s" % (what, base)]
    for p in sorted(base.rglob("*")):
        rel = _rel(p, base)
        low = p.name.lower()
        if p.is_dir():
            if low in _FORBIDDEN_DIRS:
                problems.append("[禁目录] %s/%s" % (what, rel))
            continue
        if low in _FORBIDDEN_NAMES:
            problems.append("[禁文件] %s/%s" % (what, rel))
            continue
        if low.endswith(_FORBIDDEN_SUFFIX):
            problems.append("[禁后缀] %s/%s" % (what, rel))
            continue
        # **本项目**的 .py 明文：交付物里出现它就是源码外流，一律拦下。
        # （.pyc 不拦——它是编译产物、不是源码明文，统计见 main 里的清单。）
        if (low.endswith(".py") or low.endswith(".pyw")) and _is_our_source(rel):
            problems.append("[源码明文] %s/%s" % (what, rel))
            continue
        # 内容扫描：文本/脚本/可执行一律扫；其余二进制只在 ≤2MB 时扫
        # （几百 MB 的 dll/onnx 不会有密钥，逐个读进来只会白等）
        try:
            size = p.stat().st_size
        except OSError:
            continue
        is_text = low.endswith(_TEXT_SUFFIX)
        if is_text or low.endswith(".exe") or size <= 2 * 1024 * 1024:
            for hit in scan_secrets(p):
                problems.append("[密钥特征] %s/%s → %s" % (what, rel, hit))
        if is_text:
            for hit in scan_text_leaks(p):
                problems.append("[内部痕迹] %s/%s → %s" % (what, rel, hit))
    return problems


def audit_payload(paths: list[Path], *, what: str) -> list[str]:
    """扫"将要进包的具名文件"（比扫树更严：逐个点名）。"""
    problems: list[str] = []
    for p in paths:
        if not p.exists():
            problems.append("[缺文件] %s/%s" % (what, p))
            continue
        low = p.name.lower()
        if low in _FORBIDDEN_NAMES or low.endswith(_FORBIDDEN_SUFFIX):
            problems.append("[禁文件] %s/%s" % (what, p.name))
        for hit in scan_secrets(p):
            problems.append("[密钥特征] %s/%s → %s" % (what, p.name, hit))
        for hit in scan_text_leaks(p):
            problems.append("[内部痕迹] %s/%s → %s" % (what, p.name, hit))
    return problems


def _copy_sanitized(src: Path, dst: Path) -> int:
    """复制一份文本资源进暂存区，顺手抹掉**本机绝对路径**这类内部痕迹。

    ⚠ 只动"溯源信息"里的路径，资源正文一个字符都不改；**源文件一个字节都不动**
      （改的只是暂存区里的副本）。返回替换处数，0 表示原样。
    """
    try:
        blob = src.read_bytes()
    except OSError:
        shutil.copy2(src, dst)
        return 0
    n = 0
    for pat in _SANITIZE_PATTERNS:
        blob, k = pat.subn("<本机路径>".encode("utf-8"), blob)
        n += k
    dst.write_bytes(blob)
    return n


# ---------------------------------------------------------------- 备料
def stage_assets(*, minimal: bool) -> tuple[list[str], list[str]]:
    """把要随包的只读资源挑进干净暂存目录。返回 (add_data 参数, 说明行)。"""
    if STAGE.exists():
        shutil.rmtree(STAGE)
    notes: list[str] = []
    add_data: list[str] = []

    # 1) 两个 .ps1（走 _internal/tools/，两处调用点不用改）
    tools_dst = STAGE / "tools"
    tools_dst.mkdir(parents=True, exist_ok=True)
    picked: list[Path] = []
    for name in PS1_FILES:
        src = ROOT / "tools" / name
        if not src.exists():
            raise AuditError("缺少随包脚本 %s" % src)
        shutil.copy2(src, tools_dst / name)
        picked.append(tools_dst / name)
    add_data.append("%s;tools" % tools_dst)
    notes.append("tools/*.ps1：%d 个（%s）" % (len(picked), ", ".join(PS1_FILES)))

    # 2) 看板页面资源。dashboard.py 在导入时从 core/frontend 读取并拼装首页。
    frontend_src = ROOT / "core" / "frontend"
    frontend_dst = STAGE / "core" / "frontend"
    frontend_dst.mkdir(parents=True, exist_ok=True)
    manifest = frontend_src / "manifest.json"
    if not manifest.is_file():
        raise AuditError("缺少看板页面清单：%s" % manifest)
    scripts = json.loads(manifest.read_text(encoding="utf-8"))["scripts"]
    for name in ("index.html", "style.css", "manifest.json", *scripts):
        src = frontend_src / name
        if not src.is_file():
            raise AuditError("缺少看板页面资源：%s" % src)
        dst = frontend_dst / name
        shutil.copy2(src, dst)
        picked.append(dst)
    add_data.append("%s;core/frontend" % frontend_dst)
    notes.append("core/frontend：HTML、CSS 与 %d 份 JavaScript 资源" % len(scripts))

    # 3) 提示词真源：5 个文件全带（`core/kp_build.py` 没有内置默认提示词，
    #    缺一份就有一条管线不可用）。走 `_internal/logs/knowledge/`，
    #    首次运行时种到 exe 同级；运行时的解析顺序见 README 的交付说明。
    k_dst = STAGE / "logs" / "knowledge"
    k_dst.mkdir(parents=True, exist_ok=True)
    kpicked: list[Path] = []
    sanitized = 0
    for name in PROMPT_FILES:
        src = ROOT / "logs" / "knowledge" / name
        if not src.exists():
            raise AuditError("提示词真源缺失：%s（缺它会让管线不可用）" % src)
        dst = k_dst / name
        sanitized += _copy_sanitized(src, dst)
        kpicked.append(dst)
        picked.append(dst)
    if kpicked:
        add_data.append("%s;logs/knowledge" % k_dst)
    notes.append("logs/knowledge/*.md：%d 个（5 个提示词真源全带；"
                 "用户产物 json 一个不带；脱敏 %d 处本机路径）"
                 % (len(kpicked), sanitized))

    # 3) 嵌入模型
    if minimal:
        notes.append("嵌入模型：**预检包不带**（--minimal）")
    else:
        m_src = ROOT / "models" / MODEL_DIRNAME
        if not m_src.is_dir():
            raise AuditError("缺少嵌入模型目录 %s" % m_src)
        m_dst = STAGE / "models" / MODEL_DIRNAME
        total = 0
        for rel in MODEL_KEEP:
            s = m_src / rel
            if not s.exists():
                raise AuditError("嵌入模型缺文件 %s" % s)
            d = m_dst / rel
            d.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(s, d)
            total += s.stat().st_size
            picked.append(d)
        add_data.append("%s;models/%s" % (m_dst, MODEL_DIRNAME))
        notes.append("嵌入模型：%s（%.1f MB，只带默认加载的量化权重）"
                     % (MODEL_DIRNAME, total / 1e6))

    # 4) 面向外部用户的《使用说明》+ 许可与第三方声明（卡 C-0074 / C-0078）
    #    放进 `_internal/` ⇒ 先保证它们**不会被重打包清掉**；
    #    再由 `main()` 第 [4/4] 步复制到 **exe 同级**，用户双击就能看到。
    #    缺失时**不报错**（--minimal 预检包、或某份声明尚未交付时仍可打包），
    #    但会在说明行里**逐条**写明，不让它静默消失。
    for name in PACK_DOCS:
        doc_src = ROOT / name
        if not doc_src.is_file():
            notes.append("%s：**缺失**（跳过，不影响打包）" % name)
            continue
        doc_dst = STAGE / name
        # 法律文本**原样复制**（不做 `_copy_sanitized` 那种脱敏改写）；
        # 它们随后仍要过 `audit_tree` 的密钥/内部痕迹扫描。
        shutil.copy2(doc_src, doc_dst)
        picked.append(doc_dst)
        add_data.append("%s;." % doc_dst)
        try:
            size = "%.0f 字符" % len(doc_src.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            size = "%.1f KB" % (doc_src.stat().st_size / 1024)
        kind = "许可/声明" if name in LEGAL_DOCS else "使用说明"
        notes.append("%s（%s）：%s（随包；打包后复制到 exe 同级）"
                     % (name, kind, size))

    # 审计暂存区：这里是"将要进包的全部只读资源"，逐字节可查
    probs = audit_tree(STAGE, what="stage") + audit_payload(picked, what="stage")
    if probs:
        for x in probs:
            print("  " + x, file=sys.stderr)
        raise AuditError("暂存区含 %d 处违规，已中止（未产出任何包）" % len(probs))
    return add_data, notes


# ---------------------------------------------------------------- 构建
def ensure_pyinstaller() -> dict:
    """PyInstaller 装到 `_tmp_c0073/pylib`，**不污染项目主 venv**。

    理由：主 venv 的包清单是别的卡（C-0068 安装脚本）在做基线比对的东西，
    这里多装一个包会让那边的 diff 失真。`--target` 装法让主 venv 一个字节不变。
    """
    env = dict(os.environ)
    try:
        import PyInstaller  # noqa: F401
        return env
    except ImportError:
        pass
    if not (PYLIBS / "PyInstaller").is_dir():
        PYLIBS.mkdir(parents=True, exist_ok=True)
        cmd = [sys.executable, "-m", "pip", "install", "--target", str(PYLIBS),
               "--no-warn-script-location", "pyinstaller"]
        print("[备料] 安装 PyInstaller 到 %s（不动主 venv）" % PYLIBS, flush=True)
        r = subprocess.run(cmd, text=True, encoding="utf-8", errors="replace")
        if r.returncode != 0:
            raise SystemExit("PyInstaller 安装失败（退出码 %d）" % r.returncode)
    env["PYTHONPATH"] = str(PYLIBS) + os.pathsep + env.get("PYTHONPATH", "")
    return env


def build(*, add_data: list[str], minimal: bool, log_path: Path,
          dist: Path) -> None:
    for d in (dist, BUILD, SPEC, LOGDIR):
        d.mkdir(parents=True, exist_ok=True)
    env = ensure_pyinstaller()

    entry = ROOT / "tools" / "dashboard.py"
    cmd: list[str] = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--onedir",                       # 见文件头"为什么是 onedir"
        "--console",
        "--name", APP_NAME,
        "--distpath", str(dist),
        "--workpath", str(BUILD),
        "--specpath", str(SPEC),
        "--paths", str(ROOT),             # 让 modulegraph 找得到 core 包
        "--log-level", "INFO",
    ]
    for spec in add_data:
        cmd += ["--add-data", spec]
    # 运行时自带的原生库/模型，显式收全（这两处是已知易漏 DLL/模型的包）
    cmd += ["--collect-all", "onnxruntime",
            "--collect-all", "rapidocr_onnxruntime",
            "--collect-binaries", "tokenizers",
            "--collect-binaries", "cv2"]
    for m in CORE_MODULES:
        cmd += ["--hidden-import", "core." + m]
    for m in THIRD_PARTY:
        cmd += ["--hidden-import", m]
    # 用不到的大件，排掉省体积
    for m in ("tkinter", "matplotlib", "pandas", "scipy", "pytest",
              "IPython", "notebook", "torch", "transformers", "sentence_transformers"):
        cmd += ["--exclude-module", m]
    cmd.append(str(entry))

    print("[构建] PyInstaller %s" % ("（预检包）" if minimal else "（完整包）"), flush=True)
    t0 = time.time()
    with log_path.open("w", encoding="utf-8", errors="replace") as fh:
        fh.write(" ".join(cmd) + "\n\n")
        fh.flush()
        proc = subprocess.run(cmd, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, env=env)
        out = proc.stdout.decode("utf-8", "replace")
        fh.write(out)
    tail = [l for l in out.splitlines() if l.strip()][-12:]
    print("\n".join(tail), flush=True)
    if proc.returncode != 0:
        raise SystemExit("PyInstaller 失败（退出码 %d），完整输出见 %s"
                         % (proc.returncode, log_path))
    print("[构建] 完成，用时 %.1f 秒；日志 %s" % (time.time() - t0, log_path),
          flush=True)


def measure(base: Path) -> tuple[int, int, float]:
    n = 0
    size = 0
    for p in base.rglob("*"):
        if p.is_file():
            n += 1
            size += p.stat().st_size
    return n, size, size / 1e6


# ---------------------------------------------------------------- 主流程
def main() -> int:
    ap = argparse.ArgumentParser(description="把学习看板打包成 Windows 程序（卡 C-0073）")
    ap.add_argument("--minimal", action="store_true",
                    help="预检包：不带嵌入模型（只验证能启动与路径解析）")
    ap.add_argument("--dist", default=str(DIST))
    ap.add_argument("--keep-dist", action="store_true",
                    help="保留上一次的 dist（默认先删干净，避免旧文件混进交付物）")
    args = ap.parse_args()

    dist = Path(args.dist)
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    log_path = LOGDIR / ("build-%s%s.log" % (stamp, "-minimal" if args.minimal else ""))

    print("=" * 68)
    print("卡 C-0073 打包：%s" % ("预检包（--minimal）" if args.minimal else "完整包"))
    print("工作目录 %s" % WORK)
    print("=" * 68)

    print("\n[1/4] 备料")
    add_data, notes = stage_assets(minimal=args.minimal)
    for n in notes:
        print("  · " + n)

    if not args.keep_dist and (dist / APP_NAME).exists():
        shutil.rmtree(dist / APP_NAME)
    print("\n[2/4] 构建")
    build(add_data=add_data, minimal=args.minimal, log_path=log_path, dist=dist)

    print("\n[3/4] 打包后审计")
    app_dir = dist / APP_NAME
    probs = audit_tree(app_dir, what="dist")
    if probs:
        for x in probs[:40]:
            print("  " + x, file=sys.stderr)
        shutil.rmtree(app_dir, ignore_errors=True)
        raise AuditError("产物含 %d 处违规，已删除 %s" % (len(probs), app_dir))
    print("  · 无禁文件 / 无 .py 明文 / 无密钥特征：通过")
    pycs = [p for p in app_dir.rglob("*.pyc")]
    zips = [p for p in app_dir.rglob("*.zip")]
    print("  · 编译产物：%d 个独立 .pyc、%d 个 .zip（PYZ 归档在 exe 内）"
          % (len(pycs), len(zips)))
    for p in pycs[:10]:
        print("      - %s" % p.relative_to(app_dir).as_posix())

    print("\n[4/4] 产物")
    n, size, mb = measure(app_dir)
    exe = app_dir / (APP_NAME + ".exe")

    # 《使用说明》与许可/第三方声明复制到 exe 同级（卡 C-0074 / C-0078）：
    # 用户双击 exe 时就在旁边看到说明书与许可；放在 `_internal/` 内则要翻三层
    # 目录才找得到。放在**审计之后**——审计门禁只管"不该有的东西"，这些是合法
    # 交付物；复制完再补扫一次，确保它们自己也没带被禁内容。
    copied: list[str] = []
    missing: list[str] = []
    for name in PACK_DOCS:
        doc_src = app_dir / "_internal" / name
        if doc_src.is_file():
            shutil.copy2(doc_src, app_dir / name)
            copied.append(name)
        else:
            missing.append(name)
    if copied:
        extra = audit_tree(app_dir, what="dist")
        if extra:
            for x in extra[:20]:
                print("  " + x, file=sys.stderr)
            raise AuditError("复制使用说明/许可文件后产物出现 %d 处违规" % len(extra))
        print("  随包文档：%d 份已复制到 exe 同级（重打包不会被清掉）—— %s"
              % (len(copied), "、".join(copied)))
    if missing:
        print("  随包文档：**%d 份未随包**（源文件缺失，已跳过）—— %s"
              % (len(missing), "、".join(missing)))

    print("  目录：%s" % app_dir)
    print("  可执行：%s（存在＝%s）" % (exe, exe.exists()))
    print("  体积：%.1f MB（%d 个文件）" % (mb, n))
    print("  内部目录：%s" % ", ".join(sorted(
        p.name for p in app_dir.iterdir() if p.is_dir())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
