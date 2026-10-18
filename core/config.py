# Copyright (c) 2026 潜声海豚 <2227663973@qq.com>. All rights reserved.
# 本源码为著作权人私有作品（proprietary）：仅供经书面授权的测试使用；
# 未经著作权人许可，不得复制、分发、公开、发布或用于任何商业用途。
# Unauthorized copying, distribution or publication is strictly prohibited.
# File-ID: SA-04-f5cdd2d5   (project study_agent; keep this line)
"""集中配置：从 .env 读取，带默认值，全项目唯一配置入口。"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import threading
import time
from pathlib import Path


def _roots() -> tuple[Path, Path]:
    """算出 (可写数据根 `ROOT`, 只读资源根 `BUNDLE_ROOT`)。

    卡 C-0073（exe 路线）新增。**源码模式下行为与改动前逐字相同**：
    两个根是同一个"项目根"，一切路径取值不变。

    打包模式（PyInstaller）下必须分开，理由：
      · `__file__` 此时落在**解包目录**（`sys._MEIPASS`，一目录形态是
        `<exe目录>\\_internal`）。若照旧拿它当 `ROOT`，`index/`、`subjects/`、
        `logs/`、`notes/` 这些**可写数据**就会写进临时目录，关掉就丢。
        所以数据根改用 **exe 所在目录**。
      · 随包分发的**只读资源**（嵌入模型、两个 .ps1、提示词种子）仍在解包目录，
        由 `BUNDLE_ROOT` 给出；`tools/*.ps1` 两处调用点不需要改——
        它们算出来的正是 `BUNDLE_ROOT/tools/`（`Path(__file__).parent.parent`
        在打包后就是解包目录），所以只要打包时把 .ps1 放进 `_internal/tools/` 即可。

    `KB_HOME` 环境变量可在打包模式下把数据目录指到别处（不设＝exe 同级）。
    ⚠ 它只认**进程环境变量**，不认 `.env`——`.env` 本身就要先知道 `ROOT`
      才能找到，先有鸡还是先有蛋。
    """
    if getattr(sys, "frozen", False):
        home = os.environ.get("KB_HOME", "").strip()
        data = Path(home).resolve() if home else Path(sys.executable).resolve().parent
        return data, Path(getattr(sys, "_MEIPASS", "") or data)
    root = Path(__file__).resolve().parent.parent
    return root, root


ROOT, BUNDLE_ROOT = _roots()


def _load_env(path: Path) -> None:
    """极简 .env 解析，不引入额外依赖。已存在的真实环境变量优先。"""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip().strip('"').strip("'")
        # 不覆盖进程里已显式设置的值
        if key and key not in os.environ:
            os.environ[key] = val


_load_env(ROOT / ".env")


def _path(key: str, default: str) -> Path:
    return Path(os.environ.get(key, default)).expanduser()


def _int(key: str, default: int) -> int:
    try:
        return int(os.environ.get(key, default))
    except (TypeError, ValueError):
        return default


def _float(key: str, default: float) -> float:
    try:
        return float(os.environ.get(key, default))
    except (TypeError, ValueError):
        return default


# ---- 路径 ----
#
# 默认值一律挂在 `ROOT`（可写数据根）下；打包模式下它就是 exe 同级目录。
def _default_model_dir() -> Path:
    """嵌入模型目录的默认值（卡 C-0073）。

    源码模式：项目根 `models/bge-small-zh-v1.5`——与改动前**逐字相同**。
    打包模式：exe 同级若自备 `models/bge-small-zh-v1.5` 就优先用它
    （留给"想换模型"的人一条路），否则用随包分发的那一份（在解包目录里，
    对方下载完不用自己再拉 100MB 模型）。
    """
    own = ROOT / "models" / "bge-small-zh-v1.5"
    if getattr(sys, "frozen", False) and not own.exists():
        return BUNDLE_ROOT / "models" / "bge-small-zh-v1.5"
    return own


RAW_DIR = _path("KB_RAW", str(ROOT / "raw"))
NOTES_DIR = _path("KB_NOTES", str(ROOT / "notes"))
INDEX_DIR = _path("KB_INDEX", str(ROOT / "index"))
DB_PATH = _path("KB_DB", str(INDEX_DIR / "study.db"))
MODEL_DIR = _path("EMBED_MODEL_DIR", str(_default_model_dir()))
LOG_DIR = _path("KB_LOG", str(ROOT / "logs"))

# ---- 嵌入模型 ----
MODELSCOPE_ENDPOINT = os.environ.get("MODELSCOPE_ENDPOINT", "https://www.modelscope.cn")
EMBED_MODEL_REPO = os.environ.get("EMBED_MODEL_REPO", "Xenova/bge-small-zh-v1.5")
EMBED_ONNX_FILE = os.environ.get("EMBED_ONNX_FILE", "onnx/model_quantized.onnx")
EMBED_DIM = _int("EMBED_DIM", 512)
EMBED_MAX_TOKENS = _int("EMBED_MAX_TOKENS", 512)

# BGE 系列检索指令前缀：查询侧加，文档侧不加。缺失会明显拉低召回质量。
QUERY_PREFIX = os.environ.get("EMBED_QUERY_PREFIX", "为这个句子生成表示以用于检索相关文章：")

# ---- 切块 ----
CHUNK_TARGET_CHARS = _int("CHUNK_TARGET_CHARS", 700)
CHUNK_MAX_CHARS = _int("CHUNK_MAX_CHARS", 1100)
CHUNK_MIN_CHARS = _int("CHUNK_MIN_CHARS", 80)
CHUNK_OVERLAP_CHARS = _int("CHUNK_OVERLAP_CHARS", 120)

# ---- OCR ----
# 扫描件 OCR 的渲染分辨率。
# 实测（452 页扫描版药理学教材，单页）：100dpi 16s、150dpi 20s、200dpi 22s、300dpi 28s，
# 而中文术语识别率在各档位没有差别（都是 6/6 命中）。
# 全书 200dpi 约 2.8 小时、100dpi 约 2 小时，因此默认取 100。
# 提高它会线性拉长整本书的处理时间，且收益不明显。
OCR_DPI = _int("OCR_DPI", 100)

# 表格还原总开关（1 开 / 0 关）。
# 关掉就回到"OCR 结果按阅读顺序拉成一维"的旧行为。
# 留这个开关是为了能一键隔离问题：还原涉及图像处理，
# 若某份资料因此出问题，不必改代码就能回退。
OCR_TABLE = _int("OCR_TABLE", 1)
# 表格还原时丢弃的 OCR 框置信度下限。
# 低置信度的框会在网格里造出假的行列；而表格线检测对噪声不敏感，
# 两者用同一份框会互相拖累。取 0.4 的依据：实测误识别的乱码
# 普遍低于 0.5，而正常文字多在 0.6 以上。
OCR_TABLE_MIN_CONF = _float("OCR_TABLE_MIN_CONF", 0.4)

# 识图输出为空时的重试上限。
#
# 空输出的成因是"推理 token 与回答 token 共用 max_tokens"：推理链一超长，
# 回答一个字都写不出来，且**不报错**。实测 16384 下长页面仍会零星命中
# （约 1%~2% 的页）。重试时把上限翻倍而不是关掉推理——
# 这些恰恰是最需要推理的长页面。
FIGURE_RETRY_MAX_TOKENS = _int("FIGURE_RETRY_MAX_TOKENS", 32768)

# ---- 检索 ----
# 向量相似度下限。库里没有相关内容时，纯向量仍会返回一批 0.3~0.5 的
# "看似相似"碎片，这比返回空更危险：会诱导模型拿无关内容作答。
MIN_VEC_SCORE = _float("MIN_VEC_SCORE", 0.35)
# 相对截断系数：低于"最高分 × 此系数"的结果被丢弃。
# 只靠绝对阈值不够——实测查 "TCP 为什么需要三次握手" 时，真正相关的
# "TCP 与 UDP 的对比" 只有 0.356，而不相关的深度学习段落有 0.473，
# 绝对阈值无法区分两者，相对第一名截断才能分开。
# 取 0.75 的依据：上例中无关段落比值 0.473/0.7205 = 0.66，必须卡在它之上。
RELVEC_RATIO = _float("RELVEC_RATIO", 0.75)

# 关键词路的相对截断系数。同理：几个常见词可能让大量无关块得分相同，
# 只保留相对最高分足够接近的，避免"沾点边就算命中"。
RELKW_RATIO = _float("RELKW_RATIO", 0.5)

# 关键词路的 IDF 加权。不加权时 "TCP" 这类泛词单独命中就能拿满分，
# 把只沾边的块排到前面；按词的信息量加权后才接近"真命中"的语义。
KW_IDF = _int("KW_IDF", 1)

# 关键词命中至少覆盖这一比例的查询词（按 IDF 加权），避免单字命中就进候选
KW_MIN_COVERAGE = _float("KW_MIN_COVERAGE", 0.34)

# 命中的词里，至少要有一个的权重达到最高词权重的这一比例，
# 否则视为"只命中了泛词"。用于过滤只含 "TCP" 而不含"三次握手"的块。
KW_SEL_RATIO = _float("KW_SEL_RATIO", 0.75)


# ---- 图片理解（识图）----
# 定位：补上"只在图里、文字层拿不到"的信息——结构、通路、解剖位置、图层关系。
# 刻意不做逐字转写：实测带文字层的导出件里，图上文字 85% 已在库中，
# 重复抄一遍没有价值（详见 docs/探测报告-DeepSeek图片理解能力.md）。
# ⚠ 卡 C-0059 起，`DEEPSEEK_BASE_URL` / `DEEPSEEK_VISION_MODEL` / `FIGURE_DESC` /
#   `FIGURE_THINKING` / `FIGURE_RENDER_DPI` / `FIGURE_MAX_CALLS_PER_DOC` 这几个键
#   **不再在这里固化成模块常量**——它们改由文件末尾的「设置中心」段用模块级
#   `__getattr__` 动态提供，这样页面右上角改一个值就立刻生效、不用重启看板。
#   读取姿势一个字都不用变：仍然是 `config.FIGURE_DESC`（全项目没有一处
#   `from .config import FIGURE_DESC`，见 core/subjects.py 的说明）。
ZHIPU_BASE_URL = os.environ.get("ZHIPU_BASE_URL",
                               "https://open.bigmodel.cn/api/paas/v4")
ZHIPU_VISION_MODEL = os.environ.get("ZHIPU_VISION_MODEL", "glm-4.6v-flash")
# 输出上限。**这个值必须足够大**：DeepSeek 的推理 token 与回答 token
# 共用同一个 max_tokens 配额。实测 max_tokens=3072 且开启推理时，
# 推理会吃掉全部 3072，导致**回答完全为空**（5 页里 4 页输出空白）。
# 384K 是模型上限，这里取 8192，给推理约 5000、回答约 3000 的空间。
FIGURE_MAX_TOKENS = _int("FIGURE_MAX_TOKENS", 8192)
# ⚠ `FIGURE_RENDER_DPI` / `FIGURE_MAX_CALLS_PER_DOC` / `FIGURE_THINKING` 三个键
#   同样挪到文末的「设置中心」段（动态取值，见那里的说明）。
# 识图是否开启推理模式。开启会显著增加输出 token（推理计入输出），
# 但对"结构理解"类任务的收益需要用实测判断，不要凭想当然。

# ---- AI 全局结构分析（见 core/outline.py）----
# 入库时"让大模型看完整份课件、画出思维导图、给每页定路径"这一步的开关。
# 关掉就退回原来的逐页局部推断（面包屑靠页面自身的长相猜）。
#
# 默认开：它是这条管线存在的意义——没有它，连续几页讲同一主题时
# 只有第一页能被搜到（用户实测反馈的原问题）。
# 成本很低（一份 46 页课件约 ¥0.01、3 秒），且按内容哈希缓存，重导入不重复付费。
# ⚠ `OUTLINE_ANALYSIS` 也挪到文末的「设置中心」段（动态取值）。
# 超过这个页数就不做全局分析：教材动辄几百页，既放不下也不是它的用武之地
# （教材本身有章节层级，不需要模型去猜）。
OUTLINE_MAX_PAGES = _int("OUTLINE_MAX_PAGES", 120)
# 推理模式。层级判断是语义级的（"p.27 算不算 p.26 底下的一项"），
# 不开推理时模型容易把各页拍平成并列的兄弟，跨页结构信息就丢了。
# ⚠ `OUTLINE_THINKING` 也挪到文末的「设置中心」段（动态取值）。
# ⚠ 输出上限必须给推理留足空间：**推理 token 与回答 token 共用这个配额**，
# 给少了推理会把它全吃光、回答完全为空且不报错（识图上实测过这个坑）。
# 一份 46 页课件的路径输出约 1200 token，推理约需 3000~8000，
# 取 32768 是留足余量；真空了会自动翻倍重试一次。
OUTLINE_MAX_TOKENS = _int("OUTLINE_MAX_TOKENS", 32768)

# ---- 一页多张幻灯片的 PDF（见 core/multippt.py）----
# 有些课件导出时把好几张 PPT 拼在一页 A4 上（实测"卢"系列是一页 2×3 共 6 张）。
# 这种资料直接入库会把 6 张幻灯片的文字串成一段、层级全丢。
# 打开后：入库**前**先检测，是这种就自动切成"一页一张"再走原管线。
SPLIT_MULTISLIDE = _int("SPLIT_MULTISLIDE", 1)

# ---- 课件级 AI 答疑的独立管线（卡 C-0057）----
# 人拍板（2026-09-29 原话）：「每份课件的AI答疑不要和整个知识库的AI问答混用，
# 开一条新的管线用于每份课件的答疑。这条管线不用提示词，思考强度开到最高。」
#
# 所以下面这一组键**只服务课件答疑**（`core/chat.py` 的 deck_* 区段 +
# `tools/dashboard.py::api_deck_chat_stream`），知识库问答
# （`/api/chat/stream`）读的是另外的东西（`chat.CHAT_MODEL` +
# `load_api_key()` 的默认凭据链），两者改一个不影响另一个：
#   DECK_CHAT_MODEL             模型名（默认沿用 deepseek-flash）
#   DECK_CHAT_API_KEY           密钥；**留空则回退**到 DEEPSEEK_API_KEY /
#                               .dsh/.credentials.yaml（即"和知识库问答用同一把"）
#   DECK_CHAT_REASONING_EFFORT  思考强度，官方取值 none/low/high/max；
#                               默认 max＝人要求的"开到最高"
#   DECK_CHAT_THINKING          是否开思考模式（1/0，默认 1）
#   DECK_CHAT_MAX_CONTEXT_TOKENS 课件内容进上下文的预算（估算 token）
#   DECK_CHAT_MAX_TOKENS        单次回答的输出上限（**思考 token 也算在里面**）
#   DECK_CHAT_FIGURE_SHARE      页图识别描述最多占总预算的比例（0~1）
#
# ⚠ `.env` 只在进程启动时**首次**读一次（见上面的 `_load_env`）。卡 C-0059 起，
#   这一组键（含下面注释里列出的全部 DECK_CHAT_*）都能在页面右上角「设置」里改，
#   落盘到 `config.local.json` 并**立刻生效**，不需要重启看板——见文末「设置中心」。
#   这 7 个键不再是模块常量，而是由模块级 `__getattr__` 动态提供。
# 60000 是个**保守闸门**，不是对某个模型窗口规格的断言：给思考与回答留出
# 大片余量，同时让"整份课件塞不下"这件事有个明确的、可调的上界。
# 超了就**保序等距抽样**（见 core/chat.py::build_deck_context），
# 并把"送出多少 / 一共多少 / 是否截断"如实报到界面上——不静默截断。
#
# ⚠ 口径提醒：这里与 `chunks.token_est` 用的是同一个**估算**系数
#   （0.75 token/字符，见 core/ingest.py），不是分词器精确值。实测偏**大**：
#   卡 C-0057 真跑一次（doc#1，218 块 + 13 页图注）估算 40639，服务端回的
#   `prompt_tokens` 是 28703（约 1.42 倍）。偏大是安全方向（不会超窗口），
#   代价是"本来塞得下"的课件被提前抽样——真嫌可惜就把这个数调大。

# 思考强度拉满时推理链会吃掉大量 completion token（实测一条知识点
# reasoning_tokens=10638，见 core/kp_build.py）。给 12288 的账是：
# 一次"总结整份课件"的问答，思考 3k~8k + 正文 2k~4k 都装得下，
# 而顶格也只要 ¥0.052（$0.60/1M × 12288 × 7.1）；被思考吃光时正文会空
# 且不报错，届时调大它（这是本项目反复踩过的坑，宁可给宽一点）。


# ---- 课件级 AI 答疑：**独立管线**（卡 C-0057）----
#
# 人拍板（2026-09-29）：「每份课件的 AI 答疑不要和整个知识库的 AI 问答混用，
# 开一条新的管线用于每份课件的答疑。这条管线不用提示词，思考强度开到最高。」
#
# 下面这些键**只服务这条管线**（`core/chat.py` 的 `deck_*` 一组函数 +
# `tools/dashboard.py::api_deck_ask_stream`）。它与知识库问答
# （`CHAT_MODEL` + `SYSTEM_PROMPT` + `gather_evidence` 那条链路）**没有任何共用
# 配置**：改这里的值是改"每份课件的答疑"，知识库问答一个字节都不动。
#
# 模型：**留空 = 沿用知识库问答那个模型**（`core/chat.py::CHAT_MODEL`）。
# 想单独换模型就在这里写死一个名字（或在 .env 里写 DECK_CHAT_MODEL=…）。
DECK_CHAT_MODEL = os.environ.get("DECK_CHAT_MODEL", "").strip()
# 端点：留空 = 沿用 `core/chat.py::DEEPSEEK_BASE`。
DECK_CHAT_BASE_URL = os.environ.get("DECK_CHAT_BASE_URL", "").strip()
# 密钥：**留空 = 沿用知识库问答那把钥匙**（DSH 凭据里的 DEEPSEEK_API_KEY）。
# 填了就用它（环境变量，或写进凭据文件 `DECK_CHAT_API_KEY: sk-…`）——
# 两条链路因此可以各用各的账号/额度，互不牵连。
DECK_CHAT_API_KEY = os.environ.get("DECK_CHAT_API_KEY", "").strip()
# 思考强度：人拍板"开到最高"→ "max"（官方档位 none/low/high/max，默认 high）。
# 只在 DECK_CHAT_THINKING=1 时才发给服务端（关掉思考时这个字段没有作用）。
DECK_CHAT_REASONING_EFFORT = os.environ.get("DECK_CHAT_REASONING_EFFORT", "max").strip()
# 是否开思考模式。1 开 / 0 关。
DECK_CHAT_THINKING = _int("DECK_CHAT_THINKING", 1)
# 输出上限。⚠ 思考 token 与回答 token **共用**这个配额：max 强度下思维链实测
# 能吃掉一万多 token（core/kp_build.py 的实测记录），给小了回答会被挤成空。
DECK_CHAT_MAX_TOKENS = _int("DECK_CHAT_MAX_TOKENS", 32768)
# 送进模型的**材料预算**（token；按 `core/chat.py::CHARS_PER_TOKEN` 折算字符）。
# 实测本项目一份课件 3k~9 万字符（≈2k~5.5 万 token）：小的全放得下，
# 大的（如 doc#89 8.9 万字符）会按预算截断——截断口径见
# `core/chat.py::deck_material`，并且**如实报到界面**上，不静默截断。
DECK_CHAT_CONTEXT_TOKENS = _int("DECK_CHAT_CONTEXT_TOKENS", 32000)
# 单条「页图识别描述」最多取多少字符（它有时写成近千字的长文，一条就能吃掉预算）。
DECK_CHAT_FIG_CHARS = _int("DECK_CHAT_FIG_CHARS", 1500)
# 对话历史带几轮（1 轮 = 一问一答）与每条历史的字符上限。
# 与知识库问答的 `MAX_HISTORY_TURNS` 各算各的，改这里不影响那边。
DECK_CHAT_HISTORY_TURNS = _int("DECK_CHAT_HISTORY_TURNS", 4)
DECK_CHAT_HISTORY_CHARS = _int("DECK_CHAT_HISTORY_CHARS", 1500)


def _seed_bundle_assets() -> list[str]:
    """把随包分发的**提示词真源**种进可写数据目录（卡 C-0073，只补缺、绝不覆盖）。

    为什么需要这一步：提示词是**管线资产**、跟着程序走，但它们的读写路径
    （`core/kp_build.py::_prompt_path_v2` 的本学科优先项、
    `config.save_prompt_text` 的"必须在 `ROOT` 下"校验）都指向**可写数据根**。
    打包后数据根是 exe 同级目录，那里一开始什么都没有，所以首次运行时
    从解包目录补一份过去；用户之后改了提示词，下次启动**不会**被覆盖回去。

    源码模式下**什么都不做**（返回空列表）：项目根本身就是真身。
    """
    if not getattr(sys, "frozen", False):
        return []
    src = BUNDLE_ROOT / "logs" / "knowledge"
    if not src.is_dir():
        return []
    dst = Path(LOG_DIR) / "knowledge"
    seeded: list[str] = []
    try:
        dst.mkdir(parents=True, exist_ok=True)
    except OSError:
        return []
    for f in sorted(src.glob("*.md")):
        target = dst / f.name
        if target.exists():
            continue
        try:
            shutil.copy2(f, target)
        except OSError:
            continue
        seeded.append(f.name)
    return seeded


def ensure_dirs() -> None:
    for d in (RAW_DIR, NOTES_DIR, INDEX_DIR, MODEL_DIR, LOG_DIR):
        d.mkdir(parents=True, exist_ok=True)
    _seed_bundle_assets()


# ==========================================================================
# 卡 C-0059：设置中心 —— 四条管线的「模型 / 密钥 / 识图 / 推理强度 / 提示词」
# ==========================================================================
#
# 人拍板（2026-09-29 原话）：「在页面右上角添加一个设置栏，点击后跳出窗口显示的是
# AI 问答管线、AI 知识点总结管线，入库管线和每份课件的 AI 答疑管线的大模型 api
# 及密钥的输入、是否开启识图、是否开启推理和调节推理强度、提示词显示和修改。」
#
# 三条硬规矩（改这个文件之前先读一遍）：
#
#   1. **唯一入口**：设置只经 `save_settings()` 写，落盘只有 `config.local.json`
#      一处。界面（tools/dashboard.py 的「设置」窗口）只调这里的函数，不自己
#      解析文件、不自己拼路径。
#   2. **改完立刻生效**：本段涉及的键**不再是 import 时固化的模块常量**，而是由
#      模块级 `__getattr__` 动态提供——所以 `config.FIGURE_DESC` 这类属性访问
#      **每次拿到的都是最新值**。core/ 里一处 `from .config import XXX` 都没有
#      （见 core/subjects.py 开头的说明），所以这一改动对所有读取点都是透明的。
#      真正需要"推一把"的只有三处（见 `_apply_runtime()`）：`core/chat.py` 的
#      端点/模型常量、`core/kp_build.py` 的模型与推理档、以及识图 HTTP 报文里
#      那个 `reasoning_effort` 字段。
#   3. **密钥不出本机**：`config.local.json` 在 `.gitignore` 里；窗口只显示后 4 位；
#      仓库里只有 `.env.example` 的占位符。密钥**不回传**给前端（`value` 恒为空串）。
#
# 取值优先级：`config.local.json` > `os.environ`（含 `.env` 读进来的）> 内置默认。
#
# ⚠ 生效时机：**保存后无需重启看板**；但**正在跑的任务**用的是它启动时那份值，
#   下一次任务才用新值。窗口里如实写了这句话。

SETTINGS_FILE = _path(
    "KB_SETTINGS_FILE", str(ROOT / "config.local.json"))
# ⚠ `KB_SETTINGS_FILE` 只给验证脚本用：把设置写到临时文件里，就能在不碰用户
#   真实配置的前提下测"改一个值 → 真管线用新值"。正常运行时它永远是
#   `config.local.json`（已被 .gitignore 忽略）。

# 四条管线（界面的四个分区，顺序即界面顺序）
_PIPELINES: list[dict] = [
    {
        "id": "chat", "label": "AI 问答", "icon": "💬",
        "sub": "就整个知识库提问 · /api/chat/stream",
        "desc": "先在库里检索相关片段，再让模型**只依据这些片段**作答并给出处。"
                "这条管线的推理默认关着——问答要的是快与有据。",
    },
    {
        "id": "kp", "label": "AI 知识点总结", "icon": "🧩",
        "sub": "整理管线 · core/kp_build.py（含结构 / 知识点两步）",
        "desc": "把一份课件的结构画成思维导图，再逐条生成末级知识点。"
                "提示词真源就是下面那几个 md，改完下一次整理就用新提示词。",
    },
    {
        "id": "ingest", "label": "入库", "icon": "📥",
        "sub": "解析 → 识图 → 结构分析 · core/ingest.py",
        "desc": "把 raw/ 下的资料索引进库。识图（图片理解）与 AI 结构分析是"
                "这条线上花钱的两步，各有独立开关与推理档位。",
    },
    {
        "id": "deck", "label": "课件答疑", "icon": "🎓",
        "sub": "每份课件的独立答疑 · core/chat.py::deck_*",
        "desc": "只针对**一份课件**提问：不检索、不用提示词，整份课件按页序进上下文。"
                "人拍板要求思考强度开到最高，这里可以调回去省钱。",
    },
    {
        "id": "preview", "label": "AI 预习总结", "icon": "📖",
        "sub": "按章预习 · core/preview.py::build_preview（**独立管线分区**）",
        "desc": "选一章 → 把这一章拆成更细的知识点（小标题来自卡 C-0092 的纯正则产物，"
                "**不额外调模型**）→ 用**这条管线自己的模型**给整章做一次预习总结。"
                "**整章只做 1 次调用**（对照全份知识点管线的数百次），"
                "而知识点那条管线的提示词与产物**一个字都没动**。"
                "⚠ 本功能**默认关闭**，要用请先打开下面那一条开关。",
    },
]

# 字段表：界面上每一行一个字段。type 取 str / secret / int / bool / enum / note
_FIELDS: list[dict] = [
    # ---------------- 管线 1：AI 问答 ----------------
    {"pipeline": "chat", "key": "CHAT_MODEL", "label": "模型名", "type": "str",
     "default": "deepseek-flash",
     "hint": "落位 core/chat.py 的请求 payload.model（看板显式传参，不影响入库那条）。"},
    {"pipeline": "chat", "key": "CHAT_BASE_URL", "label": "API Base", "type": "str",
     "default": "https://api.deepseek.com",
     "hint": "兼容 OpenAI 协议的端点根地址，不带 /chat/completions。"},
    {"pipeline": "chat", "key": "CHAT_API_KEY", "label": "API 密钥", "type": "secret",
     "default": "", "env": "DEEPSEEK_API_KEY",
     "hint": "只存在本机 config.local.json（已被 .gitignore 忽略），不会上传到任何地方。"},
    {"pipeline": "chat", "key": "CHAT_FIGURES", "label": "识图（用图注作证据）",
     "type": "bool", "default": 1,
     "hint": "关闭后，检索到的「图示说明」块（视觉模型对插图的解读）不再进提示词，"
             "只留原文块。出处角标里也不会再出现图注。"},
    {"pipeline": "chat", "key": "CHAT_THINKING", "label": "开启推理", "type": "bool",
     "default": 0,
     "hint": "默认关：问答要的是快与有据。开了会明显变慢、变贵。"},
    {"pipeline": "chat", "key": "CHAT_REASONING_EFFORT", "label": "推理强度",
     "type": "enum", "choices": ["none", "low", "high", "max"], "default": "high",
     "hint": "官方取值 none/low/high/max。**只有开启推理时才会发给服务端**"
             "（core/chat.py::chat_once 的既定口径）。"},
    {"pipeline": "chat", "key": "CHAT_MAX_TOKENS", "label": "回答上限（输出 token）",
     "type": "int", "default": 2048, "advanced": True,
     "hint": "开推理时思考 token 也算在这里面，太小会把正文挤空。"},

    # ---------------- 管线 2：AI 知识点总结 ----------------
    {"pipeline": "kp", "key": "KP_BUILD_MODEL", "label": "模型名", "type": "str",
     "default": "deepseek-flash",
     "hint": "落位 core/kp_build.py::MODEL（结构与知识点两步都用它）。"},
    {"pipeline": "kp", "key": "KP_BUILD_BASE_URL", "label": "API Base", "type": "str",
     "default": "https://api.deepseek.com",
     "hint": "⚠ 这条管线的请求由 core/chat.py 发出，端点常量全项目只有一份；"
             "与「AI 问答」填成不同值时，以**最近一次启动的管线**为准。"},
    {"pipeline": "kp", "key": "KP_BUILD_API_KEY", "label": "API 密钥", "type": "secret",
     "default": "", "env": "DEEPSEEK_API_KEY",
     "hint": "只存本机，不会上传到任何地方。"},
    {"pipeline": "kp", "key": "KP_BUILD_FIGURES", "label": "识图（用图注作材料）",
     "type": "fixed", "default": 1,
     "hint": "固定为开：整理时会**自动把库里已有的识图块**当作材料（不再调用视觉模型），"
             "本版不提供开关——关掉它需要改 core/kp_build.py 的取块口径。"},
    {"pipeline": "kp", "key": "KP_BUILD_THINKING", "label": "开启推理", "type": "bool",
     "default": 1,
     "hint": "层级的判断是语义级的，关掉推理容易把跨页结构拍平。"},
    {"pipeline": "kp", "key": "KP_BUILD_REASONING_EFFORT", "label": "推理强度",
     "type": "enum", "choices": ["none", "low", "high", "max"], "default": "max",
     "hint": "卡 C-0052 起默认 max。会写进产物 meta.calls[].reasoning_effort，可回查。"},
    {"pipeline": "kp", "key": "KP_BUILD_MAX_TOKENS", "label": "单条知识点上限（token）",
     "type": "int", "default": 65536, "advanced": True,
     "hint": "思考链与正文共用这个配额。实测一条知识点的思考链可占 1 万 token。"},

    # ---------------- 管线 3：入库 ----------------
    {"pipeline": "ingest", "key": "INGEST_MODEL", "label": "结构分析模型", "type": "str",
     "default": "deepseek-flash",
     "hint": "入库时「让模型看完整份课件、画出结构、给每页定路径」这一步用的模型。"},
    {"pipeline": "ingest", "key": "DEEPSEEK_VISION_MODEL", "label": "识图模型",
     "type": "str", "default": "deepseek-flash",
     "hint": "只有支持视觉的模型才行（deepseek-flash 支持；deepseek-v4-pro 不支持）。"},
    {"pipeline": "ingest", "key": "DEEPSEEK_BASE_URL", "label": "API Base", "type": "str",
     "default": "https://api.deepseek.com",
     "hint": "识图与结构分析共用的端点根地址（core/figures.py 直接读这个键）。"},
    {"pipeline": "ingest", "key": "INGEST_API_KEY", "label": "API 密钥", "type": "secret",
     "default": "", "env": "DEEPSEEK_API_KEY",
     "hint": "只存本机，不会上传到任何地方。入库识图与结构分析都用这一把。"},
    {"pipeline": "ingest", "key": "FIGURE_DESC", "label": "开启识图（图片理解）",
     "type": "bool", "default": 1,
     "hint": "关掉后入库**不再渲染页面、不再调视觉模型**，只索引文字层。"
             "识图约 25 秒/页、是要花钱的一步，这是最有效的省钱开关。"},
    {"pipeline": "ingest", "key": "FIGURE_THINKING", "label": "识图开启推理",
     "type": "bool", "default": 1,
     "hint": "开推理对「结构理解」类任务的收益要靠实测判断，关掉更省 token。"},
    {"pipeline": "ingest", "key": "FIGURE_REASONING_EFFORT", "label": "识图推理强度",
     "type": "enum", "choices": ["none", "low", "high", "max"], "default": "high",
     "hint": "补进识图请求的 reasoning_effort 字段（core/figures.py 自己不发这个字段，"
             "由本文件的运行时钩子补上）。关掉识图推理时按既定量口径**不发**。"},
    {"pipeline": "ingest", "key": "OUTLINE_ANALYSIS", "label": "开启 AI 结构分析",
     "type": "bool", "default": 1,
     "hint": "关掉就退回逐页局部推断：连续几页讲同一主题时只有第一页搜得到。"},
    {"pipeline": "ingest", "key": "OUTLINE_THINKING", "label": "结构分析开启推理",
     "type": "bool", "default": 1,
     "hint": "层级判断不开推理容易拍平。"},
    {"pipeline": "ingest", "key": "FIGURE_RENDER_DPI", "label": "页面渲染分辨率（DPI）",
     "type": "int", "default": 110, "advanced": True,
     "hint": "识图前把页面渲染成图的清晰度。实测中文术语识别率对 DPI 不敏感，调大只变慢。"},
    {"pipeline": "ingest", "key": "FIGURE_MAX_CALLS_PER_DOC",
     "label": "单份最多识图页数", "type": "int", "default": 200, "advanced": True,
     "hint": "每份资料最多为多少页花钱调视觉模型。"},

    # ---------------- 管线 4：课件答疑 ----------------
    {"pipeline": "deck", "key": "DECK_CHAT_MODEL", "label": "模型名", "type": "str",
     "default": "deepseek-flash",
     "hint": "落位 core/chat.py::deck_cfg()。这条管线与知识库问答完全独立。"},
    {"pipeline": "deck", "key": "DECK_CHAT_BASE_URL", "label": "API Base", "type": "str",
     "default": "https://api.deepseek.com",
     "hint": "⚠ 同「知识点总结」：端点常量全项目只有一份，填成不同值时以最近启动的管线为准。"},
    {"pipeline": "deck", "key": "DECK_CHAT_API_KEY", "label": "API 密钥", "type": "secret",
     "default": "", "env": "DECK_CHAT_API_KEY",
     "hint": "**真正独立的一把**：走 chat.stream_chat(api_key=…)，与问答/整理互不影响。"},
    {"pipeline": "deck", "key": "DECK_CHAT_FIGURES", "label": "使用识图（页图描述）",
     "type": "bool", "default": 1,
     "hint": "关掉后页图识别描述不再进上下文（派生的 figure_share 置 0），只送正文块。"},
    {"pipeline": "deck", "key": "DECK_CHAT_FIGURE_SHARE",
     "label": "页图描述占预算比例", "type": "float", "default": 0.2, "advanced": True,
     "hint": "0~1。上面那把开关关掉时，本键的生效值恒为 0（core/chat.py::deck_cfg 读它）。"},
    {"pipeline": "deck", "key": "DECK_CHAT_THINKING", "label": "开启推理", "type": "bool",
     "default": 1,
     "hint": "人拍板要求「思考强度开到最高」，默认开。"},
    {"pipeline": "deck", "key": "DECK_CHAT_REASONING_EFFORT", "label": "推理强度",
     "type": "enum", "choices": ["none", "low", "high", "max"], "default": "max",
     "hint": "会随 scope 事件报到界面上（hover 范围行可核对）。"},
    {"pipeline": "deck", "key": "DECK_CHAT_MAX_TOKENS", "label": "回答上限（token）",
     "type": "int", "default": 12288, "advanced": True,
     "hint": "思考 token 也算在里面；被思考吃光时正文会空且不报错。"},
    {"pipeline": "deck", "key": "DECK_CHAT_MAX_CONTEXT_TOKENS",
     "label": "课件上下文预算（token）", "type": "int", "default": 60000,
     "advanced": True,
     "hint": "超了就保序等距抽样，并在界面上如实报「送出多少 / 一共多少 / 是否截断」。"},
    # ---------------- 管线 5：AI 预习总结（卡 C-0094，**默认关闭**） ----------------
    # 人的原话（2026-10-14）：「增加一个预习模块……之后再开一个新的大模型对该章节做预习总结，
    # 大模型依旧 deepseek flash，思考强度开 high……该功能默认关闭，有选项可以打开。」
    # 这是**第 5 条独立管线**：模型 / 端点 / 密钥 / 推理档位都自己一份，
    # 与「AI 知识点总结」互不影响（那条管线的提示词与产物行为一字未改）。
    {"pipeline": "preview", "key": "PREVIEW_ENABLED", "label": "开启预习功能",
     "type": "bool", "default": 0,
     "hint": "★ **默认关闭**（人拍板）。关着时预习的启动入口会直接拒绝并告诉你打开这一项；"
             "打开后才会真的调模型花钱。关着不影响任何既有功能。"},
    {"pipeline": "preview", "key": "PREVIEW_MODEL", "label": "模型名", "type": "str",
     "default": "deepseek-flash",
     "hint": "落位 core/preview.py::model_name()。人拍板要求 deepseek-flash；"
             "这是**独立管线**，改这里不会动到知识点总结那条的模型。"},
    {"pipeline": "preview", "key": "PREVIEW_BASE_URL", "label": "API Base", "type": "str",
     "default": "https://api.deepseek.com",
     "hint": "⚠ 同「知识点总结」：端点常量全项目只有一份（core/chat.py::DEEPSEEK_BASE），"
             "填成不同值时以**最近一次启动的管线**为准。"},
    {"pipeline": "preview", "key": "PREVIEW_API_KEY", "label": "API 密钥", "type": "secret",
     "default": "", "env": "DEEPSEEK_API_KEY",
     "hint": "留空 = 跟随凭据链（与其它管线同一把）。只存本机 config.local.json，不会上传。"},
    {"pipeline": "preview", "key": "PREVIEW_THINKING", "label": "开启推理", "type": "bool",
     "default": 1,
     "hint": "人拍板要求「思考强度开 high」，所以推理默认**开着**。"
             "关掉会明显变快变便宜，但整章的主线归纳质量会掉。"},
    {"pipeline": "preview", "key": "PREVIEW_REASONING_EFFORT", "label": "推理强度",
     "type": "enum", "choices": ["none", "low", "high", "max"], "default": "high",
     "hint": "★ 人明确要求 **high**（**不是**知识点那条管线的 max）。"
             "会逐次写进产物 meta.calls[].reasoning_effort，可回查。"},
    {"pipeline": "preview", "key": "PREVIEW_MAX_TOKENS", "label": "整章总结上限（token）",
     "type": "int", "default": 16384, "advanced": True,
     "hint": "思考链与正文共用这个额度；被打满时管线会自动加倍重试一次。"
             "整章一次调用，这个值给太小会把正文挤空。"},
]

_FIELDS_BY_KEY: dict[str, dict] = {f["key"]: f for f in _FIELDS}
_PIPELINE_IDS = tuple(p["id"] for p in _PIPELINES)

# 每条管线「端点 / 模型 / 密钥」三个落位键
# ⚠ 卡 C-0094：新增一条管线**必须同时改这三处与上面的 `_PIPELINES`**，
#   否则 `activate()`（本文件 `:782` 起）会返回"未知管线"或直接 KeyError。
_PIPE_BASE_KEY = {"chat": "CHAT_BASE_URL", "kp": "KP_BUILD_BASE_URL",
                  "ingest": "DEEPSEEK_BASE_URL", "deck": "DECK_CHAT_BASE_URL",
                  "preview": "PREVIEW_BASE_URL"}
_PIPE_MODEL_KEY = {"chat": "CHAT_MODEL", "kp": "KP_BUILD_MODEL",
                   "ingest": "INGEST_MODEL", "deck": "DECK_CHAT_MODEL",
                   "preview": "PREVIEW_MODEL"}
_PIPE_KEY_KEY = {"chat": "CHAT_API_KEY", "kp": "KP_BUILD_API_KEY",
                 "ingest": "INGEST_API_KEY", "deck": "DECK_CHAT_API_KEY",
                 "preview": "PREVIEW_API_KEY"}

_SETTINGS_LOCK = threading.RLock()
_SETTINGS: dict[str, str] = {}
_SETTINGS_MTIME: float = -1.0
_ACTIVE = threading.local()
_LAST_PIPELINE = "chat"


def _ensure_loaded(force: bool = False) -> None:
    """把 config.local.json 读进内存（按 mtime 缓存，手工改文件也能被认出来）。"""
    global _SETTINGS, _SETTINGS_MTIME
    try:
        mt = SETTINGS_FILE.stat().st_mtime
    except OSError:
        mt = 0.0
    if not force and mt == _SETTINGS_MTIME:
        return
    data: dict[str, str] = {}
    if mt:
        try:
            raw = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                data = {str(k): v for k, v in raw.items()
                        if not str(k).startswith("_")}
        except (OSError, ValueError):
            data = {}
    _SETTINGS = data
    _SETTINGS_MTIME = mt


def _coerce(spec: dict, raw):
    """把字符串/任意输入折成该字段的类型；折不动就用默认值（绝不抛异常）。"""
    t = spec["type"]
    if t in ("int", "bool", "fixed"):
        if isinstance(raw, bool):
            return 1 if raw else 0
        try:
            return int(str(raw).strip() or spec["default"])
        except (TypeError, ValueError):
            return int(spec["default"])
    if t == "float":
        try:
            return float(str(raw).strip() or spec["default"])
        except (TypeError, ValueError):
            return float(spec["default"])
    if t == "enum":
        v = str(raw if raw is not None else "").strip()
        return v if v in spec["choices"] else str(spec["default"])
    v = str(raw if raw is not None else spec["default"]).strip()
    # 空字符串一律退回默认值（密钥除外——空串在那儿是"还没配"这个有意义的取值）。
    # 这条挡住的是 `.env` 里 `CHAT_MODEL=` 这种空赋值把默认值冲掉的情况。
    if not v and t != "secret":
        return str(spec["default"])
    return v


def value_of(key: str, default=None):
    """读一个配置键的**当前生效值**（供本文件与其他模块统一口径使用）。"""
    spec = _FIELDS_BY_KEY.get(key)
    if spec is None:
        return os.environ.get(key, default)
    _ensure_loaded()
    raw = _SETTINGS.get(key)
    if raw is None:
        raw = os.environ.get(spec.get("env") or key, spec["default"])
    val = _coerce(spec, raw)
    # 派生键：课件答疑那把「使用识图」开关关掉时，页图描述的比例恒为 0
    # （core/chat.py::deck_cfg 读的就是 DECK_CHAT_FIGURE_SHARE，不这样接就关不掉）。
    if key == "DECK_CHAT_FIGURE_SHARE" and not value_int("DECK_CHAT_FIGURES", 1):
        return 0.0
    return val


def value_int(key: str, default: int = 0) -> int:
    try:
        return int(value_of(key))
    except (TypeError, ValueError):
        return int(default)


def is_set(key: str) -> bool:
    """密钥类字段：本机是否已经有一把（用于界面显示"已保存 …后4位"）。"""
    return bool(str(value_of(key) or "").strip())


def key_tail(key: str) -> str:
    v = str(value_of(key) or "").strip()
    return v[-4:] if v else ""


def _write_settings_file(data: dict) -> None:
    """原子写：先写临时文件再替换，避免半截文件把配置读坏。"""
    tmp = SETTINGS_FILE.with_name(SETTINGS_FILE.name + ".tmp")
    body = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    tmp.write_text(body, encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)          # 尽力而为：Windows 上不一定管用
    except OSError:
        pass
    os.replace(tmp, SETTINGS_FILE)


def save_settings(patch: dict) -> dict:
    """保存若干字段（**部分更新**）。密钥留空＝跳过，绝不用空串覆盖已有密钥。

    返回 `{"ok", "saved", "skipped", "errors", "values", "applied", ...}`，
    界面据此提示"哪几项变了、是否要重启"（答案：不用重启）。
    """
    _ensure_loaded()
    patch = patch or {}
    saved: list[str] = []
    skipped: list[str] = []
    errors: list[str] = []
    global _SETTINGS, _SETTINGS_MTIME
    with _SETTINGS_LOCK:
        cur = dict(_SETTINGS)
        for k, v in patch.items():
            spec = _FIELDS_BY_KEY.get(str(k))
            if spec is None or spec["type"] in ("note", "fixed"):
                errors.append("%s：不是可写的配置项" % k)
                continue
            if spec["type"] == "secret" and not str(v or "").strip():
                skipped.append(str(k))          # ★ 留空＝不改（防止把已有密钥清空）
                continue
            if spec["type"] == "enum" and str(v).strip() not in spec["choices"]:
                errors.append("%s：取值必须是 %s"
                              % (k, "/".join(spec["choices"])))
                continue
            if spec["type"] in ("int", "bool") and str(v).strip() not in ("",):
                try:
                    int(str(v).strip())
                except (TypeError, ValueError):
                    errors.append("%s：必须是整数" % k)
                    continue
            cur[str(k)] = str(_coerce(spec, v))
            saved.append(str(k))
        if errors and not saved:
            return {"ok": False, "saved": [], "skipped": skipped, "errors": errors,
                    "values": settings_values(), "restart_required": False}
        if saved:
            _write_settings_file(cur)
            _SETTINGS = cur
            _SETTINGS_MTIME = SETTINGS_FILE.stat().st_mtime
    applied = _apply_runtime() if saved else {}
    return {"ok": True, "saved": saved, "skipped": skipped, "errors": errors,
            "values": settings_values(), "applied": applied,
            "settings_file": str(SETTINGS_FILE),
            "restart_required": False,
            "hint": ("已保存并生效（下一次任务就按新值跑；正在跑的任务不受影响）。"
                     "密钥只写在本机 %s 里，该文件已被 .gitignore 忽略。"
                     % SETTINGS_FILE.name)}


def reset_settings(keys: list[str] | None = None) -> dict:
    """把若干键（默认全部）恢复成"未设置"＝跟随 .env / 内置默认。"""
    _ensure_loaded()
    global _SETTINGS, _SETTINGS_MTIME
    with _SETTINGS_LOCK:
        cur = dict(_SETTINGS)
        for k in (keys if keys else list(cur)):
            cur.pop(str(k), None)
        _write_settings_file(cur)
        _SETTINGS = cur
        _SETTINGS_MTIME = SETTINGS_FILE.stat().st_mtime
    _apply_runtime()
    return {"ok": True, "values": settings_values(), "restart_required": False}


def settings_values() -> dict:
    """全部字段的当前生效值（密钥只给"是否已设 + 后 4 位"，明文不出内存）。"""
    out: dict = {}
    for f in _FIELDS:
        k, t = f["key"], f["type"]
        if t == "secret":
            out[k] = {"set": is_set(k), "tail": key_tail(k)}
        elif t == "note":
            out[k] = f.get("text", "")
        else:
            out[k] = value_of(k)
    return out


def settings_payload() -> dict:
    """给「设置」窗口的完整结构：四条管线 + 每条的字段（含说明与高级标记）。"""
    _ensure_loaded()
    pipes = []
    for p in _PIPELINES:
        fields = []
        for f in _FIELDS:
            if f["pipeline"] != p["id"]:
                continue
            item = {"key": f["key"], "label": f["label"], "type": f["type"],
                    "hint": f.get("hint", ""), "advanced": bool(f.get("advanced")),
                    "env": f.get("env") or f["key"]}
            if f["type"] == "enum":
                item["choices"] = list(f["choices"])
            if f["type"] == "secret":
                item["value"] = ""            # ★ 明文永不回传前端
                item["set"] = is_set(f["key"])
                item["tail"] = key_tail(f["key"])
            else:
                item["value"] = value_of(f["key"])
            fields.append(item)
        pipes.append({"id": p["id"], "label": p["label"], "icon": p["icon"],
                      "sub": p["sub"], "desc": p["desc"], "fields": fields})
    return {
        "pipelines": pipes,
        "settings_file": str(SETTINGS_FILE),
        "settings_file_ignored": True,
        "restart_required": False,
        "secret_notice": ("API 密钥只写在本机的 %s 里（该文件已被 .gitignore 忽略，"
                          "不会被提交到 GitHub，也不会上传到任何服务器）；"
                          "窗口里只显示后 4 位，留空表示不改动。" % SETTINGS_FILE.name),
    }


def _mod(name: str):
    """按包上下文取模块：包内用相对导入，单独跑 config.py 时退回绝对导入。"""
    import importlib
    pkg = __package__ or "core"
    try:
        return importlib.import_module("." + name, pkg)
    except Exception:                          # noqa: BLE001
        return importlib.import_module("core." + name)


def active_pipeline() -> str:
    """当前正在跑的是哪条管线（线程内标记优先，其次全局最近一次）。"""
    p = getattr(_ACTIVE, "pipeline", "")
    return p or _LAST_PIPELINE or "chat"


def pipeline_key(pipeline: str) -> str:
    """某条管线自己的密钥；没配就返回空串（由调用方回退到凭据链）。"""
    return str(value_of(_PIPE_KEY_KEY.get(pipeline, "")) or "").strip()


def activate(pipeline: str) -> dict:
    """启动某条管线**之前**调一次：把它自己的端点/模型/密钥推到该去的地方。

    为什么需要这一步：`core/chat.py` 的 `DEEPSEEK_BASE`、`CHAT_MODEL` 是模块常量
    （本卡的允许改动范围不含 chat.py），而入库的结构分析（core/outline.py）与
    知识点总结（core/kp_build.py）都要经它发请求。所以看板在每次启动管线前把
    当前配置推过去，并把这次推了什么如实返回，便于界面与验证脚本核对。
    """
    global _LAST_PIPELINE
    if pipeline not in _PIPELINE_IDS:
        return {"error": "未知管线：%s" % pipeline}
    _LAST_PIPELINE = pipeline
    _ACTIVE.pipeline = pipeline
    base = str(value_of(_PIPE_BASE_KEY[pipeline]) or "").strip()
    info = {"pipeline": pipeline, "base_url": base,
            "model": str(value_of(_PIPE_MODEL_KEY[pipeline]) or "").strip(),
            "key": "已配置" if pipeline_key(pipeline) else "回退凭据链"}
    try:
        ch = _mod("chat")
        if base:
            ch.DEEPSEEK_BASE = base
        if pipeline == "ingest":
            # 入库的「AI 结构分析」走 chat.CHAT_MODEL（core/outline.py 读它）；
            # 知识库问答已经改成显式传模型，不再受这一行影响。
            ch.CHAT_MODEL = str(value_of("INGEST_MODEL") or "").strip() or ch.CHAT_MODEL
    except Exception as e:                     # noqa: BLE001
        info["chat_error"] = "%s: %s" % (type(e).__name__, e)
    k = pipeline_key(pipeline)
    if k:
        # 识图那条路（core/figures.py::load_deepseek_key）只认环境变量，
        # 所以这里把它同步进去；chat 那条有 install_hooks() 的包装按管线取。
        os.environ["DEEPSEEK_API_KEY"] = k
    return info


def _with_figure_effort(payload):
    """给识图请求补 `reasoning_effort`（core/figures.py 自己不发这个字段）。

    只在**确实是识图请求**、且**没关掉推理**时补：关掉推理时按 core/chat.py
    的既定量口径"不发这个字段"，不在这里自造一套。
    """
    if not isinstance(payload, dict) or "reasoning_effort" in payload:
        return payload
    msgs = payload.get("messages")
    if not isinstance(msgs, list):
        return payload
    vision = False
    for m in msgs:
        c = m.get("content") if isinstance(m, dict) else None
        if isinstance(c, list) and any(
                isinstance(x, dict) and x.get("type") == "image_url" for x in c):
            vision = True
            break
    if not vision:
        return payload
    th = payload.get("thinking")
    if isinstance(th, dict) and th.get("type") == "disabled":
        return payload
    eff = str(value_of("FIGURE_REASONING_EFFORT") or "").strip()
    if not eff:
        return payload
    out = dict(payload)
    out["reasoning_effort"] = eff
    return out


_HOOKS: dict = {"installed": False, "orig_load_api_key": None}


def credential_ready() -> dict:
    """凭据链（环境变量 / `~/.dsh/.credentials.yaml`）当前是否可用。

    设置窗口靠它说一句实话："密钥留空"到底还能不能跑——而不是让人猜。
    """
    try:
        ch = _mod("chat")
        fn = _HOOKS.get("orig_load_api_key") or ch.load_api_key
        k = str(fn() or "")
        return {"ok": bool(k), "tail": k[-4:] if k else ""}
    except Exception as e:                     # noqa: BLE001
        return {"ok": False, "tail": "",
                "error": "%s: %s" % (type(e).__name__, e)}


def install_hooks() -> dict:
    """给两处"本卡不允许改的文件"装上薄包装（幂等，重复调用只装一次）：

      1. `core.chat.load_api_key`：按**当前管线**取密钥 —— 三条管线的密钥
         因此能各配各的（课件答疑本来就支持独立密钥，见 chat.stream_deck_chat）；
      2. `core.figures._http_post`：给识图请求补 `reasoning_effort`
         （`describe_page` 不认这个字段，不补的话"识图推理强度"就是个摆设）。

    两处包装都**不改任何既有逻辑**：取不到配置就走原函数。
    """
    out: dict = {"installed": [], "already": _HOOKS["installed"]}
    if _HOOKS["installed"]:
        return out
    try:
        ch = _mod("chat")
        orig_key = ch.load_api_key
        if not getattr(orig_key, "_c0059_wrapped", False):
            def _patched_load_api_key(*a, **kw):
                k = pipeline_key(active_pipeline())
                if k:
                    return k
                return orig_key(*a, **kw)
            _patched_load_api_key._c0059_wrapped = True   # type: ignore[attr-defined]
            ch.load_api_key = _patched_load_api_key
            _HOOKS["orig_load_api_key"] = orig_key
            out["installed"].append("chat.load_api_key")
    except Exception as e:                     # noqa: BLE001
        out["chat_error"] = "%s: %s" % (type(e).__name__, e)
    try:
        fg = _mod("figures")
        orig_post = fg._http_post
        if not getattr(orig_post, "_c0059_wrapped", False):
            def _patched_http_post(url, payload, key, *a, **kw):
                try:
                    payload = _with_figure_effort(payload)
                except Exception:              # noqa: BLE001
                    pass
                return orig_post(url, payload, key, *a, **kw)
            _patched_http_post._c0059_wrapped = True      # type: ignore[attr-defined]
            fg._http_post = _patched_http_post
            out["installed"].append("figures._http_post")
    except Exception as e:                     # noqa: BLE001
        out["figures_error"] = "%s: %s" % (type(e).__name__, e)
    _HOOKS["installed"] = True
    _apply_runtime()
    return out


def _apply_runtime() -> dict:
    """把当前设置推到"读不到 config 动态属性"的两个模块（幂等、失败不抛）。"""
    info: dict = {}
    try:
        ch = _mod("chat")
        ingest_model = str(value_of("INGEST_MODEL") or "").strip()
        if ingest_model:
            ch.CHAT_MODEL = ingest_model
        info["chat.CHAT_MODEL"] = ch.CHAT_MODEL
    except Exception as e:                     # noqa: BLE001
        info["chat_error"] = "%s: %s" % (type(e).__name__, e)
    try:
        kb = _mod("kp_build")
        kb.MODEL = str(value_of("KP_BUILD_MODEL") or "").strip() or kb.MODEL
        kb.THINKING_V2 = bool(value_int("KP_BUILD_THINKING", 1))
        kb.REASONING_EFFORT = (str(value_of("KP_BUILD_REASONING_EFFORT") or "").strip()
                               or kb.REASONING_EFFORT)
        kb.KP_MAX_TOKENS = value_int("KP_BUILD_MAX_TOKENS", kb.KP_MAX_TOKENS)
        try:
            # `_v2_call` 的默认参数（名词筛选那几处没显式传）也跟上同一个档位，
            # 否则会出现"知识点用 high、名词还在用 max"这种半新半旧的状态。
            kw = kb._v2_call.__kwdefaults__
            if isinstance(kw, dict):
                kw["thinking"] = kb.THINKING_V2
                kw["reasoning_effort"] = kb.REASONING_EFFORT
        except Exception:                      # noqa: BLE001
            pass
        info["kp_build.MODEL"] = kb.MODEL
        info["kp_build.THINKING_V2"] = kb.THINKING_V2
        info["kp_build.REASONING_EFFORT"] = kb.REASONING_EFFORT
    except Exception as e:                     # noqa: BLE001
        info["kp_build_error"] = "%s: %s" % (type(e).__name__, e)
    return info


# ---------------------------------------------------------------- 提示词真源
# 提示词是**管线资产**，不是学科数据；真源就是 logs/knowledge/ 下的那几个 md
# （core/kp_build.py 逐字提取里面的 ```text 代码块）。这里的读写是它们的
# **唯一入口**：改前自动 .bak，写完回报新旧 sha256，好让"改了到底有没有生效"
# 有据可查（产物里的 prompt_sha256 就是同一个口径）。
def read_prompt_text(path) -> dict:
    p = Path(path)
    if not p.exists():
        return {"ok": False, "error": "文件不存在：%s" % p, "path": str(p)}
    blob = p.read_bytes()
    return {"ok": True, "path": str(p),
            "sha256": hashlib.sha256(blob).hexdigest(),
            "chars": len(blob.decode("utf-8", "replace")),
            "mtime": p.stat().st_mtime,
            "text": blob.decode("utf-8", "replace")}


def save_prompt_text(path, text) -> dict:
    """保存提示词真源：改前自动 .bak-<时间戳>，返回新旧 sha256。"""
    p = Path(path).resolve()
    try:
        p.relative_to(ROOT.resolve())
    except ValueError:
        return {"ok": False, "error": "提示词真源必须在本项目目录内：%s" % p}
    if p.suffix.lower() != ".md":
        return {"ok": False, "error": "只允许编辑 .md 提示词真源"}
    if not p.exists():
        return {"ok": False, "error": "文件不存在：%s" % p}
    body = str(text if text is not None else "")
    if not body.strip():
        return {"ok": False, "error": "内容为空，已拒绝保存（不把提示词清空）"}
    before = hashlib.sha256(p.read_bytes()).hexdigest()
    bak = p.with_name(p.name + ".bak-" + time.strftime("%Y%m%d-%H%M%S"))
    try:
        shutil.copy2(p, bak)
    except OSError as e:
        return {"ok": False, "error": "备份失败，为不覆盖原文件已中止：%s" % e}
    tmp = p.with_name(p.name + ".tmp-c0059")
    with tmp.open("w", encoding="utf-8", newline="") as fh:
        fh.write(body)
    os.replace(tmp, p)
    after = hashlib.sha256(p.read_bytes()).hexdigest()
    return {"ok": True, "path": str(p), "backup": str(bak),
            "sha256_before": before, "sha256_after": after,
            "changed": before != after, "chars": len(body),
            "mtime": p.stat().st_mtime}


# ---------------------------------------------------------------- 动态属性
# PEP 562：模块级 `__getattr__` 只在"属性在模块字典里找不到"时触发。上面那些
# 键**故意不再赋值**，于是 `config.FIGURE_DESC` 这类访问每次都走这里 →
# 保存设置后立刻生效。属性不存在时仍然抛 AttributeError，
# `getattr(config, "OUTLINE_THINKING", 1)` 那种带默认值的写法语义不变。
def __getattr__(name: str):                    # noqa: D105
    spec = globals().get("_FIELDS_BY_KEY", {}).get(name)
    if spec is not None:
        return value_of(name)
    raise AttributeError("module %r has no attribute %r" % (__name__, name))


def __dir__():                                 # noqa: D105
    return sorted(set(globals()) | set(_FIELDS_BY_KEY))