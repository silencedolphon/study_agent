# C-0054：一次性采集关键锚点行号 + 文件哈希（报告里的行号以此为准）
import hashlib
import re
from pathlib import Path

ROOT = Path(r"G:\study_agent")
FILES = ["core/figures.py", "core/ingest.py", "core/extract.py",
         "core/pipeline_pool.py", "tools/dashboard.py", "core/config.py",
         "tools/render_pptx_slides.ps1", "tools/convert_legacy.ps1",
         "tools/reimport_all.py", "core/chat.py", "tools/find_empty_figures.py"]

PATTERNS = [
    (r"^def render_pages\(", "figures.render_pages 定义"),
    (r"kind == \"pdf\":", "render_pages 的 pdf 分支"),
    (r"f = outdir / f\"slide_\{p:02d\}\.png\"", "PDF 页图文件名"),
    (r"# pptx：调 PowerShell COM 脚本", "pptx 分支起点"),
    (r"script = Path\(__file__\)\.resolve\(\)\.parent\.parent", "COM 脚本路径"),
    (r"^def ingest_document_figures\(", "整页识图入口"),
    (r"outdir = config\.LOG_DIR / \"figure_desc\"", "页图输出目录"),
    (r"_adopt_previous_renders\(path, outdir\)", "搬运历史渲染"),
    (r"slides = render_pages\(path, outdir, kind=kind\)", "调用渲染"),
    (r"row\[\"image_hash\"\] == ihash", "复用判据"),
    (r"^def _safe_dirname\(", "目录名清洗"),
    (r"^def clear_document_figures\(", "清识图块"),
    (r"^def image_hash\(", "sha256 定义"),
    (r"^def page_has_image\(", "按形状判有无图"),
    (r"if kind != \"pptx\":", "非 pptx 直接返回 None"),
    (r"prs = Presentation\(str\(src\)\)", "python-pptx 打开（无兜底）"),
    (r"^def ensure_schema\(", "figure_desc 建表"),
    (r"CREATE TABLE IF NOT EXISTS figure_desc", "figure_desc 表体"),
    (r"^def split_description\(", "识图描述切块"),
    (r"^def extract_pages\(", "整份骨架"),
]

ING = [
    (r"^def ingest_file\(", "ingest_file 定义"),
    (r"with_figures = bool\(getattr\(config, \"FIGURE_DESC\", 1\)\)", "识图默认开关"),
    (r"if with_figures and kind in \(\"pdf\", \"pptx\"\):", "识图触发点"),
    (r"_fig\.clear_document_figures\(conn, doc_id\)", "识图前清块"),
    (r"fs = _fig\.ingest_document_figures\(", "调用整页识图"),
    (r"fig_text = _figure_texts\(conn, doc_id\)", "取回描述正文"),
    (r"^def _figure_texts\(", "_figure_texts 定义"),
    (r"^def _should_analyze\(", "全局分析判据"),
    (r"^def iter_files\(", "文件遍历"),
]

EXT = [
    (r"^def extract_pptx\(", "extract_pptx 定义"),
    (r"LEGACY_OFFICE = ", "旧格式集合"),
    (r"^SUPPORTED_EXT = ", "支持扩展名"),
    (r"^def detect_kind\(", "detect_kind 定义"),
    (r"if ext in LEGACY_OFFICE:", "legacy 判定"),
    (r"^def extract\(path: Path\)", "extract 分发"),
    (r"if kind == \"legacy\":", "legacy 拒绝分支"),
    (r"旧版 Office 格式", "拒绝文案"),
]

DASH = [
    (r"^def _figure_desc_root\(", "_figure_desc_root"),
    (r"^def _deck_dir_for\(", "_deck_dir_for"),
    (r"for name in \(stem, stem \+ \"_slides\"\):", "目录候选"),
    (r"^def _slide_files\(", "_slide_files"),
    (r"^def _slide_info\(", "_slide_info"),
    (r"^def _slide_png_path\(", "_slide_png_path"),
    (r"    def _slide\(self", "Handler._slide"),
    (r"if path == \"/img/slide\":", "GET 路由"),
    (r"^def api_ingest\(", "api_ingest"),
    (r"with_figures=not no_figures", "api_ingest 传参"),
    (r"^def api_ingest_batch_start\(", "批量入口"),
    (r"with_figures=\(None if nf is None", "批量传参"),
    (r"^async function reindex\(rel, force, noFigures\)", "前端 reindex"),
    (r"^function mmShowSlide\(d, page\)", "mmShowSlide"),
    (r"const url = '/img/slide\?doc_id=", "前端取图 URL"),
    (r"该页没有课件图缓存", "无图提示文案"),
]

for rel, pats in (("core/figures.py", PATTERNS), ("core/ingest.py", ING),
                  ("core/extract.py", EXT), ("tools/dashboard.py", DASH)):
    p = ROOT / rel
    text = p.read_text(encoding="utf-8")
    lines = text.splitlines()
    print("=" * 78)
    print(f"## {rel}  sha256={hashlib.sha256(p.read_bytes()).hexdigest()[:16]}"
          f"  行数={len(lines)}")
    for pat, label in pats:
        hits = [i + 1 for i, ln in enumerate(lines) if re.search(pat, ln)]
        print(f"  {label:<28} -> {hits if hits else '未命中'}")

for rel in ("core/pipeline_pool.py", "core/config.py", "core/chat.py",
            "tools/reimport_all.py", "tools/find_empty_figures.py",
            "tools/render_pptx_slides.ps1", "tools/convert_legacy.ps1"):
    p = ROOT / rel
    print("=" * 78)
    print(f"## {rel}  sha256={hashlib.sha256(p.read_bytes()).hexdigest()[:16]}"
          f"  行数={len(p.read_text(encoding='utf-8', errors='replace').splitlines())}")

pp = (ROOT / "core/pipeline_pool.py").read_text(encoding="utf-8").splitlines()
for pat, label in ((r"needs_fig = bool\(with_figures", "批量预估 needs_fig"),
                   (r"if not will_skip and supported and kind in", "批量预估 outline 花钱"),
                   (r"kind == \"pptx\"", "pptx 特判")):
    hits = [i + 1 for i, ln in enumerate(pp) if re.search(pat, ln)]
    print(f"  {label:<28} -> {hits}")

cf = (ROOT / "core/config.py").read_text(encoding="utf-8").splitlines()
for pat in (r"^FIGURE_DESC = ", r"^FIGURE_RENDER_DPI = ", r"^LOG_DIR = ",
            r"^OUTLINE_ANALYSIS = ", r"^OCR_DPI = "):
    hits = [i + 1 for i, ln in enumerate(cf) if re.search(pat, ln)]
    print(f"  config {pat:<26} -> {hits}")

ch = (ROOT / "core/chat.py").read_text(encoding="utf-8").splitlines()
for pat in (r"is_slide = fk in", r"role = \"courseware\" if file_kind"):
    hits = [i + 1 for i, ln in enumerate(ch) if re.search(pat, ln)]
    print(f"  chat {pat:<28} -> {hits}")
