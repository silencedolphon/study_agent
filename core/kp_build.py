# -*- coding: utf-8 -*-
# Copyright (c) 2026 潜声海豚 <2227663973@qq.com>. All rights reserved.
# 本源码为著作权人私有作品（proprietary）：仅供经书面授权的测试使用；
# 未经著作权人许可，不得复制、分发、公开、发布或用于任何商业用途。
# Unauthorized copying, distribution or publication is strictly prohibited.
# File-ID: SA-11-560d9719   (project study_agent; keep this line)
"""末级知识点整理管线（卡 C-0035）——看板按钮与命令行共用这一条。

一件事：**把一份课件的末级节点整理成知识点总结**，落成
`logs/knowledge/<doc_id>-末级知识点总结.json`，供看板导图弹窗按节点读取。

与卡 C-0031/C-0032/C-0034 那套「一页 = 一个末级节点」的批量脚本相比，这里有三处
**升级**（逐条对应人提的要求，提示词真源见 `logs/knowledge/提示词-知识点总结.md`）：

1. **选择性合并**：调用时给模型「当前页 + 后续至多 2 页」的**窗口**，由模型判断
   从当前页起连续几页属于同一个末级节点（依据信息密度低 + 主题连续，上限 3 页）。
   返回的 `pages` 由本模块**逐条校验**（升序、连续、含当前页、≤3 页、不越窗口），
   不合法就降级为单页并记 `merge.rejected_reason`——**绝不静默接受**。
2. **全中文 + 专有名词附英文**、**要点按结构/概念/过程/原因/结果五类精简**：
   都写在提示词里，本模块只负责把提示词逐字提取出来用（不存在"文档写一套、
   实际跑另一套"）。
3. **可被看板一键调用**：`build()` 用 `core/progress.py` 上报进度（**自己一组阶段名**，
   与入库的五个阶段不混），`start_background()` 起后台线程，`progress_snapshot()`
   给 `/api/kp-build/progress` 轮询；同一时刻只允许一个整理任务。

硬约束（本模块自己守，不靠调用方）：
- **只读真库**（`mode=ro`），只写 `logs/knowledge/` 下的产物文件，**不写任何表**。
- **不重新调用视觉模型**：识图文本直接用库里已有的识图块。
- **断点续跑**：每完成一条就原子落盘一次；重跑时跳过已覆盖的页。
  ⚠ 只有**本版**（`meta.pipeline_version == PIPELINE_VERSION`）的产物才参与续跑：
  C-0034 的旧产物是"一页一条"的口径，拿它续跑会把整份课件误判成"已完成"。
- **成本上限**：`max_cost`（人民币）到了就停，剩余页留到下次续跑。

用法::

    from core import kp_build
    kp_build.estimate(103)                    # dry-run：清单 + 预估成本，不调模型
    kp_build.build(103, dry_run=True)         # 同上（命令行走这个）
    kp_build.build(103, max_cost=0.8)         # 真跑（会调模型）
    kp_build.start_background(103)            # 看板：后台线程 + 进度
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import sqlite3
import threading
import time
from concurrent import futures as _cf
from pathlib import Path

from . import config, outline as outline_mod, progress
# S16 第一步：页面预约/释放抽到 kp_owners；旧名字继续可访问。
from .kp_owners import claim_pages, release_pages  # noqa: F401
# S16 第二步：纯页码/页列表工具抽到 kp_pages；旧下划线名字继续可访问。
from .kp_pages import REF_RE as _REF_RE  # noqa: F401
from .kp_pages import page_list as _page_list  # noqa: F401
from .kp_pages import pages_label as _pages_label  # noqa: F401
from .kp_pages import parse_ref as _parse_ref  # noqa: F401

# ---------------------------------------------------------------- 常量
# 【卡 C-0049：整理管线不再读入库结构，两代管线的版本号如下】
#
#   v1 = `C-0045-v1`：读 `logs/outline/<doc_id>.json`（入库管线的成果）拿"页 → 节点路径"，
#        据此分末级页/父节点页、把父节点页**下放**给一个末级宿主页，再按窗口调模型。
#        这条路的产物**全部保留**（人拍板：保留旧文件、页签不显示、按需逐份重整理），
#        代码也留着（`estimate_v1()` / `build_v1()` / `prev_info_v1()`），可随时对照。
#
#   v2 = `C-0049-v2`（**当前默认**）：只读库里的**文本**（chunks 的正文 + 识图文本，
#        按 source_ref 的页号组织），**不读** `logs/outline/*.json`，也**不读**
#        `chunks.topic` / `chunks.heading_path` 这类入库结构派生的字段。
#        思维导图结构改由本管线**自己的模型调用**生成（`gen_structure_v2()`，map-reduce），
#        每条知识点再挂到结构树的叶节点上。
#
# 的大模型来生成，不再复用知识库的成果。」
# 不再用于整理知识点……当整理知识点要生成思维导图结构时，应该由整理知识点管线
# 的大模型来生成，不再复用知识库的成果。」


PIPELINE_VERSION_V1 = "C-0045-v1"


PIPELINE_VERSION_V2 = "C-0049-v2"
#   v3 = `C-0052-v3`（**当前默认**）：v2 的骨架（只读文本 + 自生成结构）不动，
#        按人拍板（2026-09-27）改五处：
#          ① **开推理模式**：结构/知识点两步都 `thinking=True` +
#             `reasoning_effort="max"`（官方最高档），`max_tokens` 相应放大；
#          ② **换角色**：从"医药学/化学课件助手"改成"资深教师 + 广大专业学生"；
#          ③ **解除花费上限**：`BUDGET_UNLIMITED=True`，`max_cost` 只记录、不拦停；
#          ④ **解除页数上限**：不再"每条 ≤3 页"，覆盖页由模型判断 + 管线仲裁留痕；
#          ⑤ **新增 `terms`**：重点名词 + 英文 + 中文解释 + 首次出现的知识点 id。
#        版本号从 v2 升到 v3 是**必须的**：提示词 sha256 变了、产物字段也变了，
#        看板按 `meta.pipeline_version` 分流显示，旧 v2 产物**照旧可读**（不缺字段就崩不了：
#        新字段一律用 `.get()` 读，见 `build_v2()` 的续跑判断与 `prev_info_v2()`）。


PIPELINE_VERSION_V3 = "C-0052-v3"
#   v4 = `C-0058-v4`（**当前默认**）：v3 的骨架与提示词口径**一字未改**，只做一件事——
#        按人拍板（2026-09-29）「取消知识点总结管线与名词解释功能的偶联
#        （因为总结出的知识点太多了）」，把名词那两块**整块摘出去**：
#          ① `gen_kp_v2()` 不再从模型输出里读 `terms`、rec 里不再有
#             `terms` / `terms_rejected` / `quality_terms` 三个字段
#             （提示词真源 `PROMPT-SYS` 里那段 terms 要求也一并删掉）；
#          ② `build_v2()` 末尾不再有"汇总去重 + 一次全局筛选"，产物**不再有顶层 `terms` 键**、
#             `meta` 里不再有 `n_terms` / `terms_filter` / `params.terms_max_tokens`。
#        名词解释**功能保留**，但改成**按课件单独触发**的独立功能：
#          入口 `build_terms()` / `tools/terms_build.py --doc <id>`，
#          提示词真源 `logs/knowledge/提示词-名词解释.md`（新的 `TERMS_PROMPT_NAME`），
#          产物 `logs/knowledge/<doc_id>-名词解释.json`（`{"meta", "terms"}`，
#          `terms[]` 字段与 v3 的顶层 terms 逐个同构）。
#        为什么要升版本号：产物**少了一个顶层键**、提示词 sha256 也变了，
#        看板按 `meta.pipeline_version` 分流；旧 v2 / v3 产物**照旧可读**
#        （`tools/dashboard.py::_KP_READABLE_VERSIONS` 白名单三代都在册，
#        名词解释栏读不到 `terms` 时会去读独立产物）。
#        为什么这么改（实测依据，见卡 C-0058 方案报告）：逐条提炼曾占掉每条知识点
#        **26.40%** 的输出字符额度（doc#5：24 条 rec 里 terms 字段 28136/106565 字符），
#        末尾那次筛选占该产物全部花费的 **9.29%**（CNY0.1404 / CNY1.5117）。
#        ⚠ 知识点**条数**不会因此变少：v3/v4 都是"一个结构叶节点 = 一条知识点"，
#        条数由结构树（另一次调用、其提示词一个 terms 字都没有）决定。


PIPELINE_VERSION_V4 = "C-0058-v4"
# 模块级的"当前版本"：看板/批量整理用它判"这份产物是不是本版"（`prev_info()`）。


PIPELINE_VERSION = PIPELINE_VERSION_V4


KP_SUFFIX = "-末级知识点总结.json"

# 整理自己的阶段名（**不要**用入库那五个：`core/progress.py::STAGES` 是入库的，
# 而且 `progress.stage()` 只认它自己的 STAGES，所以进度快照里的 stages/stage_index
# 由本模块覆盖成这一组，见 `progress_snapshot()`）。
#
# ⚠ 卡 C-0049 改了名字（原为"读取结构/构建材料/调用模型/写入产物"）：v2 的第一步是
# **读库里的文本**、第二步是**生成结构**（调模型）、第三步才是写知识点。阶段**个数
# 保持 4 个**（看板的进度条按 `len(KP_STAGES)` 算百分比，个数变了会错位）。


KP_STAGES = ("读取文本", "生成结构", "写知识点", "写入产物")


MAX_MERGE = 3            # 最多合并 3 页（上限的理由写在提示词里，不是拍脑袋）
# 卡 C-0045：`MAX_MERGE` 现在限的是**末级页**的额度（合并判断与压缩比口径不变）；
# 窗口里「下放给当前末级页的上级节点页」**不占**这个额度，但受本兜底上限约束——
# 某页前面若挂了一长串上级页，一次调用的材料不能无限膨胀。


MAX_WINDOW_PAGES = 6


MODEL = "deepseek-flash"


MAX_TOKENS = 2048


TEMPERATURE = 0.3


THINKING = False


TIMEOUT = 300


RETRY = 2

# ---------------------------------------------------------------- 挂起兜底（卡 C-0123）
#
# 现场（2026-10-15 08:42，用户 8756 看板）：4 个 TCP 连接指向 api.deepseek.com
# (111.4.224.113:443)，建立于 08:42:43/44/48/56，**16 分钟没有任何字节**；看板进程
# CPU 零增长；`/api/kp-build/batch/progress` 仍报 active=true、n_running=2，两份任务
# 停在 kp_done=14 与 5 —— 用户看到的就是"知识点整理卡住"。
#
# 打桩重现（cards/C-0123/_c0123_repro.py，faulthandler 定罪栈）证明这条链**没有
# 任何一层超时**，一个挂起的调用就能把整批任务永久钉死：
#     worker 永卡  kp_build.py:2628 _v2_call ← :3290 gen_kp_v2 ← :5037 _kp_worker
#     build_v2 永卡 :5064 `_cf.wait(..., FIRST_COMPLETED)`      ← 无 timeout
#     kp_batch 永卡 :299  `pool.map(...)`                        ← 无 timeout
#   ⇒ `_STATE["active"]` 永远 True ⇒ 进度条永远"运行中"、`is_running()` 永远挡住
#     后续整理，只能重启看板。
#
# ⚠ 根子在 `core/chat.py`：`urllib.request.urlopen(req, timeout=TIMEOUT)` 的 timeout
# 是 **socket 单次操作**超时，实测对"连接活着、服务端不回包"（网关挂起 / 长思考被
# 中间设备吊住）**不生效**。chat.py 不在本卡的可改范围内，所以在**本文件**补兜底：
#
#   ① `model_slot(timeout=...)`：抢槽带上限，抢不到就抛错（不再无限排队）；
#   ② `_call_with_hard_timeout()`：真调用放进**陪跑线程**，最多等 `CALL_HARD_TIMEOUT`
#      秒 ⇒ 超时后 **worker 线程自己**从 `with model_slot` 里退出来、**当场把槽位还
#      回去**（陪跑线程不持槽），于是后续 worker 照跑，不会连锁饿死；
#   ③ `build_v2` 的 `_cf.wait(..., timeout=STALL_TIMEOUT)`：在飞调用全都不回来时
#      判"停滞"，落盘已完成的部分后收尾（`status="failed"` + 明确原因），
#      而不是永远"运行中"。
#
# 三个值一律走环境变量（默认值足够宽松，**正常路径永不触发**，行为逐字不变）：
#   · `KP_SLOT_WAIT_TIMEOUT`  默认 1800 秒：等模型槽位的上限；
#   · `KP_CALL_HARD_TIMEOUT`  默认 1800 秒：单次模型调用的硬上限；
#   · `KP_STALL_TIMEOUT`      默认 = 硬上限 + 600 秒：在飞调用全部无进展的判停线。
#
# ⚠⚠ **默认值不能拍脑袋定小**：本卡开工时（08:54）看到那 4 个连接"16 分钟没动静"，
# 差点定成"永久卡死"；但把现场跟到底后发现它们**只是极慢**——08:42:56 发起的调用
# 在 **08:58:10 真的返回了**（单次 914 秒），doc#2 随即跑完 20/20，doc#3 继续推进到
# 23/55。也就是说：DeepSeek 网关在高并发下会长时间吊住请求（排队/长思考），# **15 分钟级的一次调用是真实存在的**。所以硬墙必须明显大于这个实测最坏值
# （1800 秒 ≈ 2 倍余量），否则会把"慢但正常"的调用误判成挂起、白白丢掉已经付过钱的输出。
SLOT_WAIT_TIMEOUT_ENV = "KP_SLOT_WAIT_TIMEOUT"

CALL_HARD_TIMEOUT_ENV = "KP_CALL_HARD_TIMEOUT"

STALL_TIMEOUT_ENV = "KP_STALL_TIMEOUT"


def _env_seconds(key: str, default: float) -> float:
    """从环境变量取秒数（非法值/负值一律回退默认；`0` = 不限，用于关掉兜底）。"""
    raw = os.environ.get(key)
    if raw is None or str(raw).strip() == "":
        return float(default)
    try:
        v = float(str(raw).strip())
    except (TypeError, ValueError):
        return float(default)
    return v if v > 0 else 0.0


def slot_wait_timeout() -> float:
    """等模型槽位的上限（秒）；0 = 不限（退回改造前的行为）。"""
    return _env_seconds(SLOT_WAIT_TIMEOUT_ENV, 1800.0)


def call_hard_timeout() -> float:
    """单次模型调用的硬上限（秒）；0 = 不限（退回改造前的行为）。

    默认 1800 秒：实测最坏的一次真实调用是 914 秒（见上方注释），要留足余量。
    """
    return _env_seconds(CALL_HARD_TIMEOUT_ENV, 1800.0)


def stall_timeout() -> float:
    """在飞调用全部无进展的判停线（秒）；0 = 不限。

    比硬墙再宽 600 秒：硬墙负责把"单个挂起调用"打成失败，这一层只是防
    "硬墙本身也没生效"（例如陪跑线程创建失败）的最后一道。
    """
    return _env_seconds(STALL_TIMEOUT_ENV, call_hard_timeout() + 600.0)


class ModelSlotTimeout(RuntimeError):
    """抢模型槽位超时（有调用挂起没还槽，见文件上方的"挂起兜底"）。"""


class ModelCallHang(RuntimeError):
    """单次模型调用超过硬上限仍未返回（连接活着但不回包）。

    与普通调用失败的区别很重要：**这是"挂起"不是"报错"，重试只会再挂一个线程**，
    所以 `_v2_call` 收到它就直接放弃重试。
    """


def _call_with_hard_timeout(fn, *, timeout: float, kind: str = ""):
    """在**陪跑线程**里执行 `fn()`，最多等 `timeout` 秒；超时抛 `ModelCallHang`。

    为什么要陪跑线程（而不是直接调 `fn()`）：Python 没法中断一个卡在 socket read
    上的线程，但它**能不再等它**。这样调用方（worker）拿回控制权，
    `_v2_call` 的 `with model_slot(...)` 正常退出 ⇒ **槽位当场归还**，
    卡住的只是这一个陪跑线程（daemon，进程退出即清），不会拖垮别的 worker。

    `timeout<=0` 时退化成直接调用（与改造前逐字相同）。
    """
    if not timeout or timeout <= 0:
        return fn()
    from contextvars import copy_context
    context = copy_context()
    box: dict = {}

    def _run() -> None:
        try:
            box["r"] = context.run(fn)
        except BaseException as e:  # noqa: BLE001 - 原样带回调用方
            box["e"] = e

    th = threading.Thread(target=_run, name="kp-call%s" % (("-" + kind) if kind else ""),
                          daemon=True)
    th.start()
    th.join(timeout)
    if th.is_alive():
        raise ModelCallHang(
            "模型调用超过硬上限 %.0f 秒仍未返回（连接活着但服务端不回包）；"
            "已放弃等待、当场归还模型槽位（卡 C-0123 兜底）" % timeout)
    if "e" in box:
        raise box["e"]
    return box.get("r")

# 卡 C-0052：整理管线（v2/v3）两步都开推理模式、强度拉满。
#   - `reasoning_effort` 由 `core/chat.py::chat_once()` **原样透传**成 payload 字段，
#     官方取值 none/low/high/max（默认 high），"max" 就是人要求的最高档；
#   - ⚠ **思考 token 也算 completion token**：实测（2026-09-27）doc#5 的 8 页材料
#     一条知识点 = `completion_tokens=11196`，其中 `reasoning_tokens=10638`。
#     所以 `max_tokens` 必须放大（见 v2/v3 常量区的 `KP_MAX_TOKENS` /
#     `STRUCT_MAX_TOKENS`），否则正文被思维链挤爆（旧的 2048 连思维链都装不下）。
#   - ⚠ 思考模式下 `temperature` **不生效**（传了不报错、被服务端忽略）。
#   - 上面的 `THINKING`/`MAX_TOKENS`/`TEMPERATURE` 三个模块级常量**只服务 v1**
#     （`build_v1`/`_gen_one`），v2/v3 走下面那组自己的常量，避免动旧管线。


THINKING_V2 = True


REASONING_EFFORT = "max"
# 花费上限：人拍板（2026-09-27）「解除整理知识点时花费金额和页数的限制……
# 预算无上限」。为 True 时 `max_cost` 只写进 `meta.budget_cny` 供核对，
# **不再作为停止条件**；要恢复旧的硬上限行为，显式传 `budget_unlimited=False`
# （CLI 是 `--enforce-budget`）。


BUDGET_UNLIMITED = True

# 计价：与 `core/outline.py` 同一口径（非高峰 $0.15/1M 入、$0.60/1M 出，汇率 7.1）


PRICE_IN = 0.15


PRICE_OUT = 0.60


FX = 7.1
# 中文 token 估算：实测 doc#103 单页材料 ≈ 2338 token（含约 1000 token 提示词），
# 按 1.6 字符/token 估足够准，只用于 dry-run 的预估值，不参与记账。


CHARS_PER_TOKEN = 1.6


EST_OUT_TOKENS = 350

# 提示词**真源**（卡 C-0039 起是函数，不再是模块级常量）。
#
# 为什么必须动态取：`config.LOG_DIR` 会被切库改掉，模块级常量在 import 时
# 就固化了，切到新学科后仍指向老学科的路径。默认学科下取值与原来**逐字相同**。
#
# 为什么要"本学科优先、根目录兜底"：提示词真源是**管线资产**，不是学科数据。
# 新学科一出生，自己的 `logs/knowledge/` 里只有产物 json、没有这个 md；
# 若只认 `LOG_DIR`，新学科一整理就 `FileNotFoundError`——那"管线复用"就成了空话。
# 所以：本学科放一份就优先用它（允许单学科自定义提示词），没有就用项目根那份，
# 保证"真源仍然只有一份"（`load_prompts()` 的既定原则）。


_PROMPT_NAME = "提示词-知识点总结.md"


def default_prompt_file() -> Path:
    own = config.LOG_DIR / "knowledge" / _PROMPT_NAME
    if own.exists():
        return own
    root = config.ROOT / "logs" / "knowledge" / _PROMPT_NAME
    if root.exists():
        return root
    # 卡 C-0073（exe 路线）：打包后随包分发的真源在**解包目录**里
    # （`sys._MEIPASS`，一目录形态是 `<exe目录>\_internal`），这一级是保底——
    # 用户把 exe 同级的 `logs/knowledge/` 整份删掉，管线也不至于直接不可用。
    # 源码模式下 `config.BUNDLE_ROOT` 与 `config.ROOT` 是同一个目录，
    # 上面一行已经返回，走不到这里 ⇒ **源码模式行为逐字不变**。
    return config.BUNDLE_ROOT / "logs" / "knowledge" / _PROMPT_NAME


# ---------------------------------------------------------------- 提示词


def prompt_file(path: str | os.PathLike | None = None) -> Path:
    return Path(path) if path else default_prompt_file()


def _grab(md: str, tag: str, path: Path) -> str:
    m = re.search(r"<!-- %s-BEGIN -->\s*```text\n(.*?)\n```\s*<!-- %s-END -->"
                  % (tag, tag), md, re.S)
    if not m:
        raise SystemExit("提示词真源里找不到 %s 代码块：%s" % (tag, path))
    return m.group(1)


def _grab_opt(md: str, tag: str) -> str:
    """**可选**提示词块：找不到就返回空串，不 raise。

    卡 C-0052 曾用它取知识点真源里的 terms 两个块；卡 C-0058 把名词摘出后，
    知识点这条管线已经没有可选块了（`load_kp_prompts_v2()` 只取 SYS/USER）。
    这个函数**保留**：独立的名词解释功能将来若要扩可选块可以直接用，
    而且删掉它会让"可选块"这条既有契约从代码里消失。
    """
    try:
        return _grab(md, tag, Path("(optional)"))
    except SystemExit:
        return ""


def load_prompts(path: str | os.PathLike | None = None) -> dict:
    """从提示词真源逐字提取 SYS/USER，并算出文件 sha256（记进产物 meta）。

    真源是唯一的一份：改提示词只改那个 md，本模块不做任何"内置默认提示词"，
    否则就会出现"文档写一套、实际跑另一套"。
    """
    p = prompt_file(path)
    blob = p.read_bytes()
    md = blob.decode("utf-8")
    sys_p = _grab(md, "PROMPT-SYS", p)
    user_p = _grab(md, "PROMPT-USER", p)
    return {"sys": sys_p, "user": user_p, "sha256": hashlib.sha256(blob).hexdigest(),
            "path": str(p), "chars": len(sys_p) + len(user_p)}


# ---------------------------------------------------------------- 结构（末级节点）


def _outline_pages_from_cache(d: dict) -> dict:
    """退化成只读 `path`（`_load_pages` 万一改了私有接口时的兜底）。"""
    out: dict[int, list[str]] = {}
    for k, v in (d.get("pages") or {}).items():
        try:
            pno = int(k)
        except (TypeError, ValueError):
            continue
        path = v.get("path") if isinstance(v, dict) else v
        if isinstance(path, list):
            names = [str(x).strip() for x in path if str(x or "").strip()]
            if names:
                out[pno] = names
    return out


def outline_pages(doc_id, outline_path):
    """读一份 outline 缓存，返回 {页号: 路径}。

    **与看板逐字同源**（`tools/dashboard.py::_outline_pages`）：
      1. 解析交给 `core.outline._load_pages()`（它认得旧缓存的 `theme + leaf` 写法）；
      2. 再补一层「**末项豁免**」——`core.outline._clean()` 按**子串包含**判同层
         重复，会把某页路径的末项当成祖先的同义项剔掉，那一页因此在树里
         失去落点。实测 doc#103 有 13 页这样；只把被剔掉的末项补回来
         （仅当缓存里的原路径正好是解析结果的**延长**时）。
    看板与管线必须用同一套解析，否则"看板里点得开的节点"与"管线建的知识点"
    会对不上号。`tools/verify_c0035*` 会拿 40 份缓存逐份比对两边输出全等。
    """
    if outline_path:
        p = Path(outline_path)
    else:
        p = config.LOG_DIR / "outline" / f"{int(doc_id)}.json"

    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}

    if not isinstance(d, dict):
        return {}

    try:
        out = outline_mod._load_pages(d)
    except Exception:
        return _outline_pages_from_cache(d)

    for k, v in (d.get("pages") or {}).items():
        try:
            pno = int(k)
        except (TypeError, ValueError):
            continue

        cur = out.get(pno)
        path = v.get("path") if isinstance(v, dict) else v
        if not cur or not isinstance(path, list):
            continue

        names = [str(x).strip() for x in path if str(x or "").strip()]
        if len(names) > len(cur) and names[:len(cur)] == list(cur):
            out[pno] = names

    return out


def load_outline(doc_id, outline_path):
    p = Path(outline_path) if outline_path else config.LOG_DIR / 'outline' / f"{int(doc_id)}.json"
    try:
        d = json.loads(p.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    if isinstance(d, dict):
        return d
    return {}


def split_leaf_pages(pages):
    """把 {页号: 路径} 分成 (末级页, 父节点页)。

    口径与看板 `_attach_lkp` 的 `lkp_role` 同一套：某页路径若还是**另一页路径的
    真前缀**，该页在导图里还有下级 → 是"父节点页"，按现有口径不建知识点；
    其余页路径的终点就是**末级节点**。返回 (leaf, parent)，都是升序列表。
    """
    keys = {p: tuple(pages[p]) for p in pages}
    leaf, parent = [], []
    for p in sorted(pages):
        me = keys[p]
        has_kid = any(keys[k][:len(me)] == me and keys[k] != me for k in keys)
        (parent if has_kid else leaf).append(p)
    return leaf, parent


def leaf_descendants(p, pages, leaf):
    """页 p 的**真后代末级页**（页号更大、路径以 p 的路径为前缀），升序。"""
    me = tuple(pages[p])
    return [
        L
        for L in leaf
        if L > p
        and len(pages[L]) > len(me)
        and tuple(pages[L])[: len(me)] == me
    ]


def _parent_groups(parent):
    """把父节点页按**极大连续段**分组：{9,10,11} 是一组，{14} 是一组。

    为什么必须整组一起下放：下放的目标是"这一节的内容别丢"，而连续的几页
    上级节点本来就是同一节的引子；拆开分给不同宿主，会出现"A 的页进了 B 的
    材料、B 的页进了 A 的材料"，同一段内容在两条知识点里各写一遍（重复计数）。
    """
    groups = []
    cur = []
    for p in sorted(parent):
        if cur and p == cur[-1] + 1:
            cur.append(p)
            continue
        if cur:
            groups.append(cur)
        cur = [p]
    if cur:
        groups.append(cur)
    return groups


def _pick_host(group, pages, leaf):
    """给一组连续父页选**唯一宿主末级页**（页号）。

    优先级（越靠前越"主题连续"）：
      1. 组内每一页的**真后代末级页**的**交集**里页号最小者
         —— 整组都是它的上级，内容天然同一条脉络；
      2. 组**之后**最近的一个末级页 —— 页号相接，课件里就是"引子 + 第一小节"；
      3. 组**之前**最近的一个末级页（兜底：该组后面没有末级页了）。
    都没有（整份课件只有父页，不可能但要有出口）→ 返回 0，调用方记为未下放。
    """
    sets = [set(leaf_descendants(P, pages, leaf)) for P in group]
    common = set.intersection(*sets) if sets else set()
    if common:
        return min(common)
    after = [L for L in leaf if L > group[-1]]
    if after:
        return after[0]
    before = [L for L in leaf if L < group[0]]
    if before:
        return before[-1]
    return 0


def handoff_map(pages, leaf, parent):
    """算出父节点页的**下放归属**：({宿主末级页: [下放页…]}, {页号: 宿主末级页})。

    一张页只能有一个宿主（`owner` 是**全页覆盖**的映射：末级页归自己，父页归宿主），
    这是"同一页不被两条知识点重复收录"的机器可判据——产物 `meta.coverage.overlap_pages`
    必须为空。

    返回的 `handoff[L]` 里的页**保证与 L 页号连续、且全部 < L**（宿主在组之后的情形）；
    若某组只能退到"组之前的末级页"（宿主 < 下放页），则该组记进第二个返回值的
    `unplaced`，不强行下放——那时材料顺序会倒挂，模型会读不懂因果。

    ⚠ 这里**不改** `split_leaf_pages()` 的末级/父节点判定：那套口径与看板
    `_attach_lkp` 的 `lkp_role` 逐字同源（C-0034/C-0035 的既定约定），
    下放只是管线内部"这一页的材料归谁写"，不动导图上的节点身份。
    """
    owner = {int(p): int(p) for p in leaf}
    handoff = {int(p): [] for p in leaf}
    unplaced = []
    for group in _parent_groups(parent):
        host = _pick_host(group, pages, leaf)
        if not host or host < group[-1]:
            unplaced.append({
                "pages": list(group),
                "host": int(host) if host else 0,
                "reason": "找不到位于它之后的末级宿主页" if not host else "只有更靠前的末级页，材料顺序会倒挂",
            })
            for p in group:
                owner[int(p)] = 0
            continue
        handoff[host].extend(group)
        for p in group:
            owner[int(p)] = host
    for k in handoff:
        handoff[k] = sorted(set(handoff[k]))
    return handoff, owner, unplaced


def _figure_rows(conn, doc_id, cols):
    """读该文档的识图行；**表不存在时返回空**（卡 C-0077 兜底）。

    为什么要有这道守卫：`figure_desc` 原来**只由识图路径建表**（`figures.ensure_schema`），
    空库（建完库、还没做过识图入库）里没有它，而本模块直接 `FROM figure_desc`
    就抛 `OperationalError: no such table: figure_desc` —— 看板「AI 知识点总结」
    第一次点就是 500。卡 C-0077 已把建表搬进 `db.init_db()`（新库、旧库、
    新建学科库、exe 首启动都有表），这里再加一道兜底，即使表被删也不会崩。

    为什么这里**不**顺手建表：本模块的硬约束是"只读真库（`mode=ro`）、
    不写任何表"（见模块 docstring），而且这两条连接的 DSN 就是 `mode=ro`，
    **物理上建不了表**。表缺失在语义上等价于"这份课件没有任何识图数据"
    ——`no_info` 只用来给材料加一句提示，少它不影响任何结论。所以降级为空。

    `cols` 只由本模块内部传常量字符串，不含外部输入。
    """
    try:
        return conn.execute(
            'SELECT %s FROM figure_desc WHERE doc_id=?' % cols,
            (int(doc_id),),
        ).fetchall()
    except sqlite3.OperationalError as e:
        if 'no such table' not in str(e):
            raise
        return []


def read_chunks(doc_id: int, db_path: str | os.PathLike | None = None) -> dict:
    """读该文档的块与识图记录（**只读**）。

    返回 {body, fig, texts, figrows, cover}：
      body[page] = [(ord, chunk_id)]   正文块（按 `source_ref` 的**起始页**归档）
      fig[page]  = [(ord, chunk_id)]   识图块（图片理解），同样按起始页归档
      texts[cid] = 块原文
      figrows[page] = figure_desc 那一行（no_info 用来给材料加提示）
      cover[page]   = 覆盖该页的全部块 id（跨页块 `p.12-16` 会落在 12..16 每一页上）

    `chunks` 表**没有 page 列**：页码一律从 `source_ref`（`p.3` / `p.3 图` /
    `p.12-16`）解析；解析不出来的行**直接报错**，绝不静默丢块。
    """
    db = Path(db_path) if db_path else Path(config.DB_PATH)
    conn = sqlite3.connect("file:" + db.as_posix() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        texts: dict = {}
        body: dict = {}
        fig: dict = {}
        cover: dict = {}
        span: dict = {}
        bad = []
        for r in conn.execute("SELECT id, ord, source_ref, text FROM chunks "
                              "WHERE doc_id=? ORDER BY ord, id", (int(doc_id),)):
            cid = int(r["id"])
            ref = (r["source_ref"] or "").strip()
            parsed = _parse_ref(ref)
            if parsed is None:
                bad.append((cid, ref))
                continue
            a, b, is_fig = parsed
            texts[cid] = r["text"] or ""
            span[cid] = (a, b)
            for p in range(a, b + 1):
                cover.setdefault(p, set()).add(cid)
            (fig if is_fig else body).setdefault(a, []).append(
                (int(r["ord"] or 0), cid))
        figrows: dict = {}
        for r in conn.execute("SELECT page, page_title, description, no_info, n_chunks "
                              "FROM figure_desc WHERE doc_id=?", (int(doc_id),)):
            figrows[int(r["page"])] = dict(r)
    finally:
        conn.close()
    if bad:
        raise RuntimeError("有 %d 个块的 source_ref 解析不出页码（拒绝静默丢块）：%s"
                           % (len(bad), bad[:5]))
    return {"body": body, "fig": fig, "texts": texts, "figrows": figrows,
            "cover": cover, "span": span}


def _page_stats(doc_id: int, db_path: str | os.PathLike | None = None) -> dict:
    """每页有多少块、多少字——**只算不看正文**，dry-run / 预估专用。

    与 `read_chunks()` 同一口径（块按 `source_ref` 起始页归档，跨页块区间内
    每页都算被覆盖），只是不把 `text` 取回来：全库 9217 块、240 万字，把正文
    读回来只为了数长度，会让看板「整理知识点」弹窗的预估慢到十几秒。
    """
    db = Path(db_path) if db_path else Path(config.DB_PATH)
    conn = sqlite3.connect("file:" + db.as_posix() + "?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT source_ref, COUNT(*) AS n, SUM(LENGTH(text)) AS c FROM chunks "
            "WHERE doc_id=? GROUP BY source_ref", (int(doc_id),)).fetchall()
    finally:
        conn.close()
    per: dict = {}
    cover: dict = {}
    bad = []
    total = 0
    n_blocks = 0
    for ref, n, c in rows:
        parsed = _parse_ref(ref or "")
        if parsed is None:
            bad.append(ref)
            continue
        a, b, is_fig = parsed
        d = per.setdefault(a, {"chars": 0, "n_body": 0, "n_fig": 0})
        d["chars"] += int(c or 0)
        d["n_fig" if is_fig else "n_body"] += int(n or 0)
        total += int(c or 0)
        n_blocks += int(n or 0)
        # 跨页块：区间内每一页都算被覆盖（与 read_chunks() 同口径）
        for p in range(a, b + 1):
            cover.setdefault(p, 0)
            cover[p] += int(n or 0)
    return {"per_page": per, "cover_n": cover, "bad": bad,
            "total_chars": total, "n_blocks": n_blocks}


def doc_title(doc_id: int, conn=None) -> str:
    """课件标题：优先库里 documents.title，其次 outline 的 deck，最后兜底。"""
    raw = load_outline(doc_id)
    title = str(raw.get("deck") or "").strip()
    try:
        own = conn is None
        if own:
            db = Path(config.DB_PATH)
            conn = sqlite3.connect("file:" + db.as_posix() + "?mode=ro", uri=True)
        row = conn.execute("SELECT title FROM documents WHERE id=?",
                           (int(doc_id),)).fetchone()
        if row and (row["title"] or "").strip():
            title = str(row["title"]).strip()
        if own:
            conn.close()
    except Exception:  # noqa: BLE001
        pass
    return title or ("文档 %d" % int(doc_id))


# ---------------------------------------------------------------- 材料


def window_of(page, leaf_set, handoff, owner):
    """从 page 起的**连续窗口**：下放给它的上级节点页 + 本页 + 后续末级页。

    卡 C-0045 起，窗口不再"只从当前页开始"：
      - **起点** = `min(下放给 page 的上级页 ∪ {page})`——上级页的内容跟它后面
        那一小节本来就是连着的，拆开就丢（这正是 doc#5 丢 11 页 / 42% 正文的原因）；
      - **额度**：窗口内**末级页**最多 `MAX_MERGE` 个（合并判断与压缩比口径不变），
        前置的上级页不占这个额度，但总页数受 `MAX_WINDOW_PAGES` 兜底；
      - **不越界**：遇到"归**别的**宿主的页"就停——否则同一页会进两条知识点，
        重复计数就发生在这一步。

    不传 `handoff`/`owner` 时退化为旧口径（从当前页起的连续末级页，≤ `MAX_MERGE` 页），
    方便单独调用与回归对比。

    ⚠ **卡 C-0048：宿主被阻断时不再静默丢下放页**。旧版在"下放页与宿主之间隔着
    别的条目的页"时（实测 doc#3 的 p.5 下放给 p.19，相距 14 页）会落到"只取宿主"
    的兜底，下放页**悄悄消失**（覆盖 30/31）。现在这种情形改走"显式携带"：
    优先在原窗口里给下放页排一个位置（下放页与宿主之间的缺口页**不算进窗口**，
    模型仍按页面块读到连续内容），配额实在不够时返回 `[]`——由调用方（`estimate()`
    → `meta.coverage.window_unclaimed`）逐页登记"没被任何窗口认领"。
    常规情形（宿主与下放页相邻/可同窗）的行为逐字不变。
    """
    back = sorted(
        (handoff or {}).get(page) or []
    )
    owner = owner or {}
    if not back:
        win = [page]
        nxt = page + 1
        while len(win) < MAX_MERGE and nxt in leaf_set:
            win.append(nxt)
            nxt += 1
        return win

    known = set(leaf_set) | set(back) | set(owner or {})
    win = []
    n_leaf = 0
    p = min(back + [page])
    while p in known:
        if p in leaf_set:
            if n_leaf >= MAX_MERGE:
                break
            n_leaf += 1
        else:
            if owner.get(p) != page:
                break
        win.append(p)
        if len(win) >= MAX_WINDOW_PAGES:
            break
        p += 1

    if page in win:
        return win

    tail = [x for x in back if x != page]
    if tail:
        n_leaf, q = 0, int(page)
        while q in known and len(win) + len(tail) < MAX_WINDOW_PAGES:
            if q in leaf_set:
                if n_leaf >= MAX_MERGE:
                    break
                n_leaf += 1
            else:
                if owner.get(q) != page:
                    break
            tail.append(q)
            q += 1
        if not tail or tail[0] != int(page):
            return []

    room = MAX_WINDOW_PAGES - len(tail)
    head = []
    for x in win:
        if len(head) < room or x in back:
            head.append(x)

    got = sorted(set(head) | set(tail))
    if page not in got or not set(back) <= set(got):
        return []
    return got


def page_block_rows(page, ch):
    """该页的材料块清单（正文块 + 识图块），带字数。"""
    texts = ch['texts']
    body, fig = ch['body'], ch['fig']
    rows = []
    for _o, cid in sorted(body.get(page) or []):
        rows.append({
            'chunk_id': cid,
            'kind': '正文块',
            'page': page,
            'ref': 'p.%d' % page,
            'chars': len(texts.get(cid, '')),
        })
    for _o, cid in sorted(fig.get(page) or []):
        rows.append({
            'chunk_id': cid,
            'kind': '图片理解',
            'page': page,
            'ref': 'p.%d 图' % page,
            'chars': len(texts.get(cid, '')),
        })
    return rows


def render_materials(win, pages, ch, cur, parent_pages, carry_pages):
    texts, body, fig, figrows = ch['texts'], ch['body'], ch['fig'], ch['figrows']
    span = ch.get('span') or {}
    pp = parent_pages or set()
    cp = carry_pages or set()
    lines = []
    inv = []
    for p in win:
        lines.append('===== 第 %d 页 =====' % p)
        path = pages.get(p) or []
        lines.append('【本页在导图中的节点路径】%s' % ' > '.join(path))
        if p in pp:
            if p in cp:
                where = '其宿主末级页（p.%d）已被本条（含并列合并）一并收录，转由本条补入' % cur
            else:
                where = '它自己不成条'
            lines.append('（注：本页是**上级节点页**「%s」——它在导图里还有下级节点、%s；它的内容必须**并入本条知识点**，并在 pages 里带上第 %d 页。）' % (path[-1] if path else 'p.%d' % p, where, p))
        elif cur is not None and p != cur and p < cur:
            lines.append('（注：本页在导图里是末级节点页，先于当前页 p.%d 出现。）' % cur)
        rows_fig = fig.get(p) or []
        fdr = figrows.get(p) or {}
        if fdr.get('no_info'):
            lines.append('（注：本页图片已被判定为“无知识性图示”，下面的图片理解只是占位，不含知识内容）')
        for _o, cid in sorted(body.get(p) or []):
            t = texts.get(cid, '')
            a, b = span.get(cid, (p, p))
            lines.append('〔正文块〕[%d]（%s）原文：%s' % (cid, 'p.%d' % a if a == b else 'p.%d-%d' % (a, b), t))
            inv.append({
                'chunk_id': cid,
                'page': p,
                'kind': '正文块',
                'ref': 'p.%d' % a if a == b else 'p.%d-%d' % (a, b),
                'chars': len(t),
                'pages': list(range(a, b + 1)),
            })
        for k, (_o, cid) in enumerate(sorted(rows_fig), 1):
            t = texts.get(cid, '')
            a, b = span.get(cid, (p, p))
            lines.append('〔图片理解·%d/%d〕[%d]（%s）%s' % (k, len(rows_fig), cid, 'p.%d 图' % a if a == b else 'p.%d-%d 图' % (a, b), t))
            inv.append({
                'chunk_id': cid,
                'page': p,
                'kind': '图片理解',
                'ref': 'p.%d 图' % a if a == b else 'p.%d-%d 图' % (a, b),
                'chars': len(t),
                'fig_index': k,
                'fig_total': len(rows_fig),
                'pages': list(range(a, b + 1)),
            })
        lines.append('')
    if not inv:
        lines.append('（本次材料为空）')
    return '\n'.join(lines), inv


def render_user(user_tmpl, deck, page, win, pages, materials, parent_pages):
    path = pages.get(page) or []
    pp = [int(x) for x in (parent_pages or [])]
    return (user_tmpl
            .replace('{deck}', deck)
            .replace('{cur_page}', str(page))
            .replace('{path}', ' > '.join(path))
            .replace('{leaf_name}', path[-1] if path else '')
            .replace('{window_pages}', '、'.join(str(x) for x in win))
            .replace('{parent_pages}', '、'.join(str(x) for x in pp) if pp else '（无）')
            .replace('{materials}', materials))


COPY_ABS = 40


COPY_RATIO = 0.60


_SENT_RE = re.compile(r"[^。！？；\n]+[。！？；]?")


_NUM_RE = re.compile(r"\d+(?:\.\d+)?")


_EN_RE = re.compile(r"[A-Za-z][A-Za-z0-9\-\.]{1,}")


COPY_ABS = 40


COPY_RATIO = 0.60


_SENT_RE = re.compile(r"[^。！？；\n]+[。！？；]?")


_NUM_RE = re.compile(r"\d+(?:\.\d+)?")


_EN_RE = re.compile(r"[A-Za-z][A-Za-z0-9\-\.]{1,}")


COPY_ABS = 40


COPY_RATIO = 0.60


_SENT_RE = re.compile(r"[^。！？；\n]+[。！？；]?")


_NUM_RE = re.compile(r"\d+(?:\.\d+)?")


_EN_RE = re.compile(r"[A-Za-z][A-Za-z0-9\-\.]{1,}")


def normalize_pages(raw_pages, win, cur):
    """校验模型给的 `pages`，返回 (pages, rejected_reason)。

规则（提示词里写了，这里必须真的执行）：
  - 升序、**连续**、**含当前页**、不越出窗口；
  - 取**包含当前页的最长连续段**：C-0045 起当前页未必是窗口第 1 页（上级
    节点页排在它前面），模型写 [4,5,6] 而当前页是 6 时必须整段保留——旧版
    从"当前页起向后看断号"，会把 p.4/p.5 悄悄剔掉，那是丢内容的复发路径；
  - 长度上限 = 窗口长度（窗口本身已按"末级页 ≤ MAX_MERGE"构造好）；
  - 任何一条不满足 → 降级为合法前缀，取不出来就退回 `[cur]`，
    并把原因写进 rejected_reason（**不静默合并**）。
"""
    bad = []
    ps = []
    for x in (raw_pages if isinstance(raw_pages, (list, tuple)) else []):
        try:
            n = int(x)
        except (TypeError, ValueError):
            bad.append(f"有非整数页号 {x!r}")
            continue
        if n not in ps:
            ps.append(n)
    ps.sort()
    if not ps:
        return [cur], "模型没给出 pages，按当前页单页处理"
    if cur not in ps:
        bad.append("pages %s 不含当前页 p.%d" % (ps, cur))
    outside = [p for p in ps if p not in win]
    if outside:
        bad.append(f"pages {outside!s} 越出可合并范围 {win!s}")
    keep = [p for p in ps if p in win]
    if cur not in keep:
        keep = [cur]
    i = keep.index(cur)
    lo = i
    while lo - 1 >= 0 and keep[lo - 1] == keep[lo] - 1:
        lo -= 1
    hi = i
    while hi + 1 < len(keep) and keep[hi + 1] == keep[hi] + 1:
        hi += 1
    seq = keep[lo:hi + 1]
    if len(seq) < len(keep):
        bad.append("pages %s 不连续，以当前页 p.%d 为中心只取连续段 %s" % (keep, cur, seq))
    if len(seq) > len(win):
        bad.append(
            "一次覆盖 %d 页，超过窗口上限 %d 页，只取 %s"
            % (len(seq), len(win), seq[:len(win)])
        )
        seq = seq[:len(win)] if cur in seq[:len(win)] else seq[-len(win):]
    return seq, "；".join(bad)


def cost_of(usage: dict) -> float:
    ti = int(usage.get("prompt_tokens") or 0)
    to = int(usage.get("completion_tokens") or 0)
    return round((ti / 1e6) * PRICE_IN * FX + (to / 1e6) * PRICE_OUT * FX, 6)


def parse_json(raw: str) -> dict:
    """剥代码块 + 截取最外层 {}（与 `core/outline.py::_parse` 同一套路）。"""
    s = (raw or "").strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", s, re.S)
    if m:
        s = m.group(1).strip()
    i, j = s.find("{"), s.rfind("}")
    if i >= 0 and j > i:
        s = s[i:j + 1]
    try:
        return json.loads(s)
    except Exception as e:  # noqa: BLE001
        return {"_parse_error": "%s: %s" % (type(e).__name__, e), "_raw": raw}


# 照抄判定：与 C-0031/C-0032/C-0034 **同一判据**


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


def lcs_len(a, b):
    """最长公共**子串**长度（二分 + 集合）。"""
    if not a or not b or not (set(a) & set(b)):
        return 0
    lo, best, hi = 1, 0, min(len(a), len(b))
    while lo <= hi:
        mid = (lo + hi) // 2
        grams = {b[i:i + mid] for i in range(len(b) - mid + 1)}
        if any(a[i:i + mid] in grams for i in range(len(a) - mid + 1)):
            # C-0104 订正：恢复版写成 `mid, mid + 1`，二分左界不前进 ⇒
            # 命中即死循环；字节码真值（STORE_FAST_STORE_FAST lo, best）
            # 与 pyc 原生实跑一致的都是这一版。
            lo, best = mid + 1, mid
        else:
            hi = mid - 1
    return best


def _lcs_piece(a, b, n):
    grams = {b[i:i + n] for i in range(len(b) - n + 1)}
    for i in range(len(a) - n + 1):
        if a[i:i + n] in grams:
            return a[i:i + n]
    return ""


def quality_issues(summary, points, material):
    out = (summary or "") + "\n" + "\n".join(points or [])
    mat = _norm(material)
    low = (material or "").lower()
    copy = []
    for s in _SENT_RE.findall(_norm(summary or "")):
        s = s.strip()
        if len(s) < 8:
            continue
        n = lcs_len(s, mat)
        if n >= COPY_ABS and n >= COPY_RATIO * len(s):
            copy.append({
                "sentence_chars": len(s),
                "same_chars": n,
                "piece": _lcs_piece(s, mat, n),
            })
    num_bad = sorted({t for t in _NUM_RE.findall(out) if t not in mat})
    en_bad = sorted({t for t in _EN_RE.findall(out) if t.lower() not in low})
    return {
        "copy": copy,
        "numbers_not_in_material": num_bad,
        "terms_not_in_material": en_bad,
        "n": len(copy) + len(num_bad) + len(en_bad),
    }


def material_text_of(inv, ch):
    # C-0111 连带修复：真值（v1.1 原始字节码同函数 + genexpr）逐字为
    #   return _norm("".join(ch['texts'].get(m['chunk_id'], '') for m in inv))
    # 旧重建版写成 `"".join(ch for ch in inv)`：生成器变量把入参 ch 遮蔽掉了，
    # 且 join 的是 inv 里的 **dict**，非空材料必抛
    # TypeError: sequence item 0: expected str instance, dict found —— 它就挡在
    # _gen_one 的 `mat = material_text_of(inv, ch)` 上（quality_issues 之前）。
    return _norm("".join(ch['texts'].get(m['chunk_id'], '') for m in inv))


def cover_material(inv, kp_pages):
    """从窗口材料里挑出「这条知识点覆盖的页」的块（跨页块按它覆盖的页算）。"""
    # C-0111 连带修复：真值（v1.1 原始字节码同函数）是
    #   pgs = set(int(p) for p in (kp_pages or []))   ← genexpr 的 varnames 是 ['p']、
    #                                                    co_names 含 'int'
    # 旧重建版把入参 kp_pages 当成「dict 列表」遍历（m.get('pages')）——但两处调用点
    # （_gen_one 与 restamp_material_scope）传进来的都是**页号列表**，必抛
    # AttributeError: 'int' object has no attribute 'get'，正好挡在 _gen_one 的
    # `_apply_material(rec, cover_material(...), inv)` 与 return 之间。
    pgs = set(int(p) for p in (kp_pages or []))
    return [m for m in inv if set(m.get('pages') or [m.get('page')]) & pgs]


def _apply_material(rec, inv_cover, inv_window):
    """把**两套材料口径**写进一条产物（口径单一，别处不要各自拼）。

    - **覆盖口径**（`material` / `material_block_ids` / 各种计数）：这条知识点
      `pages` 覆盖的那些页的块。产物对外一律用这一份——"每条知识点的材料 =
      它覆盖的全部页的正文块 + 识图块"，人拿 `pages` 回查材料必须对得上。
    - **窗口口径**（`merge.window_block_ids` / `merge.window_material_chars`）：
      这一次**真的喂给模型**的块（含后面几页——模型要靠它判断要不要合并）。
      两种口径都留痕：对齐与可审计性都要，不能只留一个。
    """
    rec["material"] = [
        {
            "chunk_id": m["chunk_id"],
            "kind": m["kind"],
            "ref": m["ref"],
            "chars": m["chars"],
            "page": m["page"],
            "pages": m.get("pages") or [m["page"]],
        }
        for m in inv_cover
    ]
    rec["material_block_ids"] = [m["chunk_id"] for m in inv_cover]
    rec["material_chars"] = sum(m["chars"] for m in inv_cover)
    rec["n_material_blocks"] = len(inv_cover)
    rec["n_body_blocks"] = sum(1 for m in inv_cover if m["kind"] != "图片理解")
    rec["n_fig_blocks"] = sum(1 for m in inv_cover if m["kind"] == "图片理解")
    rec["figure_pages"] = sorted({m["page"] for m in inv_cover if m["kind"] == "图片理解"})
    rec["used_figures"] = bool(rec["n_fig_blocks"])
    mg = rec.setdefault("merge", {})
    mg["window_block_ids"] = [m["chunk_id"] for m in inv_window]
    mg["window_material_chars"] = sum(m["chars"] for m in inv_window)
    mg["cover_rule"] = "material* = pages 覆盖页的全部块（正文+识图）；merge.window_* = 本次真正注入模型的窗口块"


def restamp_material_scope(recs, pages, ch):
    """按覆盖口径**重算**已有条目的 material 字段（纯本地，**不调模型**）。

    产物可能是更早的口径写下的（`material` 记的是整个窗口的块），续跑时统一
    刷新一次，免得同一份文件里两种口径混着——那正是机器校验会抓的不一致。
    """
    n = 0
    for r in recs:
        pgs = [int(x) for x in (r.get("pages") or [])]
        if not pgs:
            continue
        win = [int(x) for x in ((r.get("merge") or {}).get("window") or pgs)]
        _m, inv = render_materials(win, pages, ch)
        before = list(r.get("material_block_ids") or [])
        _apply_material(r, cover_material(inv, pgs), inv)
        if before != r["material_block_ids"]:
            n += 1
    return n


def _load_prev(out: Path) -> dict:
    try:
        d = json.loads(out.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def resume_set(prev: dict) -> set:
    """能从旧产物续跑的页集合。

    ⚠ **只认本版产物**：C-0034 的旧文件是"一页一条、没有合并"的口径，
    拿它当"已完成"会把整份课件误判成跑完了（那就等于什么都没做）。
    判据是 `meta.pipeline_version`，旧文件缺这个字段 → 返回空集合（重跑）。

    ⚠ **重叠即整份作废**：正常产物里每页最多属于一条知识点（游标会跳过被合并
    掉的页）。若两条的 `pages` 有交集，说明这份产物出自有缺陷的中间版本
    （实测有过：合并后没跳过已覆盖页，跑出 [4,5,6] 后面又跟着 [5,6,7]）。
    这种产物**一条都不续**——宁可重花钱，也不把脏数据当基线往下接。
    """
    meta = prev.get("meta") if isinstance(prev.get("meta"), dict) else {}
    if str(meta.get("pipeline_version") or "") != PIPELINE_VERSION_V1:
        return set()
    done: set = set()
    seen: dict = {}
    for r in (prev.get("knowledge_points") or []):
        if not isinstance(r, dict) or not str(r.get("summary") or "").strip():
            continue
        for p in (r.get("pages") or []):
            try:
                n = int(p)
            except (TypeError, ValueError):
                continue
            if n in seen and seen[n] != r.get("id"):
                return set()      # 重叠 → 整份产物不可信
            seen[n] = r.get("id")
            done.add(n)
    return done


_STATE = {
    "stages": list(KP_STAGES),
    "stage_index": 0,
    "running": False,
    "doc_id": 0,
    "kp_done": 0,
    "kp_total": 0,
    "cost_cny": 0.0,
    "lock": threading.Lock(),
}


_STATE_LOCK = threading.Lock()

# 卡 C-0124：**已观测到的单次调用耗时**，用来在界面上给出"还要多久"的量级，
# 而不是让进度条干杵着（用户 2026-10-15 那次单次调用实测 914 秒，界面 15 分钟
# 一动不动，看着就是"卡住"）。只留最近 40 次，取中位数（抗离群）。
_DUR_LOCK = threading.Lock()

_DUR_OBS: dict = {"knowledge": [], "structure": [], "other": []}


def _dur_bucket(kind: str) -> str:
    k = str(kind or "")
    if "knowledge" in k:
        return "knowledge"
    if "struct" in k:
        return "structure"
    return "other"


def observe_call_seconds(kind: str, seconds: float) -> None:
    """记一次模型调用的耗时（只用于进度展示，不参与记账/预算）。"""
    try:
        v = float(seconds)
    except (TypeError, ValueError):
        return
    if v <= 0:
        return
    with _DUR_LOCK:
        b = _DUR_OBS.setdefault(_dur_bucket(kind), [])
        b.append(v)
        if len(b) > 40:
            del b[:-40]
        _DUR_OBS["last_at"] = time.time()


def observed_call_seconds(kind: str = "knowledge") -> float:
    """同类调用的**中位耗时**（秒）；没有样本返回 0。"""
    with _DUR_LOCK:
        b = list(_DUR_OBS.get(_dur_bucket(kind)) or [])
        last = float(_DUR_OBS.get("last_at") or 0.0)
    if not b:
        return 0.0
    return round(sorted(b)[len(b) // 2], 1)


def seconds_since_last_call() -> float:
    """距最近一次模型调用返回过去了多久（秒）；没观测过返回 0。"""
    with _DUR_LOCK:
        last = float(_DUR_OBS.get("last_at") or 0.0)
    return round(time.time() - last, 1) if last else 0.0


def out_file(doc_id: int, out_path: str | os.PathLike | None = None) -> Path:
    """产物路径。`out_path` 为空 —— 或虽非空但**没有文件名** —— 时回退到默认产物。

    [C-0120] 这是"空名路径"的第二道闸。`Path("")` 在 Windows 上等于 `Path(".")`，
    而 `_save()` 的原子落盘第一步就是 `out.with_suffix(out.suffix + ".tmp")`，
    对 `Path(".")` 会抛 `ValueError: WindowsPath('.') has an empty name` ——
    崩点在**模型调用全部结束之后**（C-0120 实测 traceback：build L5221 →
    build_v2 L5005 → persist L4950 → _save L1403），也就是钱已经花了、产物却没
    落地。调用侧（`core/kp_batch.py::plan/_blank_task/_run_one`）已按 v1.1 字节码
    真值补上 `out_path`，这里再挡一道：`"."`/`".."` 和空串一样**没有文件名**，
    都不可能落盘，一律按"没给"处理。
    """
    p = str(out_path or "").strip()
    if p and p not in (".", ".."):
        return Path(p)
    return config.LOG_DIR / "knowledge" / ("%d%s" % (int(doc_id), KP_SUFFIX))


def prev_info_v1(doc_id, out_path):
    """已有产物的状态（给看板判断"要不要先备份、能不能续跑"）。

    看板点「开始」前必须知道三件事：产物在不在、是不是本版（本版才能续跑）、
    里面已经有多少条。旧版产物（如 C-0034 的"一页一条"）**会被整份重写**，
    所以调用方要先备份——这个判断只有这里算得准，不要在前端猜。

    ⚠ 卡 C-0049：本函数是 **v1（读 outline）那代**的口径，保留供对照；
    对外入口 `prev_info()` 现在走 v2。
    """
    out = out_file(doc_id, out_path)
    prev = _load_prev(out)
    meta = prev.get("meta") if isinstance(prev.get("meta"), dict) else {}
    ver = str(meta.get("pipeline_version") or "")
    kps = [r for r in (prev.get("knowledge_points") or []) if isinstance(r, dict)]
    done = resume_set(prev)
    return {
        "exists": out.exists(),
        "path": str(out),
        "pipeline_version": ver,
        "is_current_version": ver == PIPELINE_VERSION_V1,
        "n_kps": len(kps),
        "n_done_pages": len(done),
        "resumable": bool(done),
        "pages_total": int(meta.get("pages_total") or 0),
        "cost_cny": float(meta.get("cost_cny_all_calls") or 0.0),
        "generated_at": str(meta.get("generated_at") or ""),
        "source_card": str(meta.get("source_card") or ""),
    }


def estimate_v1(doc_id, outline_path, prompt_path, db_path):
    """dry-run：列出末级页/窗口/材料规模与**预估**条数、成本，**不调模型**。

    ⚠ 卡 C-0049：本函数是 **v1（读 `logs/outline/<id>.json`）那代**的口径，
    保留供对照与回退（CLI `--pipeline v1`）；对外入口 `estimate()` 现在走 v2。

    预估是**上界**（按"每页一条、不合并"算），所以真实花费只会更少：
    合并掉了的页不会产生单独一次调用。这里的口径写清楚，免得看板把上界
    当承诺——成本硬上限在 `build(max_cost=...)` 那边真的拦。
    """
    prompt = load_prompts(prompt_path)
    pages = outline_pages(doc_id, outline_path)
    raw = load_outline(doc_id, outline_path)
    if not pages:
        return {
            "doc_id": int(doc_id),
            "ok": False,
            "error": "该文档没有结构数据（logs/outline/%d.json 不存在或为空）" % int(doc_id),
        }
    leaf, parent = split_leaf_pages(pages)
    handoff, owner, unplaced = handoff_map(pages, leaf, parent)
    st = _page_stats(doc_id, db_path)
    if st["bad"]:
        return {
            "doc_id": int(doc_id),
            "ok": False,
            "error": "有 %d 个块的 source_ref 解析不出页码（拒绝静默丢块）：%s"
            % (len(st["bad"]), st["bad"][:5]),
        }
    per = st["per_page"]
    leaf_set = set(leaf)
    windows = [window_of(p, leaf_set, handoff, owner) for p in leaf]
    win_union = {x for w in windows for x in w}
    unplaced_pages = {int(p) for x in unplaced for p in (x.get("pages") or [])}
    window_unclaimed = []
    for p in sorted(set(pages) - win_union - unplaced_pages):
        host = int(owner.get(p) or 0)
        win_host = window_of(host, leaf_set, handoff, owner) if host else []
        if p in leaf_set:
            reason = "末级页却不在自己的窗口里（复算口径异常，请核对）"
        elif not host:
            reason = "父节点页且没有宿主末级页（`handoff_unplaced` 之外的情形）"
        else:
            reason = (
                "父节点页下放给 p.%d，宿主窗口 %s 里没有它——窗口总页数上限 MAX_WINDOW_PAGES=%d（下放页优先，塞不下的记在这里，不静默丢）"
                % (host, win_host or "（空）", MAX_WINDOW_PAGES)
            )
        window_unclaimed.append(
            {
                "page": p,
                "role": "leaf" if p in leaf_set else "parent",
                "owner": host,
                "host_window": win_host,
                "reason": reason,
            }
        )

    per_all = []
    for p in sorted(pages):
        d = per.get(p) or {"chars": 0, "n_body": 0, "n_fig": 0}
        per_all.append(
            {
                "page": p,
                "role": "leaf" if p in leaf_set else "parent",
                "chars": d["chars"],
                "n_body": d["n_body"],
                "n_fig": d["n_fig"],
            }
        )
    mat_chars = sum(d["chars"] for d in per_all)
    win_chars = sum(sum(per_all[p]["chars"] for p in w) for w in windows)
    calls = len(leaf)
    est_in = int((prompt["chars"] + win_chars) / CHARS_PER_TOKEN)
    est_out = calls * EST_OUT_TOKENS
    est_cost = round(
        est_in / 1000000.0 * PRICE_IN * FX + est_out / 1000000.0 * PRICE_OUT * FX, 6
    )
    out = out_file(doc_id)
    prev = _load_prev(out)
    done = resume_set(prev)
    return {
        "doc_id": int(doc_id),
        "ok": True,
        "title": doc_title(doc_id),
        "deck": str(raw.get("deck") or ""),
        "n_pages": len(pages),
        "n_leaf_pages": len(leaf),
        "excluded_parent_pages": parent,
        "leaf_pages": leaf,
        "handoff": {str(k): v for k, v in sorted(handoff.items()) if v},
        "handoff_pages": sorted(p for v in handoff.values() for p in v),
        "handoff_detail": [
            {
                "host": int(h),
                "host_path": list(pages.get(h) or []),
                "parents": [
                    {"page": int(p), "path": list(pages.get(p) or [])} for p in v
                ],
            }
            for h, v in sorted(handoff.items())
            if v
        ],
        "handoff_unplaced": unplaced,
        "window_unclaimed": window_unclaimed,
        "window_claimed_pages": sorted(win_union),
        "page_owner": {str(k): v for k, v in sorted(owner.items())},
        "max_window_pages": MAX_WINDOW_PAGES,
        "windows": {str(p): w for p, w in zip(leaf, windows)},
        "kp_max": len(leaf),
        "kp_min": (len(leaf) + MAX_MERGE - 1) // MAX_MERGE,
        "max_merge_pages": MAX_MERGE,
        "material_chars_total": mat_chars,
        "window_chars_total": win_chars,
        "pages": per_all,
        "est_calls": calls,
        "est_tokens_in": est_in,
        # TODO(反编译存疑): 字节码在此处截断，以下字段按常量表与上下文推断
        "est_tokens_out": est_out,
        "est_cost_cny": est_cost,
        "est_rule": "上界估算：按每页一条（不合并）算调用数；输入 token ≈ (提示词 + 窗口材料字符)/%.1f；输出按每条 %d token；单价非高峰 $%.2f/1M 入、$%.2f/1M 出，汇率 %.1f"
        % (CHARS_PER_TOKEN, EST_OUT_TOKENS, PRICE_IN, PRICE_OUT, FX),
        "already_done_pages": sorted(done),
        "already_out": out,
        "prompt_path": prompt_path,
        "path": str(out),
        "prompt_sha256": hashlib.sha256(
            Path(prompt_path).read_bytes()
        ).hexdigest(),
    }


def _stage(i: int, note: str = "", on: bool = True) -> None:
    """切到自己的第 i 个阶段（同时把阶段名发给 `core/progress.py`）。

阶段条只能**单调向前**：调用方在循环里不再来回切阶段，否则进度条会闪。

⚠ `on=False`（批量整理，卡 C-0038）时**连本模块的 `_STATE` 也不写**：
模块级 `_STATE` 是"单份任务"的全局位，N 份并发时谁写谁覆盖；
批量任务有自己的 per-task 进度（`core/kp_batch.py`），走 `on_event` 回调。
"""
    if not on:
        return None
    with _STATE_LOCK:
        _STATE['stage_index'] = max(0, min(i, len(KP_STAGES) - 1))
    progress.stage(KP_STAGES[i], note)


_SAVE_LOCK = threading.Lock()


def _emit(on_event, ev: dict) -> None:
    """把进度事件交给调用方（批量整理用）。

    **回调的异常绝不许打断管线**：进度是"看得见的糖"，整理本身才是正事；
    回调里写错一个字段不该让一整份课件的模型调用白花。
    """
    if on_event is None:
        return
    try:
        on_event(ev)
    except Exception:  # noqa: BLE001
        pass


def progress_snapshot() -> dict:
    """给 `/api/kp-build/progress` 的快照：字段与 `/api/ingest/progress` 同构，
    前端可以直接复用同一个进度条渲染函数。

    为什么要把 `stages` / `stage_index` / `percent` 覆盖掉：`core/progress.py`
    的五个阶段是**入库**的，`stage()` 也只认它自己的阶段名。这里用同一份状态
    （label/stage/note/elapsed/error 都来自它），但阶段表换成整理自己的四个，
    百分比按"阶段序号 + 阶段内完成度"重算——**不改 core/progress.py 一个字**。
    """
    s = progress.snapshot()
    with _STATE_LOCK:
        idx = int(_STATE["stage_index"])
        s["stages"] = list(KP_STAGES)
        s["stage_index"] = idx
        s["kind"] = "kp_build"
        s["doc_id"] = int(_STATE["doc_id"])
        s["kp_done"] = int(_STATE["kp_done"])
        s["kp_total"] = int(_STATE["kp_total"])
        s["cost_cny"] = round(float(_STATE["cost_cny"]), 6)
    frac = (s["done"] / s["total"]) if s.get("total") else 0.0
    frac = min(1.0, max(0.0, frac))
    n = len(KP_STAGES)
    s["percent"] = (int(((idx + frac) * 100) / n) if s.get("active")
                    else (100 if s.get("status") == "done" else 0))
    # 卡 C-0124：让"慢"看得见。单次调用实测最慢 914 秒（15 分钟），期间阶段名与
    # 条数都不变 ⇒ 界面看着就是卡死。这里补两个**心跳**字段给前端渲染：
    #   · since_last_call_s：距最近一次调用返回多久（在飞时就是"这一条已经等了多久"）；
    #   · eta_call_s：同类调用的中位耗时（量级参考，不是承诺）。
    s["since_last_call_s"] = seconds_since_last_call()
    s["eta_call_s"] = observed_call_seconds("knowledge")
    s["eta_note"] = ("单次调用已耗时 %s；同类调用中位 %s（模型网关高峰期会更久）"
                     % (_fmt_secs(s["since_last_call_s"]), _fmt_secs(s["eta_call_s"]))
                     if s["since_last_call_s"] > 0 else "")
    return s


def _fmt_secs(v) -> str:
    """秒 → `12 分 34 秒` 这种可读形式（进度文案用）。"""
    try:
        n = int(round(float(v)))
    except (TypeError, ValueError):
        return "—"
    if n <= 0:
        return "—"
    if n < 60:
        return "%d 秒" % n
    m, sec = divmod(n, 60)
    if m < 60:
        return "%d 分 %d 秒" % (m, sec) if sec else "%d 分" % m
    h, m = divmod(m, 60)
    return "%d 小时 %d 分" % (h, m) if m else "%d 小时" % h


STRUCT_PROMPT_NAME = "提示词-思维导图结构.md"


KP_PROMPT_NAME_V2 = "提示词-知识点总结-v2.md"

# 结构生成的分块参数（双阈值：谁先到就切段）。理由写在提示词真源 §0.1：
# 90~100 页课件实测 1.3~1.8 万字，按 6000 字符切恒为 3~4 段 → 结构调用 4~5 次，与页数无关地有上界。


STRUCT_SEG_PAGES = 12


STRUCT_SEG_CHARS = 6000
# 喂给结构模型的**每页字符预算**。做层级判断不需要全文（`core/outline.py` 用的是 220/260），
# 这里给得宽一些（420）是因为 v2 还要给每页起短标题。


STRUCT_PAGE_CHARS = 420


STRUCT_MAX_TOKENS = 3000


STRUCT_RETRY = 1


STRUCT_TEMPERATURE = 0.2
# 一条知识点最多覆盖几页（连续）。v1 的 MAX_MERGE=3 是"模型可合并的上限"，
# v2 里它变成**管线的切片长度**：页归属由结构树唯一决定，模型不再判断合并。


KP_SPAN_PAGES = 3


def is_running() -> bool:
    with _STATE_LOCK:
        return bool(_STATE["running"])


# ---------------------------------------------------------------- 落盘


def _save(doc: dict, out: Path) -> None:
    """原子落盘：先写 .tmp 再 replace，中断不会留下半截 JSON。"""
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, out)


# ---------------------------------------------------------------- 主流程


def _coverage_block(est, recs):
    owner_est = {int(k): int(v) for k, v in (est.get('page_owner') or {}).items()}
    seen = {}
    overlap = []
    for r in recs:
        rid = r.get('id')
        for p in (r.get('pages') or []):
            try:
                n = int(p)
            except (TypeError, ValueError):
                continue
            if n in seen and seen[n] != rid:
                overlap.append(n)
            seen[n] = rid
    pages_all = sorted(owner_est) or sorted(seen)
    unclaimed = est.get('window_unclaimed') or []
    return {
        'pages_all': pages_all,
        'n_covered': len(pages_all),
        'covered': len(seen),
        'uncovered': sorted(seen),
        'window_unclaimed': unclaimed,
        'n_window_unclaimed': len(unclaimed),
        'window_claimed_pages': est.get('window_claimed_pages') or {str(p): owner_est.get(p) for p in pages_all},
        'page_owner_expected': {str(p): seen[p] for p in sorted(seen)},
        'page_owner_actual': sorted(set(overlap)),
        'handoff': est.get('handoff') or {},
        'handoff_pages': est.get('handoff_pages') or [],
        'handoff_unplaced': est.get('handoff_unplaced') or [],
        'rule': '父节点页下放（C-0045）：每页**唯一**归属一个末级页宿主（同节连续优先）；下放页的材料并入该条知识点的窗口，并由管线强制写进它的 pages；overlap_pages 必须为空。C-0048：没被任何窗口认领的页进 window_unclaimed（逐页给原因），下放又确实无法放置的（如末页父节点）进 handoff_unplaced——两种情形都在产物里留痕，不再静默丢弃',
    }


def _parent_nodes(est, recs):
    owner_actual = {}
    for r in recs:
        for p in (r.get("pages") or []):
            try:
                owner_actual[int(p)] = r
            except (TypeError, ValueError):
                continue

    out = []
    for item in (est.get("handoff_detail") or []):
        host = int(item.get("host") or 0)
        rec = owner_actual.get(host) or {}
        for par in (item.get("parents") or []):
            path = list(par.get("path") or [])
            out.append({
                "name": path[-1] if path else ("p.%d" % int(par.get("page") or 0)),
                "path": path,
                "level": len(path),
                "pages": [int(par.get("page") or 0)],
                "host_page": host,
                "host_path": list(item.get("host_path") or []),
                "carried_by": rec.get("id") or "",
                "carried_by_name": rec.get("name") or "",
                "role": "父节点不单独成条：它的页已下放给子知识点，文本只作子知识点的上下文与概括索引",
            })

    # C-0110：原重建版猜的是 x["key"]（列表元素里没有这个键 → KeyError）。
    # 真值 = pages：kp_build.funcs.txt `_parent_nodes.<lambda> args=1 vars: x` +
    # kp_build.strings.txt 该条唯一常量 `pages`（与 persist / merge_decisions 同一处同源错）。
    return sorted(out, key=lambda x: x["pages"])


def _meta(est, prompt, recs, extra, status, started, excluded_note):
    done = [r for r in recs if str(r.get("summary") or "").strip()]
    ti = sum(r.get("tokens_in", 0) for r in recs)
    to = sum(r.get("tokens_out", 0) for r in recs)
    cost = round(sum(r.get("cost_cny", 0.0) for r in recs), 6)
    pages = est.get("leaf_pages") or []
    covered = sorted({p for r in recs for p in (r.get("pages") or [])})
    merged = [r for r in recs if r.get("merged")]
    groups = [[int(p) for p in (r.get("pages") or [])] for r in merged]
    not_merged = [r for r in recs if not r.get("merged")]
    ex = extra or {"calls": 0, "tokens_in": 0, "tokens_out": 0, "cost_cny": 0.0}
    copy_failed = [
        r["id"]
        for r in recs
        if (r.get("quality") or {}).get("n", 0) > 0
    ]
    return {
        "doc_id": int(est["doc_id"]),
        "chapter": est.get("title") or "",
        "deck": est.get("deck") or est.get("title") or "",
        "root_name": (recs[0].get("path") or [""])[0] if recs else "",
        "source_card": "C-0045",
        "pipeline": "C-0045 知识点整理管线（core/kp_build.py）：父节点页下放（上级页并入后继末级页）+ 窗口合并（末级页 ≤%d 页，总页 ≤%d）+ 全中文 + 要点五类精简" % (MAX_MERGE, MAX_WINDOW_PAGES),
        "pipeline_version": PIPELINE_VERSION_V1,
        "prompt_source": "logs/knowledge/提示词-知识点总结.md（唯一真源）",
        "prompt_sha256": prompt["sha256"],
        "outline_source": "logs/outline/%d.json" % int(est["doc_id"]),
        "model": MODEL,
        "params": {
            "thinking": THINKING,
            "temperature": TEMPERATURE,
            "max_tokens": MAX_TOKENS,
        },
        "price_rule": "非高峰 $%.2f/1M 入 + $%.2f/1M 出，汇率 %.1f（同 core/outline.py）" % (PRICE_IN, PRICE_OUT, FX),
        "kp_total": len(pages),
        "kp_done": len(done),
        "kp_actual": len(recs),
        "kp_insufficient": sorted(r["id"] for r in recs if (r.get("quality") or {}).get("n", 0) > 0),
        "pages_total": len(pages),
        "pages_covered": covered,
        "pages_total_all": len(est.get("pages") or []),
        "pages_pending": [p for p in pages if p not in set(covered)],
        "excluded_parent_pages": est.get("excluded_parent_pages") or [],
        "excluded_reason": excluded_note,
        "coverage": _coverage_block(est, recs),
        "unclaimed_pages": est.get("window_unclaimed") or [],
        "excluded_unplaced_pages": est.get("handoff_unplaced") or [],
        "parent_nodes": _parent_nodes(est, recs),
        "merge_summary": {
            "max_merge_pages": MAX_MERGE,
            "n_kps": len(recs),
            "n_merged_kps": len(merged),
            "n_single_kps": len(not_merged),
            "merged_groups": groups,
            "merged_pages_total": sum(len(g) for g in groups),
            "pages_saved": sum(len(g) - 1 for g in groups),
            "single_pages": [int((r.get("pages") or [0])[0]) for r in not_merged],
        },
        "copy_check": {
            "rule": "整句照抄＝与材料连续相同 ≥%d 字 且 占该句 ≥%.0f%%；数字/专名＝输出里每个数字串与英文 token 必须能在材料里找到" % (COPY_ABS, COPY_RATIO * 100),
            "checked": len(recs),
            "passed": len(recs) - len(copy_failed),
            "failed_ids": copy_failed,
        },
        "cost_budget_note": "硬上限由调用方（CLI --max-cost / 看板）传入，见 budget_cny",
        "budget_cny": float(est.get("budget_cny") or 0.0),
        "cost_cny": cost,
        "usage_total": {
            "calls": len(recs),
            "tokens_in": ti,
            "tokens_out": to,
        },
        "usage_extra": dict(ex, note="重试/失败等未留在产物里的调用"),
        "est_cost_cny": round(cost + float(ex.get("cost_cny") or 0), 6),
        "usage_total_all_calls": {
            "calls": len(recs) + int(ex.get("calls") or 0),
            "tokens_in": ti + int(ex.get("tokens_in") or 0),
            "tokens_out": to + int(ex.get("tokens_out") or 0),
        },
        "elapsed_s": round(time.perf_counter() - started, 1),
        "status": status,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "note": "每条知识点 = 一段**连续页**：下放给它的上级节点页（若有）+ 当前末级页 + 后续至多 %d 个末级页（由模型按信息密度与主题连续性判断合并，本模块逐条校验 pages，并把下放的上级页强制并入）；材料 = 覆盖页的全部正文块 + 识图块（图片理解），不重新调用视觉模型；总结为提炼后的中文正文与结构/概念/过程/原因/结果类要点，不贴原文。material 逐条列出实际注入的块。" % MAX_MERGE,
        "stages": list(KP_STAGES),
    }


def merge_decisions(recs, est, ch):
    per = {int(x["page"]): x for x in (est.get("pages") or [])}
    out = []
    for r in sorted(recs, key=lambda x: x["pages"]):  # C-0110：键名 pages（见 persist 处的真值说明）
        pgs = [int(p) for p in (r.get("pages") or [])]
        win = [int(p) for p in ((r.get("merge") or {}).get("window") or pgs)]
        ev = []
        for p in win:
            d = per.get(p) or {}
            ev.append({
                "page": p,
                "chars": d.get("chars", 0),
                "n_body": d.get("n_body", 0),
                "n_fig": d.get("n_fig", 0),
                "in_this_kp": p in pgs,
            })
        out.append({
            "id": r.get("id"),
            "name": r.get("name"),
            "pages": pgs,
            "merged": bool(r.get("merged")),
            "merge_reason": str(r.get("merge_reason") or ""),
            "window": win,
            "density_evidence": ev,
            "rejected_reason": str((r.get("merge") or {}).get("rejected_reason") or ""),
        })
    return out


def build_v1(
    doc_id,
    *,
    outline_path=None,
    prompt_path=None,
    out_path=None,
    db_path=None,
    dry_run=False,
    limit=0,
    max_cost=0.0,
    force=False,
    progress_on=True,
    on_event=None,
    log=print,
):
    started = time.perf_counter()
    est = estimate(doc_id, outline_path, prompt_path, db_path=db_path)
    if not est.get("ok"):
        return {"ok": False, "error": est.get("error"), "doc_id": int(doc_id)}
    prompt = load_prompts(prompt_path)
    pages = outline_pages(doc_id, outline_path)
    leaf, parent = split_leaf_pages(pages)
    handoff, owner, unplaced = handoff_map(pages, leaf, parent)
    ch = read_chunks(doc_id, db_path)
    out = out_file(doc_id, out_path)
    est["budget_cny"] = float(max_cost or 0.0)
    unclaimed = est.get("window_unclaimed") or []
    if parent:
        # C-0110：真值把各宿主的「下放页列表」**展平**后再排序——kp_build.funcs.txt 里
        # 这处 genexpr 是 `vars: .0, v, p`（两层循环）。原重建版漏了内层，
        # 得到 list of list，紧接着的 `p in claimed`（set 成员测试）必然
        # TypeError: unhashable type: 'list'（只要有父节点页就走这里）。
        hp = sorted(p for v in handoff.values() for p in v)
        claimed = {int(x) for x in (est.get("window_claimed_pages") or [])}
        hp_in = [p for p in hp if p in claimed]
        excluded_note = "以下 %d 页在导图里还有下级节点（父节点页），**自己不单独成条**，已下放给紧随其后的末级页，内容并入那条知识点并计入其 pages：%s%s%s" % (
            len(parent),
            "、".join(str(p) for p in (hp_in or hp)),
            ("；另 %d 页下放后与宿主相距过远、连宿主的新窗口都塞不下，记为未认领（见 unclaimed_pages / coverage.window_unclaimed，逐页有原因）：%s" % (len(unclaimed), "、".join(str(p) for p in unclaimed))) if unclaimed else "",
            ("；未下放：%s" % "、".join(str(p) for p in unplaced)) if unplaced else "",
        )
    else:
        excluded_note = "本课件没有父节点页：全部 %d 页的路径互不为前缀，页页都是末级节点" % len(pages)
    if dry_run:
        return {"ok": True, "dry_run": True, **est}
    leaf_set = set(leaf)
    prev = {} if force else _load_prev(out)
    done_pages = resume_set(prev)
    recs = []
    if done_pages:
        for r in (prev.get("knowledge_points") or []):
            if not isinstance(r, dict):
                continue
            if not str(r.get("summary") or "").strip():
                continue
            try:
                if int((r.get("pages") or [0])[0]) in done_pages:
                    recs.append(r)
            except (TypeError, ValueError):
                continue
        log("续跑：产物里已有 %d 条（覆盖 %d 页），跳过这些页" % (len(recs), len(done_pages)))
    elif prev:
        if str((prev.get("meta") or {}).get("pipeline_version") or "") != PIPELINE_VERSION_V1:
            log(f"提示：已有产物 {out.name} 不是本版管线（{PIPELINE_VERSION_V1}）产出的，按「全新生成」处理；如需留档请先备份")
        else:
            log("提示：已有产物 %s 是本版但**不可续跑**（条目 pages 相互重叠，出自有缺陷的中间版本），整份重新生成；如需留档请先备份" % out.name)
    if recs:
        n_fix = restamp_material_scope(recs, pages, ch)
        if n_fix:
            log("口径刷新：%d 条的 material 字段已按「覆盖页」重算（未调模型）" % n_fix)
    covered = {p for r in recs for p in (r.get("pages") or [])}
    todo = [p for p in leaf if p not in covered]
    covered_now = set(covered)
    if progress_on:
        progress.reset()
        progress.begin("整理知识点：%s（doc#%d）" % (est.get("title") or "", doc_id), files_total=1)
        _stage(0, note="读取 logs/outline 与真库块")
        with _STATE_LOCK:
            _STATE.update({
                "running": True,
                "doc_id": int(doc_id),
                "kp_done": len(recs),
                "kp_total": len(leaf),
                "cost_cny": 0.0,
            })
    else:
        _emit(on_event, {"ev": "stage", "stage": KP_STAGES[0], "stage_index": 0, "note": "读取 logs/outline 与真库块"})
        _emit(on_event, {"ev": "begin", "kp_done": len(recs), "kp_total": len(leaf)})
    _stage(1, note="构建窗口材料（%d 页待整理）" % len(todo), on=progress_on)
    _emit(on_event, {"ev": "stage", "stage": KP_STAGES[1], "stage_index": 1, "note": "构建窗口材料（%d 页待整理）" % len(todo)})
    extra = {"calls": 0, "tokens_in": 0, "tokens_out": 0, "cost_cny": 0.0}
    spent = 0.0
    status = "done"
    err = ""

    def persist():
        # C-0110 回填：真值 = _work/rebuilt/kp_build/0049-build_v1.persist.py
        # （kp_build.funcs.txt：build_v1.persist args=0 vars: doc；其 <lambda> args=1 vars: r
        #  且 kp_build.strings.txt 该条唯一常量是 pages ⇒ 排序键是 rec["pages"]，
        #  重建分块里写的 r["key"] 是猜的，与 _parent_nodes.<lambda> 那处同源）
        doc = {
            "meta": _meta(est, prompt, recs, extra, status, started, excluded_note),
            "knowledge_points": sorted(recs, key=lambda r: r["pages"]),
            "merge_decisions": merge_decisions(recs, est, ch),
        }
        with _SAVE_LOCK:
            _save(doc, out)

    try:
        made = 0
        for i, p in enumerate(todo, 1):
            if p in covered_now:
                continue
            if limit and made >= limit:
                status = "stopped"
                log("已达 --limit %d 条上限，停止（剩余 %d 页下次续跑）" % (limit, len(todo) - i + 1))
                break
            if max_cost and spent >= float(max_cost):
                status = "stopped"
                log("★累计 CNY%.6f 已达硬上限 CNY%.2f：停止（剩余 %d 页下次续跑）" % (spent, float(max_cost), len(todo) - i + 1))
                break
            # TODO(反编译存疑): 以下循环体字节码被截断，仅按可见常量与全局名推断
            win = window_of(p, todo, leaf_set, MAX_WINDOW_PAGES)
            own = [p]
            carry = []
            for h in hp:
                if p in (handoff.get(h) or []):
                    carry.append(h)
            render_win = win
            materials = render_materials(ch, render_win, carry)
            inv = render_user(prompt, materials)
            user = inv
            try:
                # C-0111 回填：真值实参序列来自 v1.1 原始字节码
                # （C:\Users\Lenovo\StudyDashboard.exe_extracted\PYZ.pyz_extracted\core\kp_build.pyc
                #  的 build_v1 内 CALL_KW 窗口，逐条 LOAD 对上）：
                #   LOAD p, render_win, materials, inv, user, prompt, pages, ch, est, log（10 个位置参）
                #   + KW_NAMES ('own_pages','carry_pages') ⇒ own_pages=own, carry_pages=carry。
                # 旧重建版写成 _gen_one(p, user, deck=…, parent_pages=carry)：2 个位置参 + 2 个
                # 不存在的关键字 ⇒ TypeError 被下面 except 吞成「★失败 p.x」，v1 一条都生成不出来。
                rec = _gen_one(p, render_win, materials, inv, user, prompt, pages, ch, est, log,
                               own_pages=own, carry_pages=carry)
            except Exception as e:
                log("★失败 p.%d：%s: %s" % (p, type(e).__name__, e))
                _emit(on_event, {"ev": "log", "msg": "★失败 p.%d：%s: %s" % (p, type(e).__name__, e)})
                continue
            usage = rec.get("usage") or {}
            extra["calls"] += 1
            extra["tokens_in"] += int(usage.get("tokens_in") or 0)
            extra["tokens_out"] += int(usage.get("tokens_out") or 0)
            extra["cost_cny"] += float(rec.get("cost_cny") or 0.0)
            spent += float(rec.get("cost_cny") or 0.0)
            recs.append(rec)
            made += 1
            covered_now.update(rec.get("pages") or [])
            persist()
            _emit(on_event, {"ev": "tick", "kp_done": len(recs), "kp_total": len(leaf), "cost_cny": spent, "note": ""})
            # C-0110：格式串（kp_build.strings.txt [build_v1] len=69，逐字为真）有 10 个占位符，
            # 原重建版只给了 9 个参数 → 生成第一条就 "not enough arguments for format string"。
            # 缺的是「%d 字总结」那个数（对照 build_v2._accept_rec 的同款日志：先名字后字数）。
            log("[%2d/%2d] p.%-8s → %s %s · %s · %d 字总结 / %d 要点 · CNY%.6f · 累计 CNY%.6f" % (
                i, len(todo), p, rec.get("name") or "", rec.get("id") or "",
                rec.get("summary") or "", len(rec.get("summary") or ""),
                len(rec.get("points") or []),
                float(rec.get("cost_cny") or 0.0), spent,
            ))
    except Exception as e:
        status = "failed"
        err = "%s: %s" % (type(e).__name__, e)
        log("★整理中止：%s" % err)
    try:
        persist()
    except Exception as e:
        status = "failed"
        err = "%s: %s" % (type(e).__name__, e)
        log("★落盘失败：%s" % err)
    if progress_on:
        with _STATE_LOCK:
            _STATE.update({"running": False, "error": err})
    _emit(on_event, {"ev": "end", "status": status, "error": err, "cost_cny": spent})
    log("完成：%d 条（覆盖 %d/%d 页 = 下放后的**全部页**；末级节点 %d 个）· 合并 %d 条 / 单页 %d 条 · 用时 %.0fs" % (
        len(recs), len(covered_now), len(pages), len(leaf),
        sum(1 for r in recs if len(r.get("pages") or []) > 1),
        sum(1 for r in recs if len(r.get("pages") or []) <= 1),
        time.perf_counter() - started,
    ))
    log("真实开销：CNY%.6f（入 %d / 出 %d token）· 产物 %s" % (
        extra["cost_cny"], extra["tokens_in"], extra["tokens_out"], out,
    ))
    return {
        "ok": status == "done",
        "status": status,
        "error": err,
        "out_path": str(out),
        "meta": {
            "kp_actual": len(recs),
            "pages_covered": len(covered_now),
            "pages_total_all": len(pages),
            "pages_total": len(leaf),
            "merge_summary": {
                "n_merged_kps": sum(1 for r in recs if len(r.get("pages") or []) > 1),
                "n_single_kps": sum(1 for r in recs if len(r.get("pages") or []) <= 1),
            },
            "elapsed_s": time.perf_counter() - started,
            "cost_cny_all_calls": extra["cost_cny"],
            "usage_total_all_calls": {
                "tokens_in": extra["tokens_in"],
                "tokens_out": extra["tokens_out"],
            },
        },
        "estimate": est,
    }


STRUCT_PROMPT_NAME = "提示词-思维导图结构.md"


KP_PROMPT_NAME_V2 = "提示词-知识点总结-v2.md"

# 结构生成的分块参数（双阈值：谁先到就切段）。理由写在提示词真源 §0.1：
# 90~100 页课件实测 1.3~1.8 万字，按 6000 字符切恒为 3~4 段 → 结构调用 4~5 次，与页数无关地有上界。


STRUCT_SEG_PAGES = 12


STRUCT_SEG_CHARS = 6000
# 喂给结构模型的**每页字符预算**。做层级判断不需要全文（`core/outline.py` 用的是 220/260），
# 这里给得宽一些（420）是因为 v2 还要给每页起短标题。


STRUCT_PAGE_CHARS = 420


STRUCT_MAX_TOKENS = 32768


STRUCT_RETRY = 1


STRUCT_TEMPERATURE = 0.2
# 一条知识点最多覆盖几页（连续）。
#   - v1 的 `MAX_MERGE=3` 是"模型可合并的上限"；
#   - v2 里它变成**管线的切片长度**（页归属由结构树唯一决定）；
#   - **v3（卡 C-0052）取消这个上限**：人拍板「不用强制要求每个知识点对应的 PPT 页数
#     必须不大于三张，让 AI 自己去做判断」。所以这里置 0 = **不限**：
#     一个结构叶节点 = 一条知识点（页数由结构那一步的模型判断）。
# 产物的三个顶层键：`meta` / `structure` / `knowledge_points`。
# ⚠ 卡 C-0058：顶层 `terms` **已摘出**（v3 有、v4 没有）——名词解释成了独立功能，
#   见本文件末尾的"名词解释（独立功能）"段与 `logs/knowledge/提示词-名词解释.md`。
#   旧 v2/v3 产物照旧可读：读 `terms` 一律用 `.get("terms") or []`，跨版本共存，
#   看板按 `meta.pipeline_version` 分流。


STRUCT_PROMPT_NAME = "提示词-思维导图结构.md"


KP_PROMPT_NAME_V2 = "提示词-知识点总结-v2.md"

# 结构生成的分块参数（双阈值：谁先到就切段）。理由写在提示词真源 §0.1：
# 90~100 页课件实测 1.3~1.8 万字，按 6000 字符切恒为 3~4 段 → 结构调用 4~5 次，与页数无关地有上界。


STRUCT_SEG_PAGES = 12


STRUCT_SEG_CHARS = 6000
# 喂给结构模型的**每页字符预算**。做层级判断不需要全文（`core/outline.py` 用的是 220/260），
# 这里给得宽一些（420）是因为 v2 还要给每页起短标题。


STRUCT_PAGE_CHARS = 420


STRUCT_MAX_TOKENS = 32768


STRUCT_RETRY = 1


STRUCT_TEMPERATURE = 0.2
# 一条知识点最多覆盖几页（连续）。
#   - v1 的 `MAX_MERGE=3` 是"模型可合并的上限"；
#   - v2 里它变成**管线的切片长度**（页归属由结构树唯一决定）；
#   - **v3（卡 C-0052）取消这个上限**：人拍板「不用强制要求每个知识点对应的 PPT 页数
#     必须不大于三张，让 AI 自己去做判断」。所以这里置 0 = **不限**：
#     一个结构叶节点 = 一条知识点（页数由结构那一步的模型判断）。
#     仅保留一个**调用可行性软上限** `KP_MATERIAL_CHARS_MAX`：材料超过它时按连续页
#     再切（逐条记 `split_reason`）——这不是"页数限制"，是防单次调用炸上下文。


KP_SPAN_PAGES = 0
# 单条知识点注入材料的字符软上限（约 15000 token）。doc#5 实测整份 34 页材料 ≈ 4.0 万字，
# 单叶节点远小于这个数；只有模型把一整门课塞进一个叶节点时才会触发切分。


KP_MATERIAL_CHARS_MAX = 24000
# 写知识点的 max_tokens：思考链要占大头（实测 8 页一条用掉 10638 个 reasoning token），
# 65536 给正文留足余量；它是上限不是预留用量，花多少按实际生成算。
# 写知识点的 max_tokens：思考链要占大头（实测 8 页一条用掉 10638 个 reasoning token），
# 65536 给正文留足余量；它是上限不是预留用量，花多少按实际生成算。


KP_MAX_TOKENS = 65536
# 名词筛选的 max_tokens：输入只有名词清单，输出只有 keep 名单。
# ⚠ 卡 C-0052 实测踩坑：**思考模式下 8192 不够**。doc#5 试点第一次跑，75 个候选名词
# （输入 4721 token）的两次调用都把 8192 全用在思维链上（reasoning_tokens=8192、
# 正文 raw_chars=0）→ JSON 解析失败 → 筛选降级为"保留全部 75 个"。
# 所以这里给到 32768，并在 `filter_terms_v2()` 里加了"被打满就加倍重试一次"的兜底。
# 卡 C-0058 起这个常量**只服务独立的名词解释功能**（提炼与筛选两步都用它），
# 知识点总结管线已经不用了。


TERMS_MAX_TOKENS = 32768
# dry-run 的思考 token 上界系数：思维链 token ≈ 该次输入 token 的这个倍数。
# 实测（2026-09-27）8 页材料一条：入 8937 → reasoning 10638（≈1.19 倍），
# 上界取 1.5 留余量。它只服务 `estimate_v2()` 的预估，不参与真实记账。


REASON_RATIO = 1.5


REASON_RATIO = 1.5


TERMS_MAX_TOKENS = 32768
# dry-run 的思考 token 上界系数：思维链 token ≈ 该次输入 token 的这个倍数。
# 实测（2026-09-27）8 页材料一条：入 8937 → reasoning 10638（≈1.19 倍），
# 上界取 1.5 留余量。它只服务 `estimate_v2()` 的预估，不参与真实记账。


REASON_RATIO = 1.5


def _acc_extra(extra, rec):
    u = rec.get('usage') or {}
    return {
        'calls': int(extra.get('calls') or 0) + 1,
        'tokens_in': int(extra.get('tokens_in') or 0) + int(u.get('tokens_in') or 0),
        'tokens_out': int(extra.get('tokens_out') or 0) + int(u.get('tokens_out') or 0),
        'cost_cny': round(
            float(extra.get('cost_cny') or 0)
            + float(u.get('cost_cny') or 0),
            6,
        ),
    }


def _gen_one(page, win, materials, inv, user, prompt, pages, ch, est, log, own_pages, carry_pages):
    from . import chat
    last_err = ''
    parsed = {}
    usage = {}
    model = MODEL
    elapsed = 0.0
    raw = ''
    for attempt in range(RETRY + 1):
        t0 = time.perf_counter()
        try:
            from . import agent_bridge
            with agent_bridge.pipeline_scope("courseware_kp"):
                r = chat.chat_once(
                    [
                        {'role': 'system', 'content': prompt['sys']},
                        {'role': 'user', 'content': user},
                    ],
                    model=MODEL,
                    max_tokens=MAX_TOKENS,
                    thinking=THINKING,
                    temperature=TEMPERATURE,
                    timeout=TIMEOUT,
                )
        except Exception as e:
            from .pipeline_runtime import state as runtime_state
            last_err = '第 %d 次调用仍失败：%s' % (attempt, e)
            if runtime_state.is_unknown_failure(e):
                raise RuntimeError(last_err + "（结果未知，已停止自动重试）") from None
            if attempt < RETRY:
                time.sleep(1)
                continue
            raise RuntimeError(last_err)
        elapsed = round(time.perf_counter() - t0, 1)
        raw = r['content'] or ''
        model = r.get('model') or MODEL
        u = r.get('usage') or {}
        usage = {
            'tokens_in': int(u.get('prompt_tokens') or 0),
            'tokens_out': int(u.get('completion_tokens') or 0),
            'total_tokens': int(u.get('total_tokens') or 0),
            'cache_hit_tokens': int(u.get('prompt_cache_hit_tokens') or 0),
            'cache_miss_tokens': int(u.get('prompt_cache_miss_tokens') or 0),
            'cost_cny': cost_of(u),
        }
        parsed = parse_json(raw)
        if parsed.get('_parse_error'):
            last_err = parsed['_parse_error']
            if attempt < RETRY:
                time.sleep(1)
                parsed = {}
                continue
        break

    kp_pages, rejected = normalize_pages(parsed.get('pages'), win, page)

    own = sorted({int(x) for x in (own_pages or [])})
    carry_all = sorted({int(x) for x in (carry_pages or [])} - set(own))
    owner_raw = est.get('page_owner') or {}
    owner_int = {int(k): int(v or 0) for k, v in owner_raw.items()}

    if carry_all and kp_pages:
        lo, hi = min(kp_pages), max(kp_pages)
        carry = [
            x for x in carry_all
            if owner_int.get(x) and lo <= int(owner_int.get(x) or 0) <= hi
        ]
    else:
        carry = []

    owned = sorted(set(own) | set(carry))
    if owned:
        union = sorted(set(kp_pages) | set(owned))
        gap = [q for q in range(union[0], union[-1] + 1) if q not in union]
        orphan = [q for q in gap if q not in win and not owner_int.get(q)]
        if len(union) != len(kp_pages) and not orphan:
            added = [x for x in owned if x not in kp_pages]
            extra = ('；中间 %s 已被别的条目收录，不并入' % '、'.join(str(x) for x in gap)) if gap else ''
            rejected = (rejected + '；' if rejected else '') + (
                f'管线补入下放的上级节点页 ' + '、'.join(str(x) for x in added) + f'（它们不单独成条{extra}）'
            )
            kp_pages = union
        elif orphan:
            rejected = (rejected + '；' if rejected else '') + (
                '下放的上级节点页与当前页之间有未归属的断号 %s，未并入' % '、'.join(str(x) for x in orphan)
            )

    path = list(pages.get(page) or [])
    summary = str(parsed.get('summary') or '').strip()
    points = [str(x).strip() for x in (parsed.get('points') or []) if str(x or '').strip()]
    mat = material_text_of(inv, ch)
    qual = quality_issues(summary, points, mat)
    srcs = []
    for s in (parsed.get('sources') or []):
        if not isinstance(s, dict):
            continue
        ids = []
        for x in (s.get('chunk_ids') or []):
            ids.append(int(x))
        srcs.append({
            'page': s.get('page'),
            'chunk_ids': ids,
            'figure_desc': bool(s.get('figure_desc')),
        })
    # C-0111 回填：以下到 `return rec` 为止，真值 = v1.1 原始字节码（…/PYZ.pyz_extracted/core/kp_build.pyc）
    # 里 `_gen_one` 从 STORE_FAST srcs 起的 419 条指令逐条还原；旧重建版在此处被截断成
    # `# TODO(反编译存疑) … pass`，函数连 return 都没有（调用方 rec=None ⇒ 后面 usage/persist 全崩）。
    # 五个 genexpr 的真值行号与形态也逐条对过（1774/1793/1800/1801/1803）。
    n_fig = sum(1 for m in inv if m['kind'] == '图片理解')
    reason = str(parsed.get('merge_reason') or '').strip()
    if rejected:
        reason = (reason + '｜管线校验：' + rejected).strip('｜')
    rec = {
        'id': 'LN-%02d' % page,
        'doc_id': int(est['doc_id']),
        'name': str(parsed.get('name') or (path[-1] if path else 'p.%d' % page)).strip(),
        'node_name': path[-1] if path else '',
        'path': path,
        'level': len(path),
        'pages': kp_pages,
        'handoff_pages': owned,
        'carry_pages': carry,
        'merged': len(kp_pages) > 1,
        'merge_reason': reason,
        'merge': {
            'window': list(win),
            'window_chars': sum(m['chars'] for m in inv),
            'rejected_reason': rejected,
            'model_pages': parsed.get('pages'),
            'model_merged': bool(parsed.get('merged')),
            'handoff_pages': owned,
            'carry_pages': carry,
            'page_chars': [
                {
                    'page': p,
                    'chars': sum(m['chars'] for m in inv if m['page'] == p),
                    'n_body': sum(1 for m in inv if m['page'] == p and m['kind'] == '正文块'),
                    'n_fig': sum(1 for m in inv if m['page'] == p and m['kind'] == '图片理解'),
                }
                for p in win
            ],
        },
        'summary': summary,
        'points': points,
        'sources': srcs,
        'insufficient': bool(parsed.get('insufficient')),
        'insufficient_reason': str(parsed.get('insufficient_reason') or ''),
        **{
            'quality': qual,
            'usage': usage,
            'elapsed_s': elapsed,
            'model': model,
            'generated_at': time.strftime('%Y-%m-%dT%H:%M:%S'),
            'raw': raw,
        },
    }
    _apply_material(rec, cover_material(inv, kp_pages), inv)
    return rec


def _prompt_path_v2(name: str, path: str | os.PathLike | None = None) -> Path:
    """v2 提示词真源的路径：显式给定 > 本学科 `logs/knowledge/`
    > 项目根 `logs/knowledge/` > **随包分发的资源目录**（卡 C-0073）。

    与 `default_prompt_file()` 同一套"本学科优先、根目录兜底"的规矩：提示词是**管线资产**，
    新学科一出生自己的 `logs/knowledge/` 里没有它，不能因此就跑不动。

    最后那一级只在**打包后**才有区别：exe 同级那份被用户删了，
    就从 `config.BUNDLE_ROOT`（`sys._MEIPASS`）里读随包带的那份。
    源码模式下两个根相同，行为逐字不变。
    """
    if path:
        return Path(path)
    own = config.LOG_DIR / "knowledge" / name
    if own.exists():
        return own
    root = config.ROOT / "logs" / "knowledge" / name
    if root.exists():
        return root
    return config.BUNDLE_ROOT / "logs" / "knowledge" / name


def default_struct_prompt_file() -> Path:
    return _prompt_path_v2(STRUCT_PROMPT_NAME)


def default_kp_prompt_file_v2() -> Path:
    return _prompt_path_v2(KP_PROMPT_NAME_V2)


def _load_md_prompts(path: Path, tags: list, optional: list = ()) -> dict:
    """从一个提示词真源里逐字提取若干代码块，并算出文件 sha256。

    `optional` 里的块找不到时给空串（见 `_grab_opt`）。
    """
    blob = path.read_bytes()
    md = blob.decode("utf-8")
    out: dict = {"path": str(path), "sha256": hashlib.sha256(blob).hexdigest()}
    for tag, key in tags:
        out[key] = _grab(md, tag, path)
    for tag, key in optional:
        out[key] = _grab_opt(md, tag)
    out["chars"] = sum(len(out[k]) for _t, k in tags)
    out["optional_chars"] = sum(len(out[k]) for _t, k in optional)
    return out


def load_struct_prompts(path: str | os.PathLike | None = None) -> dict:
    """结构生成的四个提示词块（map 的 sys/user + reduce 的 sys/user）。"""
    p = _prompt_path_v2(STRUCT_PROMPT_NAME, path)
    return _load_md_prompts(p, [("PROMPT-STRUCT-SYS", "sys"),
                                ("PROMPT-STRUCT-USER", "user"),
                                ("PROMPT-STRUCT-MERGE-SYS", "merge_sys"),
                                ("PROMPT-STRUCT-MERGE-USER", "merge_user")])


def load_kp_prompts_v2(path):
    p = _prompt_path_v2(KP_PROMPT_NAME_V2, path)
    return _load_md_prompts(p, [
        ("PROMPT-SYS", "sys"),
        ("PROMPT-USER", "user"),
    ])


def doc_title_v2(doc_id: int, db_path: str | os.PathLike | None = None) -> str:
    """v2 的课件名：**只查库里的 `documents.title`**（不读 outline）。

    ⚠ 卡 C-0049 实测踩到过：v1 的 `doc_title()` 会先读 `logs/outline/<id>.json` 取
    `deck` 兜底——v2 若直接复用它，就仍然依赖入库结构（离线守卫反证当场抓到）。
    所以 v2 另起一个：读库读不到就用 `文档 <id>`，绝不回头找 outline。
    """
    db = Path(db_path) if db_path else Path(config.DB_PATH)
    title = ""
    try:
        conn = sqlite3.connect("file:" + db.as_posix() + "?mode=ro", uri=True)
        try:
            row = conn.execute("SELECT title FROM documents WHERE id=?",
                               (int(doc_id),)).fetchone()
            title = str(row[0] or "").strip() if row else ""
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        title = ""
    return title or ("文档 %d" % int(doc_id))


# ---------------------------------------------------------------- 只读文本


def read_text_pages(doc_id: int, db_path: str | os.PathLike | None = None) -> dict:
    """读这份课件的**文本**，按 `source_ref` 的页号组织。**v2 的唯一数据入口。**

    ⚠ 卡 C-0049 的硬要求，这个 SQL 是判据：只 SELECT `id / ord / source_ref / text`——
    `chunks.topic`、`chunks.heading_path` 是入库结构派生的字段，一个字都不读。
    `logs/outline/<doc_id>.json` 也不读（本函数根本不知道它的存在）。

    返回：
      blocks[page] = [块…]（**按起始页归档**，与 v1 `read_chunks()` 同口径）
        块 = {chunk_id, ord, kind, ref, page, pages, chars, text}
      cover[page]   = 覆盖该页的块 id（跨页块 `p.12-16` 会落在 12..16 每一页上）
      figrows[page] = {no_info}
      pages_all     = [1..max_page]，**含中间没有块的空页**（覆盖率的分母就是这个）

    `source_ref` 解析不出页码的块**直接报错**，绝不静默丢块（同 v1）。
    """
    db = Path(db_path) if db_path else Path(config.DB_PATH)
    conn = sqlite3.connect("file:" + db.as_posix() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        blocks: dict = {}
        cover: dict = {}
        bad = []
        n_blocks = 0
        for r in conn.execute("SELECT id, ord, source_ref, text FROM chunks "
                              "WHERE doc_id=? ORDER BY ord, id", (int(doc_id),)):
            cid = int(r["id"])
            ref = (r["source_ref"] or "").strip()
            parsed = _parse_ref(ref)
            if parsed is None:
                bad.append((cid, ref))
                continue
            a, b, is_fig = parsed
            t = r["text"] or ""
            blocks.setdefault(a, []).append({
                "chunk_id": cid, "ord": int(r["ord"] or 0),
                "kind": "图片理解" if is_fig else "正文块",
                "ref": ("p.%d" % a if a == b else "p.%d-%d" % (a, b))
                       + (" 图" if is_fig else ""),
                "page": a, "pages": list(range(a, b + 1)),
                "chars": len(t), "text": t})
            for p in range(a, b + 1):
                cover.setdefault(p, []).append(cid)
            n_blocks += 1
        figrows: dict = {}
        for r in _figure_rows(conn, doc_id, "page, no_info"):
            figrows[int(r["page"])] = {"no_info": int(r["no_info"] or 0)}
    finally:
        conn.close()
    if bad:
        raise RuntimeError("有 %d 个块的 source_ref 解析不出页码（拒绝静默丢块）：%s"
                           % (len(bad), bad[:5]))
    max_page = max(cover) if cover else 0
    return {"blocks": blocks, "cover": cover, "figrows": figrows,
            "max_page": max_page, "n_blocks": n_blocks,
            "pages_all": list(range(1, max_page + 1))}


def read_text_pages_by_ids(doc_id: int, chunk_ids,
                           db_path: str | os.PathLike | None = None) -> dict:
    """只读取指定 chunk 集合的文本页结构（章级读取的快速路径）。

    返回结构与 `read_text_pages()` 相同，但只包含给定块：
      - `blocks`/`cover`/`figrows`/`max_page`/`n_blocks`/`pages_all`；
      - 块解析规则、跨页覆盖、图片理解标记与整本读取完全一致；
      - 任何给定块解析不出页码仍会报错，绝不静默丢块。

    只用于**只读章级材料**；调用方仍需自己把 `pages_all` 限定到章的页范围
    （教材章可能包含无块空页，快速路径只按实际块覆盖页计算）。
    """
    wanted = sorted({int(x) for x in (chunk_ids or []) if int(x) > 0})
    if not wanted:
        return {"blocks": {}, "cover": {}, "figrows": {}, "max_page": 0,
                "n_blocks": 0, "pages_all": []}
    db = Path(db_path) if db_path else Path(config.DB_PATH)
    conn = sqlite3.connect("file:" + db.as_posix() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        blocks: dict = {}
        cover: dict = {}
        bad = []
        n_blocks = 0
        # 章级块数通常几十个；分片查询避免 SQLite 变量上限。
        for start in range(0, len(wanted), 500):
            part = wanted[start:start + 500]
            marks = ",".join("?" for _ in part)
            sql = ("SELECT id, ord, source_ref, text FROM chunks "
                   "WHERE doc_id=? AND id IN (%s) ORDER BY ord, id" % marks)
            for r in conn.execute(sql, (int(doc_id), *part)):
                cid = int(r["id"])
                ref = (r["source_ref"] or "").strip()
                parsed = _parse_ref(ref)
                if parsed is None:
                    bad.append((cid, ref))
                    continue
                a, b, is_fig = parsed
                t = r["text"] or ""
                blocks.setdefault(a, []).append({
                    "chunk_id": cid, "ord": int(r["ord"] or 0),
                    "kind": "图片理解" if is_fig else "正文块",
                    "ref": ("p.%d" % a if a == b else "p.%d-%d" % (a, b))
                           + (" 图" if is_fig else ""),
                    "page": a, "pages": list(range(a, b + 1)),
                    "chars": len(t), "text": t})
                for p in range(a, b + 1):
                    cover.setdefault(p, []).append(cid)
                n_blocks += 1
        figrows: dict = {}
        for r in _figure_rows(conn, doc_id, "page, no_info"):
            page = int(r["page"])
            if page in cover:
                figrows[page] = {"no_info": int(r["no_info"] or 0)}
    finally:
        conn.close()
    if bad:
        raise RuntimeError("有 %d 个块的 source_ref 解析不出页码（拒绝静默丢块）：%s"
                           % (len(bad), bad[:5]))
    max_page = max(cover) if cover else 0
    return {"blocks": blocks, "cover": cover, "figrows": figrows,
            "max_page": max_page, "n_blocks": n_blocks,
            "pages_all": list(range(1, max_page + 1))}


def page_text_for_structure(p: int, ch: dict, cap: int = STRUCT_PAGE_CHARS) -> str:
    """该页喂给**结构模型**的文本（截断到 `cap` 字符，截断处标 `……`）。"""
    bs = sorted(ch["blocks"].get(p) or [], key=lambda x: (x["ord"], x["chunk_id"]))
    parts = []
    for b in bs:
        t = re.sub(r"\s+", " ", b["text"] or "").strip()
        if t:
            parts.append("〔%s〕%s" % (b["kind"], t))
    s = " ".join(parts)
    if len(s) > cap:
        s = s[:cap] + "……"
    return s


def plan_structure_segments(ch: dict) -> list:
    """把全页切成若干段（map 的输入）：**页数与字符数双阈值，谁先到就切**。

    - 字符数按"该段各页喂给结构模型的文本"算（每页先截断到 `STRUCT_PAGE_CHARS`）；
    - 空页（没有块）也占一个页位，字符按 0 算——它同样是"这一段的第几页"；
    - 单页自身就超阈值时它独占一段（段长 1），不会出现空段或死循环。

    返回 [{index, a, b, pages, chars}]（升序、无缝覆盖 `pages_all`）。
    """
    pages = list(ch["pages_all"])
    if not pages:
        return []
    segs: list = []
    cur: list = []
    cur_chars = 0
    for p in pages:
        t = page_text_for_structure(p, ch)
        n = len(t)
        if cur and (len(cur) >= STRUCT_SEG_PAGES
                    or cur_chars + n > STRUCT_SEG_CHARS):
            segs.append(cur)
            cur, cur_chars = [], 0
        cur.append(p)
        cur_chars += n
    if cur:
        segs.append(cur)
    return [{"index": i + 1, "a": s[0], "b": s[-1], "pages": list(s),
             "chars": sum(len(page_text_for_structure(p, ch)) for p in s)}
            for i, s in enumerate(segs)]


def render_struct_material(seg: dict, ch: dict) -> str:
    figrows = ch["figrows"]
    lines = []
    for p in seg["pages"]:
        lines.append("===== 第 %d 页 =====" % p)
        if (figrows.get(p) or {}).get("no_info"):
            lines.append("（注：本页图片已被判定为\u201c无知识性图示\u201d，"
                         "下面的图片理解只是占位，不含知识内容）")
        t = page_text_for_structure(p, ch)
        lines.append(t or "（本页没有文本）")
        lines.append("")
    return "\n".join(lines)


INNER_WORKERS_ENV = "KP_INNER_WORKERS"


MODEL_GATE_ENV = "KP_MODEL_GATE"


INNER_WORKERS_DEFAULT = 4


WORKERS_MIN = 1


WORKERS_MAX = 8


_GATE_LOCK = threading.Lock()


_GATE: dict = {"sem": None, "limit": 0}
# 验收用的计数器（**只活在进程里，不写进产物**）：在飞峰值 / 累计取槽次数 / 排队次数。


_GATE_STATS: dict = {"inflight": 0, "peak_inflight": 0, "acquired": 0, "waited": 0}


def render_struct_titles(pages: list, titles: dict, ch: dict) -> str:
    out = []
    for p in pages:
        out.append("p.%-3d %s" % (p, titles.get(p) or ("（第 %d 页）" % p)))
    return "\n".join(out)


def render_struct_drafts(drafts: list) -> str:
    out = []
    for d in drafts:
        out.append("〔第 %d~%d 页〕" % (d["a"], d["b"]))
        for sec in d["sections"]:
            out.append("- %s" % sec["name"])
            for kid in (sec.get("children") or []):
                out.append("  · %s (p.%s)" % (kid["name"], _pages_label(kid.get("pages"))))
        if not d["sections"]:
            out.append("- （这一段没有给出章节草稿）")
    return "\n".join(out)


# ---------------------------------------------------------------- 并发闸门（卡 C-0063）
#
# 卡 C-0063（2026-10-08）按人拍板「课件入库和总结知识点的用时太久了」做**单份整理
# 管线内部并发**（C-0062 报告 §9.1 第一步）。并发的上限不能只看本管线自己：
#   - `core/pipeline_pool.py`（多份之间·**入库**）默认 2、上限 4；
#   - `core/kp_batch.py`（多份之间·**整理**）默认 2、上限 4；
#   - 本管线（单份内部）默认 4、可配 1~8。
# 三者是**乘法叠加**（C-0062 报告 §6.3：`kp_batch=4 × 单份内部=4 = 16 路`，
# 正好压在实测边界线上）。所以这里放的是**进程级共享闸门**：
#
#   `_MODEL_GATE` 是一把 `threading.BoundedSemaphore`，单位 = **一次在飞的模型调用**。
#     - `_v2_call()` 每次真调用前后 acquire/release（**调用级**，最细的粒度）；
#     - `core/pipeline_pool.py::_run_one()` 每份入库任务持有一个（**份级**：逐页识图
#       的调用在 `core/figures.py` 里，那是另一张卡的范围，本卡不动那个文件）；
#     - `core/kp_batch.py::start()` 把份数夹到不超过闸门值（份 × 份内线程全挤在
#       同一把闸门前排队没有意义，只会让进度条长时间不动）。
#   三者**指向同一个对象**，所以"进程内在飞的模型调用数 ≤ 闸门值"永远成立
#   （证据：`model_gate_stats()["peak_inflight"] <= ["limit"]`）。
#
# ⚠ **不存在"持槽等槽"的死锁**：入库那层持槽时内部**不再取槽**（figures 不经过
# 闸门），整理那层只在调用级取槽、份级不持槽——取槽的嵌套深度恒为 1。
#
# 环境变量（与 `core/pipeline_pool.py::KB_INGEST_WORKERS` 的既有做法对齐）：
#   - `KP_INNER_WORKERS`：单份课件**内部**并发数，默认 4，夹到 1..8；
#   - `KP_MODEL_GATE`：进程级总闸门值；**不设时取 `KP_INNER_WORKERS`**（默认也是 4），
#     夹到 1..8。想单独控制总闸门时用它。


def _clamp_workers(n, default: int = INNER_WORKERS_DEFAULT) -> int:
    """把并发数夹到 1..8，非法值退回 `default`。"""
    try:
        n = int(n)
    except (TypeError, ValueError):
        return default
    return max(WORKERS_MIN, min(WORKERS_MAX, n))


def inner_workers() -> int:
    """**单份课件内部**的并发数：`KP_INNER_WORKERS` 优先，默认 4，夹到 1..8。

    `1` 是**安全的回退开关**：此时本管线走与改造前逐字相同的串行路径
    （不建线程池、不改调用顺序），产物逐字段等价。
    """
    return _clamp_workers(os.environ.get(INNER_WORKERS_ENV, INNER_WORKERS_DEFAULT))


def _gate_value_from_env() -> int:
    """闸门值：`KP_MODEL_GATE` 优先，没设则跟随 `KP_INNER_WORKERS`，都没有就是 4。"""
    raw = os.environ.get(MODEL_GATE_ENV)
    if raw is None or str(raw).strip() == "":
        raw = os.environ.get(INNER_WORKERS_ENV, INNER_WORKERS_DEFAULT)
    return _clamp_workers(raw)


def _gate():
    """惰性建闸门（进程内**唯一一份**）→ 返回 `(sem, limit)`。

    惰性是因为环境变量要在"用之前"才读：进程起来后 `os.environ` 里再塞
    `KP_INNER_WORKERS` 也来得及（测试与看板接线都吃这个便利）。
    """
    with _GATE_LOCK:
        if _GATE["sem"] is None:
            _GATE["limit"] = _gate_value_from_env()
            _GATE["sem"] = threading.BoundedSemaphore(_GATE["limit"])
        return _GATE["sem"], int(_GATE["limit"])


def configure_model_gate(n=None) -> int:
    """重设闸门值（**只在没有在飞调用时**用：进程启动接线 / 验收脚本）。

    `n=None` 表示按当前环境变量重算。返回新的闸门值。
    """
    with _GATE_LOCK:
        limit = _gate_value_from_env() if n is None else _clamp_workers(n)
        _GATE["limit"] = limit
        _GATE["sem"] = threading.BoundedSemaphore(limit)
        _GATE_STATS.update({"inflight": 0, "peak_inflight": 0, "acquired": 0, "waited": 0})
        return limit


def model_gate_limit() -> int:
    """当前进程级闸门值（= 允许同时在飞的模型调用数上限）。"""
    return _gate()[1]


_CALLS_LOCK = threading.Lock()


# ---------------------------------------------------------------- 停滞槽位回收（卡 C-0124）
#
# 现场（C-0124 打桩复现，`_c0124_hang_probe.py` 场景 B）：调用挂起时 `build_v2` 的
# 判停线只等 `stall_timeout()`，而 **worker 自己的硬墙要等 `call_hard_timeout()`**。
# 默认值下 `stall(2400) > hard(1800)`，所以判停线**必然先触发**：
#     t=0    主线程 `_cf.wait(timeout=2400)` 等着
#     t=2400 判停线到、主线程带着已完成的条数收尾落盘（进程不再等 worker）
#     t=1800 **worker 自己的硬墙**才到 —— 可此时已经没人等它了
# ⇒ 判停那一刻，在飞 worker 还**握着模型槽**（它阻塞在 `_call_with_hard_timeout`
#   的 `th.join(1800)` 里，根本走不到 `with model_slot` 的退出路径）。实测：
#   `model_gate_stats()["inflight"] == 1` 在 `build_v2` 返回之后仍然成立。
# ⇒ 后果：`configure_model_gate` 只在不变量被破坏时才重建闸门；正常路径下这一格
#   槽**要等 1800 秒**才由那个早已无人等待的 worker 释放。用户紧接着再点一次
#   「整理」，起手就少一格并发（多份/多次运行下会一路恶化）。
#
# 修法：判停时由**主线程**按账回收这些"已经没人等"的槽位。回收后闸门当场恢复
# 满额；被回收的那个 worker 之后再走完自己的退出路径时**跳过归还**（否则
# BoundedSemaphore 会 `ValueError: released too many times`）。
_ABANDONED_LOCK = threading.Lock()

_ABANDONED: dict = {"n": 0}
# 本 worker 线程的槽是否已被主线程按账回收（放弃归还）/ 是否已记账。
_ABANDONED_LOCAL = threading.local()


def _abandoned_enter() -> None:
    """worker 抢到槽后**立刻**记一笔账（卡 C-0124）。

    记账时点选在 `acquire()` 成功之后，而不是"提交任务之前"——这样
    `cancel_futures` 里那些**从没跑起来**的份不会虚增账目。
    """
    _ABANDONED_LOCAL.reclaimed = False
    with _ABANDONED_LOCK:
        _ABANDONED["n"] += 1


def _abandoned_done() -> None:
    """worker 正常收尾 → 销账（**恰好一次**，见 `_kp_worker` 的 finally）。

    若这一格已经被主线程按账抢回去了，销账**不重复扣减**（账已经归零）。
    """
    if getattr(_ABANDONED_LOCAL, "reclaimed", False):
        _ABANDONED_LOCAL.reclaimed = False
        return
    with _ABANDONED_LOCK:
        if _ABANDONED["n"] > 0:
            _ABANDONED["n"] -= 1


def _slot_was_reclaimed() -> bool:
    return bool(getattr(_ABANDONED_LOCAL, "reclaimed", False))


def reclaim_stalled_slots() -> int:
    """判停时回收**已经没人等**的在飞槽位（卡 C-0124），返回回收个数。

    ⚠ 只在 `build_v2` 判定"停滞"、主线程**不再等待**在飞 worker 之后调用 ——
    那之后这些 worker 无论多久才回来，结果都写进一个已经丢弃的 `futures`
    字典里，槽位对本次运行已经没有意义。抢回它们，下一次运行并发额度才是满的。

    回收是**逐格**还的（不重建闸门）：`_gate()` 是进程级共享的，
    `core/pipeline_pool.py` 的入库任务也在用它，重建会把在飞额度凭空翻倍。
    """
    with _ABANDONED_LOCK:
        n = int(_ABANDONED["n"])
        if n <= 0:
            return 0
        _ABANDONED["n"] = 0
    sem, _limit = _gate()
    got = 0
    for _ in range(n):
        try:
            sem.release()
        except ValueError:      # 已经还过了（极端竞态）→ 不再多还
            break
        got += 1
    if got:
        with _GATE_LOCK:
            _GATE_STATS["inflight"] = max(0, int(_GATE_STATS["inflight"]) - got)
    return got


def model_gate_stats() -> dict:
    """闸门快照（验收证据）：{limit, inflight, peak_inflight, acquired, waited}。"""
    with _GATE_LOCK:
        d = dict(_GATE_STATS)
        d["limit"] = int(_GATE["limit"])
    d["abandoned"] = int(_ABANDONED["n"])
    return d


# C-0111 连带修复：`@contextlib.contextmanager` 原本被重建错位到 configure_model_gate
# 头上，而真正需要它的是**生成器** model_slot。真值（v1.1 原始字节码）铁证：
#   · model_slot           co_flags=0x1000023（含 CO_GENERATOR），模块级 STORE_NAME
#                          前有一条 CALL（= 装饰器调用）；
#   · configure_model_gate co_flags=0x1000003（非生成器），STORE_NAME 前**没有** CALL。
# 错位后 `with model_slot(...)` 拿到的是裸生成器 ⇒ 每一次模型调用都抛
# TypeError: 'generator' object does not support the context manager protocol
# （gen_structure_v2 / gen_kp_v2 / build_v2 全链路必经，现象是「整理知识点」第一步就失败）。
@contextlib.contextmanager
def model_slot(kind: str = "", timeout: float | None = None):
    """**进程级模型调用配额**：进入时抢一个槽，退出时还回去。

    所有真发起模型调用的地方都要经过它（本文件的 `_v2_call`、`core/pipeline_pool.py`
    的每份入库任务）。抢不到就排队等——这就是"总在飞数 ≤ 闸门值"的全部实现。

    `timeout`（卡 C-0123 新增）：等槽位的上限（秒）；`None` = 不设限（**默认值，
    与改造前逐字相同的行为**——`core/pipeline_pool.py` 的**份级**槽就是这种用法：
    它本来就要持有一整份入库的时间，不该被超时打断）。传了正数则超时抛
    `ModelSlotTimeout`，让排队者不至于被一个挂起的调用永久钉在 `acquire()` 上。
    """
    sem, _limit = _gate()
    t0 = time.perf_counter()
    if timeout and timeout > 0:
        got = sem.acquire(timeout=timeout)
        if not got:
            raise ModelSlotTimeout(
                "等模型槽位超过 %.0f 秒仍没抢到（闸门值 %d）：有调用挂起未归还，"
                "本步放弃（卡 C-0123 兜底）" % (timeout, _limit))
    else:
        sem.acquire()
    waited = (time.perf_counter() - t0) > 0.001
    with _GATE_LOCK:
        _GATE_STATS["inflight"] += 1
        _GATE_STATS["acquired"] += 1
        if waited:
            _GATE_STATS["waited"] += 1
        if _GATE_STATS["inflight"] > _GATE_STATS["peak_inflight"]:
            _GATE_STATS["peak_inflight"] = _GATE_STATS["inflight"]
    try:
        yield
    finally:
        # 卡 C-0124：判停时主线程可能已按账把这个槽抢回去了（见
        # `reclaim_stalled_slots()`）。那时**绝不能再还一次**——闸门是
        # `BoundedSemaphore`，多还一次直接抛 `ValueError: released too many
        # times`，把收尾路径也炸掉。
        if _slot_was_reclaimed():
            _ABANDONED_LOCAL.reclaimed = False
        else:
            with _GATE_LOCK:
                _GATE_STATS["inflight"] -= 1
            sem.release()


# ---------------------------------------------------------------- 页预约（卡 C-0063）
#
# ★ 本卡最关键的改动：`claimed`（"页 → 收录它的知识点 id"）**必须在模型调用之前**
# 就占好，不能等调用返回再写。
#
# 依据（C-0062 报告 §7.1 的对抗实验，四种写法实测）：
#   | 写法                                        | overlap_pages |
#   |---|---|
#   | 改造前：读在调用前、写在返回后（**无锁**）      | [10, 11] ❌   |
#   | 同样写法**加锁**                             | [10, 11] ❌   |
#   | **调用前预约**（本函数）                      | [] ✅         |
# 根因：那对"读-改-写"**跨了整个模型调用**（10~88 秒），锁只保护毫秒级临界区，
# 保护不了"调用期间别人也来读"。所有权必须在并发窗口打开之前定下来。
#
# 这两个函数做成**模块级**（不是 `build_v2()` 里的闭包）是为了可被对抗实验直接
# 调用：验收要证明的正是"预约 → overlap 为空 / 不预约 → overlap 非空"。


def _append_call(calls: list, rec: dict, *, call_no: int = 0, kp_seq: bool = False) -> int:
    """把一条调用记录**在锁内** append 进 `calls`，返回它自己的下标。

    为什么要有这个函数（卡 C-0063）：改造前是 `calls.append(rec)` 之后用
    `len(calls) - 1` 反推下标。单线程下这两步之间没别人，反推成立；并发下
    另一个线程可能正好挤在中间，反推出来的下标就指向**别人的**记录。
    （C-0062 报告 §10 第 10 条：写了对抗实验但 0 次错配，属"理论不成立、
    实际极难触发"。既然本卡正在改这段代码，顺手使它正确。）

    `kp_no` 也在这里分配：`kp_seq=True` → 取"锁内的 calls 长度 + 1"，
    并发下唯一且单调；显式传 `call_no` 的老调用方语义不变（记它给的值）。
    """
    with _CALLS_LOCK:
        if kp_seq:
            rec["kp_no"] = len(calls) + 1
        elif call_no:
            rec["kp_no"] = int(call_no)
        calls.append(rec)
        return len(calls) - 1


def _v2_call(calls: list, kind: str, sys_p: str, user_p: str, *, max_tokens: int,
             retry: int, log, note: str = "", temperature: float = TEMPERATURE,
             call_no: int = 0, run_no: int = 1, thinking: bool = THINKING_V2,
             reasoning_effort: str = REASONING_EFFORT, kp_seq: bool = False) -> dict:
    """调一次模型，**成功与失败都逐次记账**（`calls` 直接进产物 `meta.calls`）。

    记账字段：run（第几轮运行）/ kind（这一步是谁）/ attempt（第几次试）/
    tokens_in/out / cost_cny / elapsed_s / ok / error，另有卡 C-0052 追加的
    `thinking`（是否开推理）、`reasoning_effort`（透传的强度档）、
    `reasoning_tokens`（思考链占用的 completion token，取自
    `usage.completion_tokens_details.reasoning_tokens`）。失败轮也占一行——
    "失败不静默"这条底线靠它落地。

    ⚠ `calls` 是**跨轮累积**的：续跑或 `--force` 重跑时，上一轮的记录一并带过来
    （见 `build_v2()`），所以产物 `meta.calls` 是这份产物**从头到现在的全部调用**，
    `meta.cost_cny_all_calls` 也就是它真正的累计花费。

    返回里除了老字段，另有（卡 C-0063）：
      - `call`：**这一次调用自己的记账对象**（并发下调用方拿它取 `elapsed_s`，
        不再用 `calls[call_index]` 二次索引）；
      - `call_index`：同一把锁里算出来的下标（并发下也指向 `call` 本人）；
      - `kp_seq=True` 时，`kp_no` 由 `_append_call()` **在锁内**分配（唯一、单调）；
        显式传 `call_no` 的老调用方行为不变。
    """
    from . import chat  # 延迟 import：dry-run 时不需要 key

    last = ""
    last_idx = -1
    for attempt in range(retry + 1):
        t0 = time.perf_counter()
        rec = {"run": int(run_no), "kind": kind, "attempt": attempt + 1, "note": note,
               "model": MODEL, "max_tokens": int(max_tokens),
               "thinking": bool(thinking),
               "reasoning_effort": (str(reasoning_effort) if thinking else ""),
               "chars_in": len(sys_p) + len(user_p),
               "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
        try:
            # 卡 C-0063：**进程级闸门**——真调用期间持一个槽（见文件上方的
            # "并发闸门"小节）。重试的每一次也算一次独立调用，各占各的槽。
            #
            # 卡 C-0123：槽位仍由**本 worker 线程**持有，真调用放进陪跑线程（硬墙）。
            # 这样一旦服务端不回包，本线程从 `with` 里退出来时**当场把槽还回去**，
            # 后续 worker 照跑；否则 4 个槽会被 4 个挂起的调用占死，全批一起卡住
            # （用户 2026-10-15 08:42 那次就是这么卡住的）。
            with model_slot(kind, timeout=slot_wait_timeout()):
                # 卡 C-0124：**抢到槽之后**为"这一格可能永远不回来"记一笔账
                # （记账在 acquire 成功之后，`cancel_futures` 里没跑起来的份不会
                # 虚增）；正常返回时在下面的 finally 里销账。判停时主线程按账回收。
                _abandoned_enter()
                try:
                    from . import agent_bridge
                    pipeline = "terms" if kind.startswith("terms.") else "courseware_kp"
                    with agent_bridge.pipeline_scope(pipeline):
                        r = _call_with_hard_timeout(
                            lambda: chat.chat_once(
                                [{"role": "system", "content": sys_p},
                                 {"role": "user", "content": user_p}],
                                model=MODEL, max_tokens=max_tokens, thinking=thinking,
                                temperature=temperature, timeout=TIMEOUT,
                                reasoning_effort=(reasoning_effort if thinking else None)),
                            timeout=call_hard_timeout(), kind=kind)
                finally:
                    _abandoned_done()
        except Exception as e:  # noqa: BLE001
            last = "%s: %s" % (type(e).__name__, e)
            el = round(time.perf_counter() - t0, 1)
            observe_call_seconds(kind, el)
            rec.update({"ok": False, "error": last,
                        "elapsed_s": el,
                        "tokens_in": 0, "tokens_out": 0, "reasoning_tokens": 0,
                        "cost_cny": 0.0, "raw_chars": 0})
            last_idx = _append_call(calls, rec, call_no=call_no, kp_seq=kp_seq)
            log("  ★调用失败（%s 第 %d 次）：%s" % (kind, attempt + 1, last))
            # P0-B：发送后结果未知（超时/断连/没回包）→ **只允许一次请求**，
            # 不自动重试（可能已扣费/已产生副作用）；把 unknown 标记交回上层。
            from .pipeline_runtime import state as runtime_state
            if runtime_state.is_unknown_failure(e):
                log("  ★remote_unknown：结果未知，禁止自动重试（P0-B）")
                return {"ok": False, "parsed": {}, "usage": {}, "raw": "",
                        "model": MODEL, "error": last, "call_index": last_idx,
                        "call": rec, "remote_unknown": True}
            # 卡 C-0123：**挂起类错误不重试**——重试只会再挂一个陪跑线程、
            # 再白等一个硬上限；而且它通常意味着整条网络路径不通，立即把错误
            # 交回上层记账（上层据此把这一条判失败、让整轮能收尾）。
            if isinstance(e, (ModelCallHang, ModelSlotTimeout)):
                log("  ★%s：不再重试，直接交回上层（卡 C-0123）"
                    % type(e).__name__)
                return {"ok": False, "parsed": {}, "usage": {}, "raw": "",
                        "model": MODEL, "error": last, "call_index": last_idx,
                        "call": rec, "hang": True}
            if attempt < retry:
                time.sleep(2 * (attempt + 1))
            continue
        u = r.get("usage") or {}
        usage = {"tokens_in": int(u.get("prompt_tokens") or 0),
                 "tokens_out": int(u.get("completion_tokens") or 0),
                 "total_tokens": int(u.get("total_tokens") or 0),
                 "reasoning_tokens": _reasoning_tokens(u),
                 "cost_cny": cost_of(u)}
        raw = r["content"] or ""
        parsed = parse_json(raw)
        err = str(parsed.get("_parse_error") or "")
        el_ok = round(time.perf_counter() - t0, 1)
        observe_call_seconds(kind, el_ok)
        rec.update({"ok": not err, "model": r.get("model") or MODEL,
                    "elapsed_s": el_ok,
                    "tokens_in": usage["tokens_in"], "tokens_out": usage["tokens_out"],
                    "reasoning_tokens": usage["reasoning_tokens"],
                    "cost_cny": usage["cost_cny"], "raw_chars": len(raw), "error": err})
        last_idx = _append_call(calls, rec, call_no=call_no, kp_seq=kp_seq)
        if not err:
            return {"ok": True, "parsed": parsed, "usage": usage, "raw": raw,
                    "model": rec["model"], "error": "", "call_index": last_idx,
                    "call": rec}
        last = "JSON 解析失败：%s" % err
        log("  ★输出解析失败（%s 第 %d 次）：%s" % (kind, attempt + 1, err))
        if attempt < retry:
            time.sleep(1)
    return {"ok": False, "parsed": {}, "usage": {}, "raw": "", "model": MODEL,
            "error": last, "call_index": last_idx, "call": None}


def _reasoning_tokens(usage):
    """从 usage 里取思考链 token（官方放在 `completion_tokens_details` 下）。

    兜底认三处写法（`reasoning_tokens` 顶层 / `completion_tokens_details` /
    `output_tokens_details`），取不到记 0——**不猜**，宁可显示 0 也不编一个数。
    """
    if not isinstance(usage, dict):
        return 0
    for holder in (
        usage.get("completion_tokens_details"),
        usage.get("output_tokens_details"),
        usage,
    ):
        if not isinstance(holder, dict):
            continue
        v = holder.get("reasoning_tokens")
        try:
            if v is not None:
                return int(v)
        except (TypeError, ValueError):
            pass
    return 0


def _clean_section(node, depth: int = 0) -> dict | None:
    """清洗一个结构节点：名字去空白限长、递归清洗 children、**只给叶节点留 pages**。"""
    if not isinstance(node, dict):
        return None
    name = str(node.get("name") or node.get("title") or node.get("section") or "").strip()
    name = re.sub("\\s+", " ", name)[:24]
    if not name:
        return None
    kids = []
    for x in (node.get("children") or node.get("sections") or []):
        c = _clean_section(x, depth + 1)
        if not c:
            continue
        kids.append(c)
    out = {"name": name, "children": kids}
    if not kids:
        out["pages"] = _page_list(node.get("pages"))
    return out


def _flat_nodes(root):
    """先序展平：给每个节点 id（`N01`…）、level、path、parent_id、children_ids。"""
    nodes = []

    def walk(n: dict, parent: str, path: list) -> str:
        # C-0110 回填：真值 = _work/rebuilt/kp_build/0082-_flat_nodes.walk.py
        # （kp_build.funcs.txt 同条 args=3 vars: n, parent, path, nid, me, c, kid 逐字对上；
        #  kp_build.strings.txt 同条常量 children_ids / children / N%02d / pages / name / path 逐字对上）
        # ⚠ 重建分块把 children_ids 的初值读成了 `list(n.get("pages") or [])`（与下一个键
        #   pages 的值串了）。按本函数的三处消费点必须修正为空列表：normalize_structure_v2
        #   用 `not n['children_ids']` 判叶（leaves_tmp / leaves / is_leaf），而
        #   plan_kp_tasks_v2 又用 `nd['is_leaf']` 选任务 —— 初值塞页号会让**有页的叶节点
        #   都不算叶**：任务清单恒空、normalize 的补页分支还会 leaves[0] IndexError
        #   （本卡实测，见 _c0110_kpbuild.json）。所以这里按语义与 funcs/strings 证据取 []。
        nid = "N%02d" % (len(nodes) + 1)
        me = {
            "id": nid,
            "name": n["name"],
            "level": len(path) + 1,
            "path": path + [n["name"]],
            "parent_id": parent,
            "children_ids": [],
            "pages": list(n.get("pages") or []),
        }
        nodes.append(me)
        for c in (n.get("children") or []):
            kid = walk(c, nid, me["path"])
            me["children_ids"].append(kid)
        return nid

    walk(root, '', [])
    return nodes


def normalize_structure_v2(root, pages_all, deck, log):
    clean = _clean_section(root) if root else None
    fallback = None
    if clean is None or not clean.get('children') and not clean.get('pages'):
        fallback = {
            'reason': '结构模型没有给出可用的树（没有节点 / 没有叶节点页码），退化为单节点结构：整份课件一个节点',
            'node_name': deck or '整份课件',
        }
        clean = {
            'name': (deck or '整份课件')[:24],
            'children': [],
            'pages': list(pages_all),
        }
    leaves_tmp = [n for n in _flat_nodes(clean) if not n['children_ids']]
    if not any(n['pages'] for n in leaves_tmp):
        fallback = {
            'reason': '结构模型给的叶节点一个页码都没写，退化为单节点结构：整份课件一个节点',
            'node_name': (deck or '整份课件')[:24],
        }
        clean = {
            'name': (deck or '整份课件')[:24],
            'children': [],
            'pages': list(pages_all),
        }
    nodes = _flat_nodes(clean)
    leaves = [n for n in nodes if not n['children_ids']]
    known = set(pages_all)

    owner = {}
    conflicts = []
    for nd in leaves:
        for p in nd['pages']:
            if p not in known:
                conflicts.append({
                    'page': p,
                    'kept': '',
                    'dropped': nd['id'],
                    'reason': '页码越出 1~%d，已丢弃' % (max(known) if known else 0),
                })
                continue
            if p in owner:
                conflicts.append({
                    'page': p,
                    'kept': owner[p],
                    'dropped': nd['id'],
                    'reason': '同一页被多个叶节点声明，保留先声明的那个',
                })
                continue
            owner[p] = nd['id']

    filled = []
    for p in sorted(known):
        if p in owner:
            continue
        prev_pages = [q for q in owner if q < p]
        nxt_pages = [q for q in owner if q > p]
        if prev_pages:
            nid = owner[max(prev_pages)]
            why = '结构模型没把这一页放进任何叶节点；归给它前面最近的已归属页 p.%d 所在的节点' % max(prev_pages)
        elif nxt_pages:
            nid = owner[min(nxt_pages)]
            why = '结构模型没把这一页放进任何叶节点；归给它后面最近的已归属页 p.%d 所在的节点' % min(nxt_pages)
        else:
            nid = leaves[0]['id']
            why = '整份结构里没有任何叶节点页码；归给第一个叶节点'
        owner[p] = nid
        filled.append({'page': p, 'node_id': nid, 'reason': why})

    node_pages = {}
    for p, nid in owner.items():
        node_pages.setdefault(nid, []).append(int(p))
    for nid in node_pages:
        node_pages[nid] = sorted(node_pages[nid])

    for nd in nodes:
        nd['pages'] = node_pages.get(nd['id'], [])
        nd['is_leaf'] = not nd['children_ids']

    if conflicts:
        log('结构兜底：%d 处页码冲突（详见产物 meta.coverage.structure_conflicts）' % len(conflicts))
    if filled:
        log('结构兜底：%d 页模型没放进叶节点，已按就近规则补入（详见 structure_filled）' % len(filled))
    if fallback:
        log('★结构兜底：%s' % fallback['reason'])

    return {
        'root': clean,
        'nodes': nodes,
        'page_owner': owner,
        'node_pages': node_pages,
        'conflicts': conflicts,
        'filled': filled,
        'fallback': fallback,
        'n_nodes': len(nodes),
        'n_leaves': len(leaves),
        'max_level': max((n.get('level', 0) for n in nodes), default=0),
    }


def _struct_map_result(parsed, a, b):
    raw_titles = parsed.get("page_titles") or parsed.get("titles") or []
    titles = {}
    if isinstance(raw_titles, dict):
        for k, v in raw_titles.items():
            for n in re.findall("\\d+", str(k)):
                titles[int(n)] = str(v or "").strip()
    elif isinstance(raw_titles, (list, tuple)):
        seq = list(range(a, b + 1))
        for i, item in enumerate(raw_titles):
            if isinstance(item, dict):
                pno = item.get("page") or item.get("p") or item.get("n")
                txt = item.get("title") or item.get("name") or ""
                try:
                    pno = int(pno)
                except (TypeError, ValueError):
                    pno = seq[i] if i < len(seq) else 0
            else:
                txt, pno = str(item or ""), seq[i] if i < len(seq) else 0
            if not pno:
                continue
            titles[int(pno)] = str(txt or "").strip()[:24]
    secs = []
    for x in parsed.get("sections") or parsed.get("structure") or []:
        c = _clean_section(x)
        if not c:
            continue
        secs.append(c)
    return {"titles": titles, "sections": secs}


def gen_structure_v2(doc_id: int, ch: dict, prompt: dict, deck: str, *,
                     calls: list, log=print, run_no: int = 1) -> dict:
    """生成这份课件的**思维导图结构**（章节树）：map 分段 + reduce 归并。

    - 段数 == 1：不调第二次模型，本地把这一段挂到课件名下面（`merged_by: "local"`）；
    - 段数 ≥ 2：一次归并调用（`merged_by: "model"`）；归并失败/输出不成树时
      **退化为"课件名 + 各段章节并列"**（`merged_by: "fallback"`），不静默放弃。
    段与段**完全独立**（各读各的页区间、各调各的模型），所以卡 C-0063 起
    N 段走 `min(KP_INNER_WORKERS, 段数)` 路并发；归并那一次仍是**单次串行**
    （它必须等所有段回来，本身不可切——C-0062 报告 §4.2 的硬串行咽喉）。
    `KP_INNER_WORKERS=1` 时走的是改造前那条串行路径（逐字相同）。
    """
    pages_all = list(ch["pages_all"])
    segs = plan_structure_segments(ch)
    if not segs:
        raise RuntimeError("这份课件库里没有任何块（chunks 为空），无法生成结构")
    log("结构生成：共 %d 页，切成 %d 段（每段 ≤%d 页 / ≤%d 字符）"
        % (len(pages_all), len(segs), STRUCT_SEG_PAGES, STRUCT_SEG_CHARS))

    def _map_one_seg(seg: dict) -> dict:
        """一段 map：渲染材料 → 调模型 → 解析成局部草稿（顺序无关，可并发）。"""
        user = (prompt["user"]
                .replace("{deck}", deck)
                .replace("{n_pages}", str(len(pages_all)))
                .replace("{a}", str(seg["a"]))
                .replace("{b}", str(seg["b"]))
                .replace("{k}", str(len(seg["pages"])))
                .replace("{pages_material}", render_struct_material(seg, ch)))
        res = _v2_call(calls, "structure.map", prompt["sys"], user,
                       max_tokens=STRUCT_MAX_TOKENS, retry=STRUCT_RETRY, log=log,
                       note="第 %d/%d 段（p.%d~%d）"
                            % (seg["index"], len(segs), seg["a"], seg["b"]),
                       temperature=STRUCT_TEMPERATURE, run_no=run_no,
                       thinking=THINKING_V2, reasoning_effort=REASONING_EFFORT)
        if not res["ok"]:
            raise RuntimeError("结构分段分析失败（第 %d 段 p.%d~%d）：%s"
                               % (seg["index"], seg["a"], seg["b"], res["error"]))
        r = _struct_map_result(res["parsed"], seg["a"], seg["b"])
        log("  段 %d/%d（p.%d~%d）：%d 页短标题、%d 个章节草稿"
            % (seg["index"], len(segs), seg["a"], seg["b"],
               len(r["titles"]), len(r["sections"])))
        return {"segment": seg["index"], "a": seg["a"], "b": seg["b"],
                "pages": list(seg["pages"]), "titles": r["titles"],
                "sections": r["sections"], "call_index": res["call_index"]}

    seg_workers = max(1, min(inner_workers(), len(segs)))
    if seg_workers > 1:
        # 卡 C-0063：N 段并行。`Executor.map` **保序返回**，所以下面的
        # `drafts` 顺序、`titles` 合并顺序与串行路径完全一致——
        # 归并那一次调用的输入（render_struct_drafts / render_struct_titles）
        # 因此逐字节相同，产物口径不变。
        log("结构分段并发：%d 段走 %d 路（进程级模型闸门 %d）"
            % (len(segs), seg_workers, model_gate_limit()))
        with _cf.ThreadPoolExecutor(max_workers=seg_workers,
                                    thread_name_prefix="kp-seg") as ex:
            drafts = list(ex.map(_map_one_seg, segs))
    else:
        drafts = [_map_one_seg(seg) for seg in segs]
    titles: dict = {}
    for _d in drafts:
        titles.update(_d["titles"])

    if len(drafts) == 1:
        root = {"name": deck[:24],
                "children": drafts[0]["sections"] or [
                    {"name": "内容", "children": [], "pages": list(pages_all)}]}
        merged_by, note = "local", "只有一段：本地把该段章节挂到课件名下（省一次归并调用）"
    else:
        t_text = render_struct_titles(pages_all, titles, ch)
        d_text = render_struct_drafts(drafts)
        user = (prompt["merge_user"]
                .replace("{deck}", deck)
                .replace("{n_pages}", str(len(pages_all)))
                .replace("{titles}", t_text)
                .replace("{drafts}", d_text))
        # ⚠ 归并**保持单次串行**（卡 C-0063 明确不动它）：
        #   ① 它必须等**所有** map 段回来才有输入，本身就是一道屏障，切不开；
        #   ② 全份只有这一次调用（doc#5 实测 87.9 s），没有第二条可并行的调用；
        #   ③ 它的输入随段数线性增长，段数越多它越慢——C-0062 报告 §4.2 把它
        #      列为"硬串行咽喉"，并预测它会是并发改造后的新主导项（13% → 34%~47%）。
        res = _v2_call(calls, "structure.merge", prompt["merge_sys"], user,
                       max_tokens=STRUCT_MAX_TOKENS, retry=STRUCT_RETRY, log=log,
                       note="归并 %d 段为全局结构" % len(drafts),
                       temperature=STRUCT_TEMPERATURE, run_no=run_no,
                       thinking=THINKING_V2, reasoning_effort=REASONING_EFFORT)
        root = _clean_section(res["parsed"].get("root")) if res["ok"] else None
        if root and not root.get("children"):
            root = None                    # 归并输出退化成一个节点 → 不当结构用
        if root:
            merged_by, note = "model", "归并调用成功（%d 段 → 一棵树）" % len(drafts)
        else:
            kids = [s for d in drafts for s in d["sections"]]
            root = {"name": deck[:24],
                    "children": kids or [{"name": "内容", "children": [],
                                          "pages": list(pages_all)}]}
            merged_by = "fallback"
            note = ("归并调用未给出可用树（%s），退化为：课件名 + 各段章节并列"
                    % (res.get("error") or "输出不成树"))
            log("★结构归并降级：%s" % note)
    log("结构：%s（merged_by=%s）" % (note, merged_by))
    return {"root": root, "raw_drafts": drafts, "titles": titles,
            "segments": [{k: v for k, v in s.items() if k != "chars"} for s in segs],
            "merged_by": merged_by, "merge_note": note,
            "params": {"seg_pages": STRUCT_SEG_PAGES, "seg_chars": STRUCT_SEG_CHARS,
                       "page_chars": STRUCT_PAGE_CHARS, "temperature": STRUCT_TEMPERATURE,
                       "max_tokens": STRUCT_MAX_TOKENS,
                       "thinking": THINKING_V2,
                       "reasoning_effort": (REASONING_EFFORT if THINKING_V2 else "")}}


# ---------------------------------------------------------------- 知识点：切片与生成


def _split_runs(ps: list) -> list:
    """把升序页号切成若干**连续段**：`[1,2,3,7,8]` → `[[1,2,3],[7,8]]`。"""
    runs, cur = [], []
    for p in ps:
        if cur and p != cur[-1] + 1:
            runs.append(cur)
            cur = []
        cur.append(p)
    if cur:
        runs.append(cur)
    return runs


def _page_material_chars(p, ch):
    """该页全部块的字符数（跨页块在它覆盖的每一页都算一次，故意保守）。"""
    # C-0111 连带修复：真值（v1.1 原始字节码同函数 + 其 genexpr）逐字为
    #   return sum(len(b.get('text') or '') for b in (ch['blocks'].get(p) or []))
    # 旧重建版写 `ch.get("blocks") or []`：ch["blocks"] 是 **{页: [块]} 的字典**，
    # 直接遍历拿到的是**页号 int** ⇒ AttributeError: 'int' object has no attribute 'get'
    # （而且它整个函数根本没用入参 p —— 一眼可判为重建臆造）。它就挡在
    # plan_kp_tasks_v2 的按页切片上，也就是「整理知识点」的第二步。
    if not ch:
        return 0
    return sum(len(b.get("text") or "") for b in (ch["blocks"].get(p) or []))


def plan_kp_tasks_v2(nodes, page_owner, span=0, ch=None, chars_max=24000):
    """由**结构叶节点**推出知识点任务清单。

    卡 C-0052 起：**一个结构叶节点 = 一条知识点**（人拍板解除"每条 ≤3 页"）。
    `span` 留给旧口径调用（>0 时才按连续段切片），默认 0 = 不限。

    页归属仍来自 `page_owner`（**全页唯一**），所以：
      - 同一页不可能落到两条知识点（`overlap_pages` 恒为空，可机器判定）；
      - 每一页都在且只在一个任务里（覆盖率 = 100%，除非预算/失败提前停）。

    唯一的例外是**调用可行性软上限**：该叶节点的材料字符数超过 `chars_max`
    （默认 24000 字 ≈ 15000 token）时，按**连续页**边界再切一条，
    每片带 `split_reason`。这不是"页数限制"，是防止单次调用把上下文撑爆。
    """
    tasks = []
    for nd in nodes:
        if not nd.get("is_leaf"):
            continue
        ps = sorted((p for p, o in page_owner.items() if o == nd["id"]))
        pieces = []
        for run in _split_runs(ps):
            if span and span > 0:
                for i in range(0, len(run), span):
                    part = run[i:i + span]
                    pieces.append((part, sum(ch[p] for p in part),
                                   "v2 口径：叶节点内连续 ≤%d 页切片" % span))
                continue
            cur = []
            cur_chars = 0
            for p in run:
                n = _page_material_chars(p, ch)
                if ch is not None and cur and cur_chars + n > chars_max:
                    pieces.append((cur, cur_chars,
                                   "材料 %d 字超过单次调用软上限 %d 字，按连续页切开" % (cur_chars + n, chars_max)))
                    cur, cur_chars = [], 0
                cur.append(p)
                cur_chars += n
            if not cur:
                continue
            pieces.append((cur, cur_chars, ""))
        for k, (pages_, chars_, why) in enumerate(pieces, 1):
            t = {
                "kp_id": "KP-%s-%d" % (nd["id"], k),
                "node_id": nd["id"],
                "node_name": nd["name"],
                "node_path": list(nd["path"]),
                "level": nd["level"],
                "pages": pages_,
                "material_chars_planned": chars_,
            }
            if why:
                t["split_reason"] = "%s（第 %d/%d 片）" % (why, k, len(pieces))
            tasks.append(t)
    return tasks


def render_kp_material(pages: list, node_path: list, ch: dict) -> tuple:
    """渲染一条知识点的材料（页内先正文块后识图块，每块带自己的出处）+ 材料清单。"""
    figrows = ch["figrows"]
    lines: list = []
    inv: list = []
    for p in pages:
        lines.append("===== 第 %d 页 =====" % p)
        lines.append("【本条所属结构节点】%s" % " > ".join(node_path))
        if (figrows.get(p) or {}).get("no_info"):
            lines.append("（注：本页图片已被判定为\u201c无知识性图示\u201d，"
                         "下面的图片理解只是占位，不含知识内容）")
        bs = sorted(ch["blocks"].get(p) or [], key=lambda x: (x["ord"], x["chunk_id"]))
        figs = [b for b in bs if b["kind"] == "图片理解"]
        body = [b for b in bs if b["kind"] != "图片理解"]
        for b in body:
            lines.append("〔正文块〕[%d]（%s）原文：%s"
                         % (b["chunk_id"], b["ref"], b["text"]))
            inv.append({"chunk_id": b["chunk_id"], "page": p, "kind": "正文块",
                        "ref": b["ref"], "chars": b["chars"], "pages": b["pages"]})
        for k, b in enumerate(figs, 1):
            lines.append("〔图片理解·%d/%d〕[%d]（%s）%s"
                         % (k, len(figs), b["chunk_id"], b["ref"], b["text"]))
            inv.append({"chunk_id": b["chunk_id"], "page": p, "kind": "图片理解",
                        "ref": b["ref"], "chars": b["chars"], "fig_index": k,
                        "fig_total": len(figs), "pages": b["pages"]})
        lines.append("")
    if not inv:
        lines.append("（本次材料为空）")
    return "\n".join(lines), inv


def _block_text_map(ch: dict) -> dict:
    """chunk_id → 块原文（名词出处回查用）。"""
    return {b["chunk_id"]: (b.get("text") or "")
            for bs in ch["blocks"].values() for b in bs}


def _page_exclusions(raw, cand_set: set) -> tuple:
    """读模型给的 `excluded_pages`（卡 C-0052）。

    认三种写法：`[3]` / `["3"]` / `[{"page":3,"reason":"目录页"}]`。
    返回 `({page: 理由}, {垃圾项: 剔除原因})`——垃圾项**照样留痕**，不静默吞掉。
    """
    ok: dict = {}
    bad: dict = {}
    if isinstance(raw, (dict, int, str)):
        raw = [raw]
    for x in (raw or []):
        reason = ""
        if isinstance(x, dict):
            page = x.get("page") or x.get("p") or x.get("n")
            reason = str(x.get("reason") or x.get("why") or "").strip()
        else:
            page = x
        try:
            p = int(str(page).strip())
        except (TypeError, ValueError):
            bad[str(page)[:20]] = "excluded_pages 里的项不是页号：%s" % str(x)[:60]
            continue
        if p not in cand_set:
            bad[str(p)] = "页 %d 不在本条候选页里，排除无效（越界页本来就不属于本条）" % p
            continue
        ok[p] = reason or "（模型没给理由）"
    return ok, bad


def _clean_terms(raw_terms, inv, ch, mat_text):
    """校验模型提炼的**重点名词**：`term` 必须真的出自本条材料。

    卡 C-0052 起这个函数服务"逐条随知识点提炼"；卡 C-0058 把那件事摘出后，
    它改由**独立的名词解释功能**调用（`build_terms()` 的提炼步），**语义与判据一字未改**：

    - `term` 在材料里找不到 → **剔除并留痕**（不许编名词，这是"出自材料"的机器判据）；
    - `en` 允许材料里没有（中文课件大多不给英文），但要标明来源：
      材料里有 → `en_source="material"`；没有 → `"model"`（通行英文名，供对照）；
    - `def` 必须 ≥6 字（中文解释不能空着）；
    - `evidence` 是**管线自己算的**：哪些块里出现了这个词（可回查），
      `first_page` 取证据里第一块的页。
    """
    by_id = _block_text_map(ch)
    mat_low = (mat_text or "").lower()
    terms = []
    rejected = []
    seen = set()
    for x in (raw_terms or []):
        if not isinstance(x, dict):
            rejected.append({
                "term": str(x)[:40],
                "reason": "不是对象（要 term/en/def 三个键）",
            })
            continue
        term = re.sub(
            "\\s+", " ",
            str(x.get("term") or x.get("name") or ""),
        ).strip()[:60]
        en = re.sub(
            "\\s+", " ",
            str(x.get("en") or x.get("english") or ""),
        ).strip()[:80]
        dfn = re.sub(
            "\\s+", " ",
            str(x.get("def") or x.get("definition") or x.get("explain") or ""),
        ).strip()[:300]
        if not term:
            rejected.append({"term": "", "reason": "term 为空"})
            continue
        key = _norm(term).lower()
        if key in seen:
            rejected.append({"term": term, "reason": "同一条里重复出现"})
            continue
        if len(_norm(term)) < 2:
            rejected.append({"term": term, "reason": "term 少于 2 字"})
            continue
        if key not in mat_low:
            rejected.append({
                "term": term,
                "reason": "本条材料里找不到这个词，剔除（不许编名词）",
            })
            continue
        if len(_norm(dfn)) < 6:
            rejected.append({"term": term, "reason": "def 缺失或短于 6 字"})
            continue
        ev = [
            {
                "chunk_id": m["chunk_id"],
                "page": m["page"],
                "ref": m["ref"],
            }
            for m in inv
            if key in _norm(by_id.get(m["chunk_id"]) or "").lower()
        ]
        seen.add(key)
        terms.append({
            "term": term,
            "en": en,
            "def": dfn,
            "en_source": (
                "material"
                if en and _norm(en).lower() in mat_low
                else ("model" if en else "")
            ),
            "evidence": ev,
            "first_page": (
                ev[0]["page"]
                if ev
                else (inv[0]["page"] if inv else 0)
            ),
        })
    return terms, rejected


def gen_kp_v2(task: dict, ch: dict, prompt: dict, deck: str, *, calls: list,
              log=print, run_no: int = 1, claimed: dict | None = None,
              claimed_lock=None) -> dict:
    """写一条知识点：调模型 → **页仲裁** → 校验 sources → 质量自查 → 组装记录。

    卡 C-0052 起，覆盖页**由模型判断**（`pages` / `excluded_pages`），但**仲裁在管线**
    （人拍板解除 ≤3 页限制的同时，这套不变量一条都不能丢）：

    1. **候选页** = 结构叶节点的页集（`task["pages"]`，全页唯一、互不重叠）；
    2. 模型给的 `pages` **越界的剔除** → `pages_rejected`；
    3. 模型给的 `excluded_pages` **尊重**（不再补回），逐页进 `pages_excluded`，
       这些页会成为产物覆盖率里的 `uncovered` 并带上模型给的理由；
    4. 候选页里**模型没声明、也没排除**的页 → 按结构归属**补入**本条的 `pages_filled`
       （逐页带原因），保证"每页都有主"这条不变量默认成立；
    5. 与**已被别人收录**的页冲突 → 先到先得，剔除并记 `pages_conflict_dropped`。

    `claimed` 是"页 → 收录它的知识点 id"的累计表（由 `build_v2()` 传入）。
    **卡 C-0063 改了它的用法**：并发下"读-改-写"跨了整个模型调用（10~88 秒），
    加锁也救不了（C-0062 报告 §7.1 对抗实验：无锁 / 加锁都出现重叠）。所以
    `build_v2()` 在**提交任务时**就把候选页预约进 `claimed`（pre-claim，见
    `_claim_pages()`），本函数读到的 `claimed` 因此**包含自己预约的那部分**：

      - 页的主人是别人 → 冲突（`pages_conflict_dropped`）；
      - 页的主人是自己（预约）→ 不算冲突，正常收录；
      - 页没有主人（串行续跑的老路径 / 预约失败）→ 正常收录。

    第 5 步的判据因此从"页在 `claimed` 里"改成"页在 `claimed` 里**且主人不是自己**"，
    这是**"调用前预约"能落地的前提**。`claimed_lock` 是保护 `claimed` 的锁
    （并发路径传进来；`None` 表示单线程，不加锁）。
    """
    cand = [int(x) for x in task["pages"]]
    cand_set = set(cand)
    materials, inv = render_kp_material(cand, task["node_path"], ch)
    user = (prompt["user"]
            .replace("{deck}", deck)
            .replace("{structure_path}", " > ".join(task["node_path"]))
            .replace("{node_name}", task["node_name"])
            .replace("{pages}", "、".join("p.%d" % p for p in cand))
            .replace("{materials}", materials))
    res = _v2_call(calls, "knowledge", prompt["sys"], user,
                   max_tokens=KP_MAX_TOKENS, retry=RETRY, log=log,
                   note="%s（p.%s）" % (task["kp_id"], _pages_label(cand)),
                   kp_seq=True, run_no=run_no)
    if not res["ok"]:
        raise RuntimeError("调用失败：%s" % res["error"])
    parsed = res["parsed"]
    mat_text = material_text_of_v2(inv, ch)

    # ---- 页仲裁（第 2~5 步）----
    claimed = claimed if claimed is not None else {}
    declared_raw = _page_list(parsed.get("pages"))
    declared = sorted(p for p in declared_raw if p in cand_set)
    rejected = [{"page": p,
                 "reason": "不在本条候选页（%s）内，已剔除"
                           % _pages_label(cand)}
                for p in declared_raw if p not in cand_set]
    excl, excl_bad = _page_exclusions(parsed.get("excluded_pages"), cand_set)
    should = [p for p in cand if p not in excl]
    # 卡 C-0063：读 `claimed` 要**和预约同一把锁**（并发下别的线程正在预约/收敛）。
    # `holder[p]` = 这一页现在的主人；`mine_id` = 本条自己（它在提交任务时已经
    # 把自己的候选页预约进去，所以"主人是自己"必须放行，否则每条都会被自己挡掉）。
    mine_id = str(task.get("kp_id") or "")
    holder: dict = {}
    if claimed:
        _cm = claimed_lock if claimed_lock is not None else contextlib.nullcontext()
        with _cm:
            holder = {p: str(claimed.get(p) or "") for p in should if p in claimed}
    taken = {p: who for p, who in holder.items() if who and who != mine_id}
    conflict = [{"page": p, "by": taken[p],
                 "reason": "这一页已被更早生成的知识点 %s 收录，按先到先得剔除" % taken[p]}
                for p in should if p in taken]
    final = [p for p in should if p not in taken]
    if declared:
        filled = [{"page": p,
                   "reason": "模型没声明这一页，按结构叶节点 %s 的候选页归属补入"
                             % task["node_id"]}
                  for p in final if p not in declared]
    else:
        filled = [{"page": p,
                   "reason": "模型没给 pages，按结构叶节点 %s 的候选页全量归属"
                             % task["node_id"]}
                  for p in final]
    pages_excluded = [{"page": p, "reason": r,
                       "note": "模型显式排除，管线尊重、不再补回（该页会进覆盖率未覆盖清单）"}
                      for p, r in sorted(excl.items())]
    pages_empty_reason = ""
    if not final:
        pages_empty_reason = ("本条最终一个页都没有：候选页 %s 全部被排除或被更早的知识点收录"
                              % _pages_label(cand))

    # ---- 出处校验 ----
    in_mat = {m["chunk_id"] for m in inv}
    srcs, dropped = [], []
    for s in (parsed.get("sources") or []):
        if not isinstance(s, dict):
            continue
        ids = []
        for x in (s.get("chunk_ids") or []):
            try:
                cid = int(x)
            except (TypeError, ValueError):
                continue
            (ids if cid in in_mat else dropped).append(cid)
        srcs.append({"page": s.get("page"), "chunk_ids": ids,
                     "figure_desc": bool(s.get("figure_desc"))})

    # ---- 名词校验（卡 C-0058：这条管线不再提炼名词，整块摘到独立功能）----

    summary = str(parsed.get("summary") or "").strip()
    points = [str(x).strip() for x in (parsed.get("points") or []) if str(x or "").strip()]
    q_main = quality_issues(summary, points, mat_text)
    rec = {
        "id": task["kp_id"],
        "doc_id": int(task.get("doc_id") or 0),
        "name": str(parsed.get("name") or task["node_name"]).strip()[:40],
        "summary": summary,
        "points": points,
        "pages": final,
        "candidate_pages": cand,
        "pages_from_model": declared,
        "pages_filled": filled,
        "pages_rejected": rejected + [{"page": None, "reason": v}
                                      for v in excl_bad.values()],
        "pages_excluded": pages_excluded,
        "pages_conflict_dropped": conflict,
        "pages_empty_reason": pages_empty_reason,
        "pages_rule": ("覆盖页由模型判断（pages / excluded_pages），管线仲裁：越界剔除 → "
                       "显式排除尊重 → 与他人冲突先到先得 → 漏声明按结构归属补入；"
                       "四类处理都逐页留痕"),
        "node_id": task["node_id"],
        "node_name": task["node_name"],
        "node_path": list(task["node_path"]),
        "level": int(task.get("level") or len(task["node_path"])),
        "sources": srcs,
        "sources_rejected": sorted(set(dropped)),
        "insufficient": bool(parsed.get("insufficient")),
        "insufficient_reason": str(parsed.get("insufficient_reason") or ""),
        "quality": q_main,
        "material": [{"chunk_id": m["chunk_id"], "kind": m["kind"], "ref": m["ref"],
                      "chars": m["chars"], "page": m["page"],
                      "pages": m.get("pages") or [m["page"]]} for m in inv],
        "material_chars": sum(m["chars"] for m in inv),
        "n_material_blocks": len(inv),
        "n_body_blocks": sum(1 for m in inv if m["kind"] == "正文块"),
        "n_fig_blocks": sum(1 for m in inv if m["kind"] == "图片理解"),
        "usage": res["usage"],
        # 卡 C-0063：耗时从**这次调用自己的记账对象**取（不再 `calls[下标]`
        # 二次索引——并发下别的线程可能已经往 `calls` 里插了新记录）。
        "elapsed_s": float((res.get("call") or {}).get("elapsed_s") or 0),
        "model": res["model"],
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "raw": res["raw"],
    }
    if task.get("split_reason"):
        rec["split_reason"] = task["split_reason"]
    rec["material_block_ids"] = [m["chunk_id"] for m in inv]
    return rec


def material_text_of_v2(inv: list, ch: dict) -> str:
    """本条材料去空白后的全文（质量自查用：判"整句照抄"与"数字/专名越界"）。"""
    by_id: dict = {}
    for bs in ch["blocks"].values():
        for b in bs:
            by_id[b["chunk_id"]] = b["text"]
    return _norm("".join(by_id.get(m["chunk_id"], "") for m in inv))


def _v2_cost(calls: list, run: int | None = None) -> dict:
    """逐次调用记录的汇总（含失败轮；失败的轮次 token 为 0，但次数照样算）。

    `run` 给定就只统计那一轮（"本次运行花了多少"），不给就统计全部（"这份产物
    从头到现在花了多少"）——两个口径都要有，见 `_v2_meta()`。
    """
    rows = [c for c in calls
            if run is None or int(c.get("run") or 1) == int(run)]
    ti = sum(int(c.get("tokens_in") or 0) for c in rows)
    to = sum(int(c.get("tokens_out") or 0) for c in rows)
    rt = sum(int(c.get("reasoning_tokens") or 0) for c in rows)
    cost = round(sum(float(c.get("cost_cny") or 0) for c in rows), 6)
    return {"calls": len(rows), "calls_failed": sum(1 for c in rows if not c.get("ok")),
            "tokens_in": ti, "tokens_out": to, "reasoning_tokens": rt,
            "cost_cny": cost}


def _v2_runs(calls: list) -> list:
    """按**轮**汇总（第几轮、几次调用、几次失败、token、花费、起止时间）。

    "逐次调用记录"与"每轮一共花了多少"是两种读法，产物里都给：
    前者是 `meta.calls`，后者是本节。
    """
    agg: dict = {}
    for c in calls:
        r = int(c.get("run") or 1)
        d = agg.setdefault(r, {"run": r, "calls": 0, "calls_failed": 0,
                               "tokens_in": 0, "tokens_out": 0,
                               "reasoning_tokens": 0, "cost_cny": 0.0,
                               "first_at": "", "last_at": ""})
        d["calls"] += 1
        if not c.get("ok"):
            d["calls_failed"] += 1
        d["tokens_in"] += int(c.get("tokens_in") or 0)
        d["tokens_out"] += int(c.get("tokens_out") or 0)
        d["reasoning_tokens"] += int(c.get("reasoning_tokens") or 0)
        d["cost_cny"] = round(d["cost_cny"] + float(c.get("cost_cny") or 0), 6)
        at = str(c.get("at") or "")
        if at:
            # 卡 C-0063：并发下 `calls` 的**顺序是"完成顺序"**，不再等于时间顺序，
            # 所以首末时刻按字典序取极值（"%Y-%m-%dT%H:%M:%S" 的字典序 = 时间序）。
            # 串行时结果与改造前**逐字相同**（原来就是按顺序首尾取）。
            if not d["first_at"] or at < d["first_at"]:
                d["first_at"] = at
            if not d["last_at"] or at > d["last_at"]:
                d["last_at"] = at
    return [agg[k] for k in sorted(agg)]


# ---------------------------------------------------------------- 名词解释（独立功能）
#
# 卡 C-0052 建的这两件（汇总去重 / 全局筛选）原来长在知识点总结管线身上；
# 卡 C-0058 按人拍板「取消知识点总结管线与名词解释功能的偶联」把它们**摘出来**，
# 与"提炼"一起组成下面这个**独立功能**（代码在本文件、入口是 `build_terms()`、
# CLI 是 `tools/terms_build.py`、提示词真源是 `提示词-名词解释.md`、产物是
# `logs/knowledge/<doc_id>-名词解释.json`）。下面两个函数**语义一字未改**，
# 只是从"总结管线的第 3/4 步"变成"独立功能的第 2/3 步"。


TERMS_PROMPT_NAME = "提示词-名词解释.md"


TERMS_SUFFIX = "-名词解释.json"


def _norm_term_key(s):
    """名词去重键：去空白 / 连字符 / 括号 / 全角符号，大小写不敏感。

    `拓扑异构酶 II` 与 `拓扑异构酶II`、`Topo II` 与 `topo-ii` 会归到同一个键——
    同一件事的两个写法不该在产物里各占一条。
    """
    return re.sub(r"[\s\-_·・（）()【】\[\]｛｝{}、,，.。/／]+", "", s or "").lower()


def collect_terms_v2(recs):
    """把若干"来源条目"提炼的名词**汇总去重**成一份名词表。

    卡 C-0052 起它吃的是**知识点 rec**（`id` / `name` / `pages` / `terms`），
    卡 C-0058 摘出后由独立功能调用，吃的是**段**：`build_terms()` 给每段造一条同形状的
    记录（`id="T01"`、`name="第 1~12 页"`、`pages=[...]`、`terms=[...]`），
    于是**这段逻辑一行都不用重写、字段语义也不漂**。

    口径（K4 的展示要直接吃这个结构）：

    - 去重键 = `_norm_term_key(term)`；
    - `first_kp_id` / `first_kp_name` / `first_page` = **第一次出现**它的那条知识点。
      顺序口径是 `recs` 的传入顺序，`build_v2()` 传的是**按 pages 升序排好的**列表，
      所以"点名词跳到首次出现的知识点"直接用 `first_kp_id`，与页面顺序一致；
    - 同一个名词在多条里出现时：解释取**最长**的那条（信息更全），英文取第一个非空，
      `kp_ids` 列全部出现过的知识点，`evidence` 合并去重；
    - `evidence` 是**管线自己算的**（名词出现在哪些块里），不是模型说的。
    """
    agg = {}
    order = []
    for r in recs:
        if not isinstance(r, dict):
            continue
        for t in (r.get("terms") or []):
            key = _norm_term_key(t.get("term"))
            if not key:
                continue
            d = agg.get(key)
            if d is None:
                agg[key] = {
                    "term": t.get("term"),
                    "en": t.get("en") or "",
                    "en_source": t.get("en_source") or "",
                    "def": t.get("def") or "",
                    "first_kp_id": r.get("id") or "",
                    "first_kp_name": r.get("name") or "",
                    "first_page": int(t.get("first_page") or (r.get("pages") or [0])[0] or 0),
                    "kp_ids": [r.get("id") or ""],
                    "evidence": list(t.get("evidence") or []),
                    "n_occurrences": 1,
                }
                order.append(key)
                continue
            d["n_occurrences"] += 1
            if (r.get("id") or "") not in d["kp_ids"]:
                d["kp_ids"].append(r.get("id") or "")
            if len(_norm(t.get("def") or "")) > len(_norm(d["def"])):
                d["def"] = t.get("def") or d["def"]
            if not d["en"] and t.get("en"):
                d["en"], d["en_source"] = t["en"], (t.get("en_source") or "")
            for ev in (t.get("evidence") or []):
                if ev not in d["evidence"]:
                    d["evidence"].append(ev)
    return [agg[k] for k in order]


def filter_terms_v2(terms, prompt, deck, n_pages, *, calls, log, run_no):
    if not terms:
        return {
            "terms": [],
            "filter": "skipped",
            "kept": [],
            "dropped": [],
            "note": "没有候选名词，不调用筛选",
        }
    if not (prompt.get("terms_sys") and prompt.get("terms_user")):
        return {
            "terms": [t["term"] for t in terms],
            "filter": "skipped",
            "kept": [],
            "dropped": [],
            "note": "提示词真源里没有 terms 筛选块（PROMPT-TERMS-SYS/USER），降级为不过滤、保留全部候选",
        }

    lines = []
    for i, t in enumerate(terms, 1):
        lines.append(
            "%d. %s｜英文：%s｜首次出现：p.%s（%s）｜解释：%s"
            % (
                i,
                t.get("term") or "",
                t.get("en") or "（无）",
                t.get("first_page") or "?",
                t.get("first_kp_name") or "",
                t.get("def") or "",
            )
        )

    user = (
        prompt["terms_user"]
        .replace("{deck}", deck)
        .replace("{n_pages}", str(int(n_pages)))
        .replace("{n_terms}", str(len(terms)))
        .replace("{terms}", "\n".join(lines))
    )

    res = _v2_call(
        calls,
        "terms.filter",
        prompt["terms_sys"],
        user,
        max_tokens=TERMS_MAX_TOKENS,
        retry=STRUCT_RETRY,
        log=log,
        note="筛选 %d 个候选名词" % len(terms),
        run_no=run_no,
    )

    if not res["ok"]:
        if int((res.get("usage") or {}).get("tokens_out") or 0) >= TERMS_MAX_TOKENS:
            mt2 = min(TERMS_MAX_TOKENS * 2, KP_MAX_TOKENS)
            cut = int((res.get("usage") or {}).get("tokens_out") or 0)
            log(
                "★筛选输出被 max_tokens 打满（%d/%d token 全在思维链上），加倍到 %d 再试一次"
                % (cut, TERMS_MAX_TOKENS, mt2)
            )
            res = _v2_call(
                calls,
                "terms.filter",
                prompt["terms_sys"],
                user,
                max_tokens=mt2,
                retry=0,
                log=log,
                note="筛选 %d 个候选名词（max_tokens 加倍到 %d）" % (len(terms), mt2),
                run_no=run_no,
            )

    if not res["ok"]:
        return {
            "terms": [t["term"] for t in terms],
            "filter": "fallback",
            "kept": [],
            "dropped": [],
            "note": "筛选调用失败（%s），保留全部候选名词" % res["error"],
        }

    keep_raw = res["parsed"].get("keep")
    if keep_raw is None:
        keep_raw = res["parsed"].get("terms") or []
    if isinstance(keep_raw, dict):
        keep_raw = list(keep_raw.keys())
    if not isinstance(keep_raw, (list, tuple)):
        keep_raw = [keep_raw]
    unknown = [str(x) for x in keep_raw if str(x).strip()]

    known = {_norm_term_key(t["term"]) for t in terms}
    matched = [x for x in unknown if _norm_term_key(x) in known]
    if not matched:
        return {
            "terms": [t["term"] for t in terms],
            "filter": "fallback",
            "kept": [],
            "dropped": [],
            "note": "筛选输出的 keep 名单里没有一个词在候选清单内（模型没按清单回答），保留全部候选名词",
        }

    keep_keys = {_norm_term_key(x) for x in matched}
    reasons = res["parsed"].get("drop_reason")
    reasons = reasons if isinstance(reasons, dict) else {}
    known_by_key = {_norm_term_key(t["term"]): t for t in terms}
    kept = [t for t in terms if _norm_term_key(t["term"]) in keep_keys]
    dropped = [
        {
            "term": t["term"],
            "reason": str(
                reasons.get(t["term"])
                or reasons.get(t.get("term") or "")
                or "未进筛选保留名单"
            ),
        }
        for t in terms
        if _norm_term_key(t["term"]) not in keep_keys
    ]

    log(
        "名词筛选：%d 个候选 → 保留 %d 个（筛掉 %d 个）"
        % (len(terms), len(kept), len(dropped))
    )

    return {
        "terms": [t["term"] for t in kept],
        "filter": "model",
        "kept": kept,
        "dropped": dropped,
        "note": "全局筛选调用成功（%d 保留 / %d 筛掉）" % (len(kept), len(dropped)),
        "call_index": res.get("call_index"),
    }


def default_terms_prompt_file() -> Path:
    """名词解释提示词真源：本学科 `logs/knowledge/` 优先、项目根兜底（与其它真源同规矩）。"""
    return _prompt_path_v2(TERMS_PROMPT_NAME)


def load_terms_prompts(path: str | os.PathLike | None = None) -> dict:
    """名词解释的四个提示词块：提炼（`extract_sys`/`extract_user`）+ 筛选（`terms_sys`/`terms_user`）。

    ⚠ `terms_sys` / `terms_user` 这两个键名**故意与卡 C-0052 一致**：
    `filter_terms_v2()` 就是照这两个键取提示词的，于是"筛选"那一步的函数体
    **一字未改**——摘出前后，筛选的口径与降级行为完全一样。
    """
    p = _prompt_path_v2(TERMS_PROMPT_NAME, path)
    return _load_md_prompts(p, [("PROMPT-TERMS-EXTRACT-SYS", "extract_sys"),
                                ("PROMPT-TERMS-EXTRACT-USER", "extract_user"),
                                ("PROMPT-TERMS-SYS", "terms_sys"),
                                ("PROMPT-TERMS-USER", "terms_user")])


def terms_out_file(doc_id: int, out_path: str | os.PathLike | None = None) -> Path:
    """独立名词解释产物的路径：`logs/knowledge/<doc_id>-名词解释.json`。"""
    if out_path:
        return Path(out_path)
    return config.LOG_DIR / "knowledge" / ("%d%s" % (int(doc_id), TERMS_SUFFIX))


TERMS_PAGE_CHARS = 4000


def _terms_page_text(p: int, ch: dict, cap: int = TERMS_PAGE_CHARS) -> str:
    """该页喂给**提炼模型**的文本（截断到 `cap` 字符，截断处标 `……`）。"""
    bs = sorted(ch["blocks"].get(p) or [], key=lambda x: (x["ord"], x["chunk_id"]))
    parts = []
    for b in bs:
        t = re.sub(r"\s+", " ", b["text"] or "").strip()
        if t:
            parts.append("〔%s〕%s" % (b["kind"], t))
    s = " ".join(parts)
    return s if len(s) <= cap else (s[:cap] + "……")


def plan_terms_segments(ch: dict) -> list:
    """把全页切成若干段（提炼的 map 输入）：**页数与字符数双阈值，谁先到就切**。

    与 `plan_structure_segments()` 同一口径，只是每页按 `TERMS_PAGE_CHARS` 截断。
    返回 [{index, a, b, pages, chars}]（升序、无缝覆盖 `pages_all`）。
    """
    pages = list(ch["pages_all"])
    if not pages:
        return []
    segs: list = []
    cur: list = []
    cur_chars = 0
    for p in pages:
        n = len(_terms_page_text(p, ch))
        if cur and (len(cur) >= TERMS_SEG_PAGES or cur_chars + n > TERMS_SEG_CHARS):
            segs.append(cur)
            cur, cur_chars = [], 0
        cur.append(p)
        cur_chars += n
    if cur:
        segs.append(cur)
    return [{"index": i + 1, "a": s[0], "b": s[-1], "pages": list(s),
             "chars": sum(len(_terms_page_text(p, ch)) for p in s)}
            for i, s in enumerate(segs)]


def render_terms_material(seg: dict, ch: dict) -> tuple:
    """渲染一段的材料（页内先正文块后识图块，每块带自己的出处）+ 材料清单。

    材料清单（`inv`）与知识点那一步**同形状**：`_clean_terms()` 判"这个词是不是
    真出自我手里的材料"就靠它，所以摘出前后"不许编名词"的判据没有任何漂移。
    """
    figrows = ch["figrows"]
    lines: list = []
    inv: list = []
    for p in seg["pages"]:
        lines.append("===== 第 %d 页 =====" % p)
        if (figrows.get(p) or {}).get("no_info"):
            lines.append("（注：本页图片已被判定为\u201c无知识性图示\u201d，"
                         "下面的图片理解只是占位，不含知识内容）")
        bs = sorted(ch["blocks"].get(p) or [], key=lambda x: (x["ord"], x["chunk_id"]))
        figs = [b for b in bs if b["kind"] == "图片理解"]
        body = [b for b in bs if b["kind"] != "图片理解"]
        for b in body:
            lines.append("〔正文块〕[%d]（%s）原文：%s"
                         % (b["chunk_id"], b["ref"], b["text"]))
            inv.append({"chunk_id": b["chunk_id"], "page": p, "kind": "正文块",
                        "ref": b["ref"], "chars": b["chars"], "pages": b["pages"]})
        for k, b in enumerate(figs, 1):
            lines.append("〔图片理解·%d/%d〕[%d]（%s）%s"
                         % (k, len(figs), b["chunk_id"], b["ref"], b["text"]))
            inv.append({"chunk_id": b["chunk_id"], "page": p, "kind": "图片理解",
                        "ref": b["ref"], "chars": b["chars"], "fig_index": k,
                        "fig_total": len(figs), "pages": b["pages"]})
        lines.append("")
    if not inv:
        lines.append("（本段材料为空）")
    return "\n".join(lines), inv


_TERMS_FIELDS_V4 = ("term", "en", "en_source", "def", "first_page",
                    "first_kp_id", "first_kp_name", "kp_ids", "n_occurrences",
                    "evidence", "seg_id", "seg_name", "page_from_model")


def extract_terms_seg(seg, ch, deck, prompt, *, calls, log, run_no):
    """提炼**一段**的名词：一次调用 → `_clean_terms()` 逐条校验 → 返回该段的清单。

    模型的 `page` 只是它的说法：产物里的 `first_page` 一律由管线按 `evidence`
    （这个词出现在哪些块里）**自己算**——与卡 C-0052 的口径一致，不采信模型给的页码。
    """
    materials, inv = render_terms_material(seg, ch)
    user = (
        prompt["extract_user"]
        .replace("{deck}", deck)
        .replace("{pages}", "、".join(p for p in seg["pages"]))
        .replace("{materials}", materials)
    )
    res = _v2_call(
        calls,
        "terms.extract",
        prompt["extract_sys"],
        user,
        max_tokens=TERMS_MAX_TOKENS,
        retry=TERMS_RETRY,
        log=log,
        note="提炼第 %d 段（p.%s）的名词" % (seg["index"], _pages_label(seg["pages"])),
        run_no=run_no,
    )
    out = {
        "index": seg["index"],
        "pages": list(seg["pages"]),
        "a": seg["a"],
        "b": seg["b"],
        "chars": seg["chars"],
        "terms": [],
        "rejected": [],
        "error": "",
        "usage": {},
    }
    if not res["ok"]:
        out["error"] = str(res.get("error") or "调用失败")
        return out
    mat_text = material_text_of_v2(inv, ch)
    raw = res["parsed"].get("terms")
    terms, rejected = _clean_terms(raw, inv, ch, mat_text)

    seg_set = {int(p) for p in seg["pages"]}

    pages_by_key = {}
    for x in raw or []:
        if not isinstance(x, dict):
            continue
        try:
            pg = int(x.get("page") or 0)
        except (TypeError, ValueError):
            pg = 0
        if pg not in seg_set:
            continue
        pages_by_key[pg] = _norm_term_key(str(x.get("term") or ""))

    for t in terms:
        t["page_from_model"] = pages_by_key.get(_norm_term_key(t["term"]), 0)

    out["terms"], out["rejected"], out["usage"] = terms, rejected, res["usage"]
    return out


def _kp_page_index(doc_id: int, out_path: str | os.PathLike | None = None) -> dict:
    """**只读**该课件的知识点产物，建 `{页号: (kp_id, kp_name)}`（名词关联用）。

    读不到就返回空表（没有产物 / 旧版 / 坏文件都算），**绝不因此失败**：
    名词解释是独立功能，不能反过来被知识点产物的状态卡住。
    """
    p = out_file(doc_id, out_path)
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    idx: dict = {}
    for kp in (raw.get("knowledge_points") or []):
        if not isinstance(kp, dict):
            continue
        kid, kname = str(kp.get("id") or ""), str(kp.get("name") or "")
        for pg in (kp.get("pages") or []):
            try:
                idx.setdefault(int(pg), (kid, kname))
            except (TypeError, ValueError):
                continue
    return idx


def _terms_meta(est: dict, prompt: dict, segs: list, cand: list, terms: list,
                gate: dict, calls: list, *, started: float, status: str = "done",
                err: str = "", kp_link: dict | None = None,
                rejected: list | None = None) -> dict:
    """独立产物的 `meta`：提示词 sha256 / 每段规模 / 逐次调用 / 花费 / 名词口径。"""
    agg = _v2_cost(calls)
    rt = int(agg.get("reasoning_tokens") or 0)
    to = int(agg.get("tokens_out") or 0)
    kp_link = kp_link or {"read": False, "note": "没有可关联的知识点产物"}
    return {
        "doc_id": int(est["doc_id"]),
        "deck": est.get("deck") or "",
        "title": est.get("title") or "",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "source_card": "C-0058",
        "pipeline_version": TERMS_VERSION,
        "pipeline": ("C-0058 v4 名词解释（**独立功能**，core/kp_build.py::build_terms）："
                     "只读库里的文本（chunks 正文 + 识图文本，按 source_ref 的页号组织）→ "
                     "按 %d 页 / %d 字符双阈值切段，**每段一次模型调用**提炼重点名词"
                     "（term 必须出自该段材料，否则剔除并记 terms_rejected）→ "
                     "按名词去重汇总 → **一次全局筛选调用**（只保留该考名词解释的）→ "
                     "写进本文件。人拍板 2026-09-29：把它从知识点总结管线里摘出来"
                     % (TERMS_SEG_PAGES, TERMS_SEG_CHARS)),
        "inputs": {"read": ["chunks.text", "chunks.source_ref", "chunks.ord",
                            "figure_desc.no_info", "documents.title"],
                   "not_read": ["logs/outline/<doc_id>.json", "chunks.topic",
                                "chunks.heading_path"],
                   "outline_read": False,
                   "terms_artifact": {"read": bool(kp_link.get("read")),
                                      "note": kp_link.get("note") or ""}},
        "prompt_path": prompt["path"],
        "prompt_sha256": prompt["sha256"],
        "prompts": {"chars": prompt["chars"],
                    "blocks": {"extract_sys": len(prompt["extract_sys"]),
                               "extract_user": len(prompt["extract_user"]),
                               "terms_sys": len(prompt["terms_sys"]),
                               "terms_user": len(prompt["terms_user"])}},
        "model": MODEL,
        "params": {"thinking": THINKING_V2,
                   "reasoning_effort": (REASONING_EFFORT if THINKING_V2 else ""),
                   "temperature": TEMPERATURE,
                   "temperature_note": "思考模式下 temperature 不生效（官方行为），保留仅为不动调用签名",
                   "max_tokens": TERMS_MAX_TOKENS,
                   "seg_pages": TERMS_SEG_PAGES, "seg_chars": TERMS_SEG_CHARS,
                   "page_chars": TERMS_PAGE_CHARS},
        "segments": [{"index": s["index"], "a": s["a"], "b": s["b"],
                      "n_pages": len(s["pages"]), "pages": list(s["pages"]),
                      "chars": s["chars"]} for s in segs],
        "n_segments": len(segs),
        "n_candidates": len(cand),
        "n_terms": len(terms),
        "terms_filter": {"mode": gate.get("filter") or "skipped",
                         "note": gate.get("note") or "",
                         "kept": gate.get("kept") or [],
                         "dropped": gate.get("dropped") or [],
                         "rule": ("逐段提炼 → 按名词去重汇总 → 一次全局筛选调用"
                                  "（只保留该考名词解释的）；筛选失败时不过滤、全部保留")},
        "terms_rejected": rejected or [],
        "terms_fields": list(_TERMS_FIELDS_V4),
        "kp_link": kp_link,
        "kp_linked": sum(1 for t in terms if t.get("first_kp_id")),
        "calls": calls,
        "runs": _v2_runs(calls),
        "cost_cny": agg["cost_cny"],
        "cost_rule": ("cost_cny = 每次调用的 (tokens_in/1e6*%.2f + tokens_out/1e6*%.2f) * %.1f"
                      "（思考 token 已在 completion 里）；失败轮 token 记 0，但照样占一行"
                      % (PRICE_IN, PRICE_OUT, FX)),
        "usage_total": {"calls": agg["calls"], "calls_failed": agg["calls_failed"],
                        "tokens_in": agg["tokens_in"], "tokens_out": agg["tokens_out"],
                        "reasoning_tokens": rt},
        "reasoning_token_share": (round(rt / to, 4) if to else 0.0),
        "elapsed_s": round(time.perf_counter() - started, 1),
        "status": status,
        "error": err,
    }


# 独立产物里每个名词的字段（顺序即展示顺序；`_TERMS_FIELDS` 是看板读的字段集）


def estimate_terms(doc_id, prompt_path=None, out_path=None, db_path=None):
    prompt = load_terms_prompts(prompt_path)
    ch = read_text_pages(doc_id, db_path)
    if not ch["pages_all"]:
        return {
            "doc_id": int(doc_id),
            "ok": False,
            "error": "这份课件在库里没有任何块（chunks 为空），没有文本可提炼",
        }
    segs = plan_terms_segments(ch)
    seg_chars = sum(s["chars"] for s in segs)
    n_pages = len(ch["pages_all"])

    est_in = int(
        prompt["chars"] * len(segs) + seg_chars
    ) / CHARS_PER_TOKEN
    est_out = int(est_in * REASON_RATIO) + 500 * len(segs)
    filt_in = int(
        len(prompt["terms_sys"]) + len(prompt["terms_user"])
        + 640 * len(segs)
    ) / CHARS_PER_TOKEN
    filt_out = int(filt_in * REASON_RATIO) + 400
    est_in += filt_in
    est_out += filt_out
    est_cost = round(
        est_in / 1000000.0 * PRICE_IN * FX
        + est_out / 1000000.0 * PRICE_OUT * FX,
        6,
    )

    out = terms_out_file(doc_id, out_path)
    prev = _load_prev(out)
    prev_meta = prev.get("meta") if isinstance(prev.get("meta"), dict) else {}

    return {
        "doc_id": int(doc_id),
        "ok": True,
        "pipeline_version": TERMS_VERSION,
        "title": doc_title_v2(doc_id, db_path),
        "deck": doc_title_v2(doc_id, db_path),
        "n_pages": n_pages,
        "pages_all": list(ch["pages_all"]),
        "max_page": ch["max_page"],
        "n_blocks": ch["n_blocks"],
        "material_chars_total": sum(p["chars"] for p in ch["pages_all"]),
        "segments": [
            {
                "index": s["index"],
                "a": s["a"],
                "b": s["b"],
                "n_pages": len(s["pages"]),
                "chars": s["chars"],
            }
            for s in segs
        ],
        "n_segments": len(segs),
        "seg_pages": TERMS_SEG_PAGES,
        "seg_chars": TERMS_SEG_CHARS,
        "page_chars": TERMS_PAGE_CHARS,
        "thinking": THINKING_V2,
        "reasoning_effort": REASONING_EFFORT,
        "max_tokens": TERMS_MAX_TOKENS,
        "est_calls": len(segs) + 1,
        "est_calls_breakdown": {"extract": len(segs), "filter": 1},
        "est_tokens_in": est_in,
        "est_tokens_out": est_out,
        "est_cost_cny": est_cost,
        "est_rule": "上界估算（含思考 token）：提炼每段一次、共 %d 次；筛选整份一次；输入 token ≈ 字符/%.1f；输出 = 思维链（输入 ×%.1f）+ 清单；单价非高峰 $%.2f/1M 入、$%.2f/1M 出，汇率 %.1f"
        % (len(segs), CHARS_PER_TOKEN, REASON_RATIO, PRICE_IN, PRICE_OUT, FX),
        "prompt_path": prompt["path"],
        "prompt_sha256": prompt["sha256"],
        "out_path": str(out),
        "exists": out.exists(),
        "prev": {
            "pipeline_version": str(prev_meta.get("pipeline_version") or ""),
            "n_terms": len([t for t in (prev.get("terms") or []) if isinstance(t, dict)]),
            "generated_at": str(prev_meta.get("generated_at") or ""),
            "cost_cny": float(prev_meta.get("cost_cny") or 0.0),
        },
        "outline_ignored": True,
    }


def prev_info_terms(doc_id: int, out_path: str | os.PathLike | None = None) -> dict:
    """独立名词解释产物的状态（看板/CLI 判"要不要重新生成"用）。"""
    out = terms_out_file(doc_id, out_path)
    prev = _load_prev(out)
    meta = prev.get("meta") if isinstance(prev.get("meta"), dict) else {}
    terms = [t for t in (prev.get("terms") or []) if isinstance(t, dict)]
    return {
        "exists": out.exists(),
        "path": str(out),
        "pipeline_version": str(meta.get("pipeline_version") or ""),
        "is_current_version": str(meta.get("pipeline_version") or "") == TERMS_VERSION,
        "n_terms": len(terms),
        "n_candidates": int(meta.get("n_candidates") or 0),
        "filter_mode": str((meta.get("terms_filter") or {}).get("mode") or ""),
        "generated_at": str(meta.get("generated_at") or ""),
        "cost_cny": float(meta.get("cost_cny") or 0.0),
        "prompt_sha256": str(meta.get("prompt_sha256") or ""),
        "kp_linked": int(meta.get("kp_linked") or 0),
    }


def build_terms(doc_id: int, *, prompt_path=None, out_path=None, db_path=None,
                dry_run: bool = False, force: bool = False, limit: int = 0,
                log=print) -> dict:
    """**独立功能**：给一份课件生成名词解释（卡 C-0058 从知识点总结管线摘出）。

    流程：读库里的文本 → 按页数/字符双阈值切段 → **每段一次调用**提炼名词
    （逐条校验必须出自该段材料）→ 按名词去重汇总 → **一次全局筛选调用**
    → 只读知识点产物把 `first_kp_id` 关联上（读不到就留空）→ 原子落盘。

    - `limit=N`：只跑前 N 段（调试用），产物照写并如实标 `status="stopped"`；
    - `force=True`：忽略已有本版产物重跑（已有文件会被覆盖，调用前请先备份）；
    - 本函数**不写任何数据库表、不碰知识点产物**。
    """
    started = time.perf_counter()
    est = estimate_terms(doc_id, prompt_path=prompt_path, out_path=out_path,
                         db_path=db_path)
    if not est.get("ok"):
        return {"ok": False, "error": est.get("error"), "doc_id": int(doc_id)}
    if dry_run:
        return {"ok": True, "dry_run": True, **est}

    prompt = load_terms_prompts(prompt_path)
    ch = read_text_pages(doc_id, db_path)
    ch["pages_all"] = list(est["pages_all"])
    deck = est.get("deck") or est.get("title") or ("文档 %d" % int(doc_id))
    out = terms_out_file(doc_id, out_path)
    segs = plan_terms_segments(ch)

    calls: list = []
    prev_meta = {}
    if out.exists() and not force:
        try:
            prev_meta = (_load_prev(out).get("meta") or {})
        except Exception:  # noqa: BLE001
            prev_meta = {}
    for _c in (prev_meta.get("calls") or []):
        if isinstance(_c, dict):
            _c = dict(_c)
            _c.setdefault("run", 1)
            calls.append(_c)
    run_no = max([int(c.get("run") or 1) for c in calls] or [0]) + 1
    log("名词解释（独立功能）：%s（doc#%d）· %d 页 → %d 段 → 预估 %d 次调用 · CNY%.4f"
        % (est.get("title") or "", doc_id, est["n_pages"], len(segs),
           est["est_calls"], est["est_cost_cny"]))
    if calls:
        log("调用账本：这份名词产物里已有 %d 次调用（%d 轮），本轮记为第 %d 轮"
            % (len(calls), len(_v2_runs(calls)), run_no))

    status, err = "done", ""
    seg_recs: list = []
    rejected: list = []
    cand: list = []
    terms: list = []
    gate: dict = {"filter": "skipped", "note": "还没到筛选那一步", "kept": [], "dropped": []}
    kp_link: dict = {"read": False, "note": "还没到关联那一步"}
    try:
        todo = segs[:int(limit)] if limit else segs
        for i, seg in enumerate(todo, 1):
            log("[%d/%d] 提炼 p.%s（%d 页 / %d 字符）"
                % (i, len(segs), _pages_label(seg["pages"]), len(seg["pages"]),
                   seg["chars"]))
            one = extract_terms_seg(seg, ch, deck, prompt, calls=calls, log=log,
                                    run_no=run_no)
            if one["error"]:
                log("  ★第 %d 段提炼失败：%s（这一段当空处理，不编名词）"
                    % (seg["index"], one["error"]))
            for r in one["rejected"]:
                rejected.append(dict(r, seg=seg["index"]))
            seg_recs.append({"id": "T%02d" % seg["index"],
                             "name": "第 %d~%d 页" % (seg["a"], seg["b"]),
                             "pages": list(seg["pages"]),
                             "terms": one["terms"]})
            log("  第 %d 段：%d 个名词（剔除 %d 个）"
                % (seg["index"], len(one["terms"]), len(one["rejected"])))
        if limit and len(todo) < len(segs):
            status = "stopped"

        cand = collect_terms_v2(seg_recs)
        gate = filter_terms_v2(cand, prompt, deck, len(ch["pages_all"]),
                               calls=calls, log=log, run_no=run_no)
        terms = list(gate.get("terms") or [])

        # 关联知识点（**只读**产物；读不到就留空，不影响别名次）
        idx = _kp_page_index(doc_id, out_path)
        seg_name = {r["id"]: r["name"] for r in seg_recs}
        for t in terms:
            key = str(t.get("first_kp_id") or "")
            t["seg_id"], t["seg_name"] = key, seg_name.get(key, "")
            try:
                pg = int(t.get("first_page") or 0)
            except (TypeError, ValueError):
                pg = 0
            pair = idx.get(pg)
            if pair:
                t["first_kp_id"], t["first_kp_name"] = pair[0], pair[1]
                t["kp_ids"] = [pair[0]]
            else:
                t["first_kp_id"], t["first_kp_name"], t["kp_ids"] = "", "", []
        kp_link = ({"read": True,
                    "source": str(out_file(doc_id)),
                    "note": ("只读该课件的知识点产物，按 first_page 关联"
                             "（%d/%d 个名词关联上）"
                             % (sum(1 for t in terms if t.get("first_kp_id")),
                                len(terms)))}
                   if idx else
                   {"read": False,
                    "note": ("没有可用的知识点产物（未整理 / 旧版 / 坏文件），"
                             "first_kp_id 一律留空——名词解释不因此失败")})
        log("名词解释：候选 %d 个 → 产物 %d 个（筛选模式 %s）"
            % (len(cand), len(terms), gate.get("filter")))
    except Exception as e:  # noqa: BLE001
        status, err = "failed", "%s: %s" % (type(e).__name__, e)
        terms = []
        gate = {"filter": "skipped", "note": "整理中止", "kept": [], "dropped": []}
        kp_link = {"read": False, "note": "整理中止：" + err}
        log("★名词解释中止：%s" % err)

    meta = _terms_meta(est, prompt, segs, cand, terms, gate, calls,
                       started=started, status=status, err=err, kp_link=kp_link,
                       rejected=rejected)
    _save({"meta": meta, "terms": terms}, out)
    log("完成：名词 %d 个（候选 %d，筛掉 %d）· %d 次调用 · CNY%.6f · 用时 %.0fs"
        % (len(terms), meta["n_candidates"],
           len((meta.get("terms_filter") or {}).get("dropped") or []),
           meta["usage_total"]["calls"], meta["cost_cny"], meta["elapsed_s"]))
    log("产物：%s" % out)
    return {"ok": True, "status": status, "error": err, "out_path": str(out),
            "meta": meta, "estimate": est}


# ---------------------------------------------------------------- 产物组装


def _v2_coverage(est, recs, norm, tasks):
    owner_kp = {}
    overlap = []
    for r in recs:
        for p in (r.get('pages') or []):
            n = int(p)
            if n in owner_kp and owner_kp[n] != r.get('id'):
                overlap.append(n)
            owner_kp[n] = r.get('id')
    pages_all = list(est.get('pages_all') or [])
    uncovered = [p for p in pages_all if p not in owner_kp]

    excl_reason = {}
    filled_rows = []
    rejected_rows = []
    conflict_rows = []
    n_from_model = 0
    for r in recs:
        kid = r.get('id') or ''
        for x in (r.get('pages_excluded') or []):
            try:
                excl_reason[int(x.get('page'))] = (
                    '知识点 ' + str(kid) + ' 显式排除：'
                    + str(x.get('reason') or '（没给理由）')
                )
            except (TypeError, ValueError):
                continue
        for x in (r.get('pages_filled') or []):
            filled_rows.append({
                'kp_id': kid,
                'page': x.get('page'),
                'reason': x.get('reason') or '',
            })
        for x in (r.get('pages_rejected') or []):
            rejected_rows.append({
                'kp_id': kid,
                'page': x.get('page'),
                'reason': x.get('reason') or '',
            })
        for x in (r.get('pages_conflict_dropped') or []):
            conflict_rows.append({
                'kp_id': kid,
                'page': x.get('page'),
                'by': x.get('by') or '',
                'reason': x.get('reason') or '',
            })
        n_from_model += len(r.get('pages_from_model') or [])

    reasons = {}
    done_ids = {r.get('id') for r in recs}
    for p in uncovered:
        if p in excl_reason:
            reasons[str(p)] = excl_reason[p]
            continue
        t = next((t for t in tasks if p in t['pages']), None)
        if t is None:
            reasons[str(p)] = '这一页没有对应的知识点任务（管线异常，请核对 structure.page_owner）'
        elif t['kp_id'] in done_ids:
            reasons[str(p)] = '任务 %s 已生成但 pages 里没有这一页（异常）' % t['kp_id']
        else:
            reasons[str(p)] = '任务 %s 尚未生成（预算到线/失败停止），下次续跑会补上；逐次调用见 meta.calls' % t['kp_id']

    empty = [p for p in pages_all if p not in (est.get('pages_with_blocks') or [])]

    return {
        'rule': '口径：①页计数分母 = 1..max_page（max_page=%d，取自 chunks.source_ref 的最大页号，**含中间无块的空页**）；分子 = 全部知识点 pages 的并集。②一条知识点 = 一个结构叶节点（页数由模型判断，管线仲裁；只有材料超过单次调用软上限 %d 字时才按连续页再切，见各条 split_reason）。③同一页只允许属于一条知识点（overlap_pages 必须为空）；④页的五类处理（模型声明/管线补入/剔除/显式排除/冲突丢弃）逐页留痕。' % (est.get('max_page') or 0, KP_MATERIAL_CHARS_MAX),
        'pages_total': len(pages_all),
        'max_page': int(est.get('max_page') or 0),
        'pages_with_blocks': len(est.get('pages_with_blocks') or []),
        'pages_empty': empty,
        'n_kps': len(recs),
        'n_kps_with_summary': sum(1 for r in recs if r.get('summary')),
        'n_tasks': len(tasks),
        'covered': sorted(owner_kp),
        'n_covered': len(owner_kp),
        'uncovered': uncovered,
        'uncovered_reasons': reasons,
        'overlap_pages': sorted(set(overlap)),
        'page_owner': {str(p): owner_kp[p] for p in sorted(owner_kp)},
        'page_owner_expected': {str(p): t['kp_id'] for t in tasks for p in t['pages']},
        'structure_page_owner': {str(p): norm['page_owner'][p] for p in sorted(norm['page_owner'])},
        'structure_conflicts': norm['conflicts'],
        'structure_filled': norm['filled'],
        'structure_fallback': norm['fallback'],
        'structure_rule': '页归属来自**本管线生成的结构树**：叶节点先到先得占页（冲突记 structure_conflicts），模型漏掉的页按「就近」补入（记 structure_filled）；不读 logs/outline/<doc_id>.json',
        'pages_rule': '覆盖页由模型判断（pages / excluded_pages），管线仲裁四步：越界剔除 → 显式排除尊重 → 与他人冲突先到先得 → 漏声明按结构归属补入',
        'n_pages_from_model': n_from_model,
        'pages_filled': filled_rows,
        'pages_rejected': rejected_rows,
        'pages_conflict_dropped': conflict_rows,
        'pages_excluded': [{'page': p, 'reason': excl_reason[p]} for p in sorted(excl_reason)],
    }


def start_background(doc_id: int, **kw) -> dict:
    """起一个后台线程跑 `build()`（看板按钮用）。同时只允许一个任务。

    **不能阻塞 HTTP 线程**：整理一份课件要几分钟到几十分钟（每条一次模型调用），
    在请求里同步跑会把看板卡死——入库那边是靠"请求挂很久 + 轮询进度"扛过去的，
    这里直接开线程，请求立刻返回，进度走 `/api/kp-build/progress`。

    `on_done`（2026-10-21 新增，**可选**）：整理成功（产物文件存在）后由后台线程
    调一次 `on_done(doc_id)`，用于"整理完顺手补做教材补充"；回调里的任何异常都被
    吞掉，不影响整理状态/进度。**不传时行为与改造前逐字相同**。
    """
    if is_running():
        return {"started": False,
                "error": "已有一个整理任务在跑，请等它结束（同一时间只允许一个）"}
    with _STATE_LOCK:
        _STATE.update({"running": True, "doc_id": int(doc_id), "kp_done": 0,
                       "kp_total": 0, "cost_cny": 0.0, "stage_index": 0})
    progress.reset()
    progress.begin("整理知识点：doc#%d" % int(doc_id), files_total=1)

    # 可选的整理完成回调（C-0124）：从 kw 里弹出，不传给 build()。
    on_done = kw.pop("on_done", None)

    def _run() -> None:
        try:
            build(doc_id, progress_on=True, **kw)
            if on_done is not None:
                # 只有整理产物真的落盘了才回调（build 中途失败/没产物时不触发）。
                try:
                    if out_file(doc_id).exists():
                        on_done(doc_id)
                except Exception:  # noqa: BLE001
                    pass      # 回调失败绝不影响整理状态/进度（零模型补做，可重试）
        except Exception as e:  # noqa: BLE001
            progress.end("failed", error="%s: %s" % (type(e).__name__, e))
        finally:
            with _STATE_LOCK:
                _STATE["running"] = False

    th = threading.Thread(target=_run, name="kp-build-%d" % int(doc_id), daemon=True)
    th.start()
    return {"started": True, "doc_id": int(doc_id)}


# ================================================================ 卡 C-0049：v2 的 dry-run / 主流程 / 对外入口


def estimate_v2(doc_id, *, prompt_path, struct_prompt_path, db_path):
    kp_prompt = load_kp_prompts_v2(prompt_path)
    st_prompt = load_struct_prompts(struct_prompt_path)
    ch = read_text_pages(doc_id, db_path)
    if not ch["pages_all"]:
        return {
            "doc_id": int(doc_id),
            "ok": False,
            "error": "这份课件在库里没有任何块（chunks 为空），没有文本可整理",
        }
    pages_all = ch["pages_all"]
    segs = plan_structure_segments(ch)
    struct_calls = len(segs) + (1 if len(segs) > 1 else 0)

    per_page = []
    for p in pages_all:
        bs = ch["blocks"].get(p) or []
        per_page.append({
            "page": p,
            "role": "leaf",
            "chars": sum(b.get("chars", 0) for b in bs),
            "n_body": sum(1 for b in bs if b.get("role") == "body"),
            "n_fig": sum(1 for b in bs if b.get("role") == "figure"),
        })
    mat_chars = sum(x["chars"] for x in per_page)
    n = len(pages_all)
    kp_min = max(1, (mat_chars + KP_MATERIAL_CHARS_MAX - 1) // KP_MATERIAL_CHARS_MAX)
    kp_max = n

    seg_chars = sum(s["chars"] for s in segs)
    seg_in_tok = int(st_prompt["chars"] * len(segs) + seg_chars) / CHARS_PER_TOKEN
    merge_in_tok = int(st_prompt["chars"] + 2500) / CHARS_PER_TOKEN
    kp_in_tok = int(kp_prompt["chars"] * kp_max + mat_chars) / CHARS_PER_TOKEN
    est_in = seg_in_tok + merge_in_tok + kp_in_tok
    est_out = (
        int(seg_in_tok * REASON_RATIO)
        + 1200 * len(segs)
        + int(merge_in_tok * REASON_RATIO)
        + 1500
        + int(kp_in_tok * REASON_RATIO)
        + 450 * kp_max
    )
    est_cost = round(
        est_in / 1000000.0 * PRICE_IN * FX
        + est_out / 1000000.0 * PRICE_OUT * FX,
        6,
    )

    out = out_file(doc_id)
    prev = _load_prev(out)
    prev_meta = prev.get("meta") if isinstance(prev.get("meta"), dict) else {}
    prev_prompts = prev_meta.get("prompts") if isinstance(prev_meta.get("prompts"), dict) else {}
    prev_struct = prev.get("structure") if isinstance(prev.get("structure"), dict) else {}
    is_v2 = str(prev_meta.get("pipeline_version") or "") == PIPELINE_VERSION

    reuse_ok = bool(
        is_v2
        and isinstance(prev_struct.get("raw_root"), dict)
        and int(prev_struct.get("n_pages") or 0) == n
        and str((prev_prompts.get("structure") or {}).get("sha256") or "") == st_prompt["sha256"]
        and str((prev_prompts.get("knowledge") or {}).get("sha256") or "") == kp_prompt["sha256"]
    )
    n_plan = int(prev_struct.get("n_leaves") or 0) if reuse_ok else 0

    done = set()
    if is_v2:
        for r in prev.get("knowledge_points") or []:
            if not isinstance(r, dict):
                continue
            if not str(r.get("summary") or "").strip():
                continue
            done.add(str(r.get("id") or ""))

    pages_with_blocks = sorted(ch["cover"])

    out = {
        "doc_id": int(doc_id),
        "ok": True,
        "pipeline_version": PIPELINE_VERSION,
        "title": doc_title_v2(doc_id, db_path),
        "deck": doc_title_v2(doc_id, db_path),
        "n_pages": n,
        "pages_all": pages_all,
        "n_leaf_pages": n_plan or kp_max,
        "n_leaf_plan_source": (
            "产物结构（%d 个叶节点，可续跑）" % n_plan
            if n_plan
            else "上界（每页一条：结构还没生成）"
        ),
        "excluded_parent_pages": [],
        "kp_min": kp_min,
        "kp_max": kp_max,
        "pages": per_page,
        "material_chars_total": mat_chars,
        "window_chars_total": mat_chars,
        "handoff": {},
        "handoff_pages": [],
        "handoff_detail": [],
        "handoff_unplaced": [],
        "window_unclaimed": [],
        "window_claimed_pages": pages_all,
        "page_owner": {},
        "max_window_pages": KP_SPAN_PAGES,
        "windows": {},
        "max_merge_pages": KP_SPAN_PAGES,
        "structure_calls": struct_calls,
        "segments": [
            {"index": s["index"], "a": s["a"], "b": s["b"], "pages": s["pages"]}
            for s in segs
        ],
        "seg_pages": STRUCT_SEG_PAGES,
        "seg_chars": STRUCT_SEG_CHARS,
        "struct_page_chars": STRUCT_PAGE_CHARS,
        "kp_span_pages": KP_SPAN_PAGES,
        "kp_material_chars_max": KP_MATERIAL_CHARS_MAX,
        "thinking": THINKING_V2,
        "reasoning_effort": REASONING_EFFORT,
        "kp_max_tokens": KP_MAX_TOKENS,
        "struct_max_tokens": STRUCT_MAX_TOKENS,
        "budget_unlimited": BUDGET_UNLIMITED,
        "est_calls": struct_calls + kp_max,
        "est_calls_breakdown": {
            "structure": struct_calls,
            "knowledge_max": kp_max,
            "knowledge_min": kp_min,
        },
        "est_tokens_in": est_in,
        "est_tokens_out": est_out,
        "est_cost_cny": est_cost,
        "est_rule": (
            "上界估算（含思考 token）：结构 %d 次（%d 段 + %d 次归并）按每页 ≤%d 字符的材料算；"
            "知识点按**每页一条**的上界算（真实条数由结构树决定，一条 = 一个叶节点、页数不限，"
            "区间 %d~%d 条）；输入 token ≈ 字符/%.1f；输出 = 思维链（输入 ×%.1f，实测 1.19）"
            "+ 正文（结构 %d/次、知识点 %d/条）；单价非高峰 $%.2f/1M 入、$%.2f/1M 出，汇率 %.1f"
            % (
                struct_calls,
                len(segs),
                struct_calls - len(segs),
                STRUCT_PAGE_CHARS,
                kp_min,
                kp_max,
                CHARS_PER_TOKEN,
                REASON_RATIO,
                1200,
                450,
                PRICE_IN,
                PRICE_OUT,
                FX,
            )
        ),
        # C-0115：真值 pyc（estimate_v2 L4054-4055）是 `sorted(done)` 与 `out.exists()`。
        # `done` 装的是**知识点 id 字符串**（真值 L4013 `done.add(str(r.get("id") or ""))`
        # 逐字如此），旧重建版按字段名想当然加了 `int(x)` ⇒ 产物一旦存在、用户第二次点
        # 「开始整理」就 `ValueError: invalid literal for int() with base 10: 'KP-N03-1'`。
        "already_done_pages": sorted(done),
        "already_out": out.exists(),
        "out_path": str(out),
        "prompt_path": str(prompt_path),
        "prompt_sha256": kp_prompt["sha256"],
        "structure_prompt_path": str(struct_prompt_path),
        "structure_prompt_sha256": st_prompt["sha256"],
        "outline_ignored": True,
        "outline_note": (
            "v2/v3/v4 不读 logs/outline/%d.json（那是入库管线的结构成果，只服务知识库）；"
            "结构由本管线的模型调用生成" % int(doc_id)
        ),
        "pages_with_blocks": pages_with_blocks,
        "max_page": max(pages_with_blocks) if pages_with_blocks else 0,
        "n_blocks": sum(len(ch["blocks"].get(p) or []) for p in pages_all),
    }
    return out


def prev_info_v2(doc_id: int, out_path: str | os.PathLike | None = None) -> dict:
    """产物状态（供调用方决定"要不要备份、能不能续跑、能不能跳过"）。

    字段名与 v1 `prev_info_v1()` 一致（看板/批量直接复用），**口径按 v2/v3 解释**：
      - `n_done_pages` / `pages_total` 数的是**知识点条数**
        （v1 数的是页；两者都是"这份产物做到哪儿了"的同一件事）；
      - `resumable`：**本版**（`C-0052-v3`）产物且至少有一条成品 → 可断点续跑
        （结构不必重生成）。旧版（`C-0049-v2` 等）产物 `is_current_version=False`，
        会被判"非本版"而重跑——**这不是坏数据**：它照样能被 `_load_prev()` 读出来、
        看板按 `meta.pipeline_version` 分流显示，只是不参与本版续跑。
      - `n_terms` / `has_terms`：**旧产物**（v2/v3）里有没有名词解释汇总。
        旧 v2 产物连这个键都没有 → 0/False，读它不会抛错；v4 产物也不再有这个键
        （名词已摘出），所以 v4 这里同样是 0/False——**这不是坏数据**。
      - `has_terms_file` / `terms_file`（卡 C-0058 加）：**独立名词解释产物**
        `logs/knowledge/<doc_id>-名词解释.json` 在不在、在哪。看板的名词解释栏
        先看它、再回落看旧产物的顶层 `terms`。
    """
    out = out_file(doc_id, out_path)
    prev = _load_prev(out)
    meta = prev.get("meta") if isinstance(prev.get("meta"), dict) else {}
    ver = str(meta.get("pipeline_version") or "")
    kps = [r for r in (prev.get("knowledge_points") or []) if isinstance(r, dict)]
    done = [r for r in kps if str(r.get("summary") or "").strip()]
    terms = [t for t in (prev.get("terms") or []) if isinstance(t, dict)]
    tfile = terms_out_file(doc_id)
    return {
        "exists": out.exists(),
        "path": str(out),
        "pipeline_version": ver,
        "is_current_version": ver == PIPELINE_VERSION,
        "n_kps": len(kps),
        "n_done_pages": len(done),
        "resumable": bool(done) and ver == PIPELINE_VERSION,
        "pages_total": int(meta.get("kp_total") or 0),
        "cost_cny": float(meta.get("cost_cny_all_calls") or 0.0),
        "generated_at": str(meta.get("generated_at") or ""),
        "source_card": str(meta.get("source_card") or ""),
        # v2 专属：结构是否已在产物里（续跑时不必重生成结构）
        "has_structure": isinstance(prev.get("structure"), dict)
                         and bool((prev.get("structure") or {}).get("raw_root")),
        "structure_prompt_sha256": str(
            ((meta.get("prompts") or {}).get("structure") or {}).get("sha256") or ""),
        # 名词解释（卡 C-0052 加、C-0058 保留）：旧产物看自己的顶层 terms 键
        "n_terms": len(terms),
        "has_terms": bool(terms),
        # 卡 C-0058 专属：独立名词解释产物（名词功能已从本管线摘出）
        "terms_file": str(tfile),
        "has_terms_file": tfile.is_file(),
    }


def _decorate_tree(node: dict, ids: list, idx: list, kp_ids: dict) -> dict:
    """把先序生成的节点 id / 页 / 知识点挂回展示树（顺序与 `_flat_nodes` 完全一致）。"""
    nid = ids[idx[0]]
    idx[0] += 1
    kids = [_decorate_tree(c, ids, idx, kp_ids) for c in (node.get("children") or [])]
    me = {"id": nid, "name": node["name"], "pages": list(node.get("pages") or []),
          "kp_ids": sorted(set(kp_ids.get(nid) or []))}
    if kids:
        me["children"] = kids
    return me


def _v2_structure_block(gen: dict, norm: dict, tasks: list, recs: list,
                        ch: dict) -> dict:
    """产物顶层 `structure`：**模型生成的思维导图**（树 + 节点表 + 页归属）。

    给前端（卡 C-0050）消费的两个入口：
      - `root`：嵌套树，节点带 `id / name / pages / kp_ids / children`——"树 + 点节点看知识点"直接用；
      - `nodes`：扁平节点表（含 `level / path / parent_id / children_ids / is_leaf`），
        需要按层级渲染或做面包屑时用它。
    """
    kp_ids: dict = {}
    for t in tasks:
        kp_ids.setdefault(t["node_id"], [])
    for r in recs:
        kp_ids.setdefault(r.get("node_id") or "", []).append(r.get("id"))
    ids = [n["id"] for n in norm["nodes"]]
    tree = _decorate_tree(gen["root"], ids, [0], kp_ids)
    nodes = []
    for nd in norm["nodes"]:
        nodes.append({"id": nd["id"], "name": nd["name"], "level": nd["level"],
                      "path": list(nd["path"]), "parent_id": nd["parent_id"],
                      "children_ids": list(nd["children_ids"]),
                      "is_leaf": bool(nd["is_leaf"]), "pages": list(nd["pages"]),
                      "kp_ids": sorted(set(kp_ids.get(nd["id"]) or []))})
    # 层级分布（卡 C-0052：人要求"多设计子节点、可以到四五级"，这是核对入口）
    hist: dict = {}
    for nd in norm["nodes"]:
        lv = int(nd["level"])
        h = hist.setdefault(lv, {"level": lv, "nodes": 0, "leaves": 0, "pages": 0})
        h["nodes"] += 1
        if nd["is_leaf"]:
            h["leaves"] += 1
        h["pages"] += len(nd["pages"])
    return {
        "source": "model",
        "generated_by": "core/kp_build.py::gen_structure_v2（整理管线自己的模型调用）",
        "outline_read": False,
        "note": ("这份结构**不复用**入库管线（core/outline.py）的成果，也不读 "
                 "logs/outline/<doc_id>.json；输入只有库里的文本。"),
        "merged_by": gen["merged_by"], "merge_note": gen["merge_note"],
        "params": gen["params"], "segments": gen["segments"],
        "n_nodes": norm["n_nodes"], "n_leaves": norm["n_leaves"],
        "max_level": norm["max_level"],
        "level_histogram": [hist[k] for k in sorted(hist)],
        "level_rule": ("层级深浅由模型按内容判断（提示词已删掉\u201c最多 3 级\u201d的硬限制，"
                       "鼓励四到五级）；管线侧没有层级常数，这里只统计、不裁剪"),
        "page_titles": {str(k): v for k, v in sorted(gen["titles"].items())},
        "segment_drafts": [{"segment": d["segment"], "a": d["a"], "b": d["b"],
                            "n_sections": len(d["sections"]),
                            "sections": [s["name"] for s in d["sections"]]}
                           for d in gen["raw_drafts"]],
        "raw_root": gen["root"],
        "root": tree,
        "nodes": nodes,
        "page_owner": {str(p): nid for p, nid in sorted(norm["page_owner"].items())},
        "n_pages": len(ch["pages_all"]),
    }


def _v2_meta(est, kp_prompt, st_prompt, norm, tasks, recs, calls, *, status, started, budget, struct_reused, failed, err, run_no, budget_unlimited):
    agg = _v2_cost(calls)
    this = _v2_cost(calls, run=run_no)
    done = [r for r in recs if str(r.get('summary') or '').strip()]
    cover = _v2_coverage(est, recs, norm, tasks)
    rt_this = int(this.get('reasoning_tokens') or 0)
    to_this = int(this.get('tokens_out') or 0)
    meta = {
        'doc_id': int(est['doc_id']),
        'chapter': est.get('title') or '',
        'deck': est.get('deck') or est.get('title') or '',
        'source_card': 'C-0058',
        'pipeline': 'C-0058 v4 知识点整理管线（core/kp_build.py）：只读库里的文本（chunks 正文 + 识图文本，按 source_ref 的页号组织）→ 模型生成思维导图结构（%d 页/段、%d 字符/段双阈值 map-reduce，层级由模型判断）→ 一个结构叶节点一条知识点（页数不限、覆盖页由模型判断 + 管线仲裁）。**只出知识点**：名词解释已摘出为独立功能（core/kp_build.py::build_terms / tools/terms_build.py）' % (STRUCT_SEG_PAGES, STRUCT_SEG_CHARS),
        'pipeline_version': PIPELINE_VERSION,
        'inputs': {
            'read': ['chunks.text', 'chunks.source_ref', 'chunks.ord', 'figure_desc.no_info', 'documents.title'],
            'not_read': ['logs/outline/<doc_id>.json', 'chunks.topic', 'chunks.heading_path', '入库结构派生的任何字段'],
            'outline_read': False,
            'note': '整理管线只吃库里的文本；知识库的结构化数据只用于构建知识库（人拍板 2026-09-23）',
        },
        'structure_source': 'model（本管线生成）',
        'structure_reused': bool(struct_reused),
        'prompts': {
            'structure': {
                'path': st_prompt['path'],
                'sha256': st_prompt['sha256'],
                'chars': st_prompt['chars'],
            },
            'knowledge': {
                'path': kp_prompt['path'],
                'sha256': kp_prompt['sha256'],
                'chars': kp_prompt['chars'],
            },
        },
        'prompt_source': kp_prompt['path'],
        'prompt_sha256': kp_prompt['sha256'],
        'model': MODEL,
        'params': {
            'thinking': THINKING_V2,
            'reasoning_effort': REASONING_EFFORT if THINKING_V2 else '',
            'temperature': TEMPERATURE,
            'temperature_note': '思考模式下 temperature 不生效（官方行为：传了不报错、被忽略），保留仅为不动调用签名',
            'max_tokens': KP_MAX_TOKENS,
            'kp_span_pages': KP_SPAN_PAGES,
            'kp_span_pages_note': '0 = 不限（人拍板 2026-09-27 解除 ≤3 页限制）：一个结构叶节点 = 一条知识点',
            'kp_material_chars_max': KP_MATERIAL_CHARS_MAX,
            'structure': {
                'temperature': STRUCT_TEMPERATURE,
                'thinking': THINKING_V2,
                'reasoning_effort': REASONING_EFFORT if THINKING_V2 else '',
                'max_tokens': STRUCT_MAX_TOKENS,
                'seg_pages': STRUCT_SEG_PAGES,
                'seg_chars': STRUCT_SEG_CHARS,
                'page_chars': STRUCT_PAGE_CHARS,
                'level_rule': '层级不设上限，由模型按内容判断（鼓励四到五级）',
            },
        },
        'price_rule': '非高峰 $%.2f/1M 入 + $%.2f/1M 出，汇率 %.1f（同 core/outline.py）；思考 token 计入 completion（出）' % (PRICE_IN, PRICE_OUT, FX),
        'reasoning_rule': '推理模式已开：每次调用 payload 里 `thinking={type:enabled}` + `reasoning_effort="%s"`（core/chat.py::chat_once 原样透传）；思考 token 从 usage.completion_tokens_details.reasoning_tokens 取，逐次记在 meta.calls[].reasoning_tokens' % REASONING_EFFORT,
        'kp_total': len(tasks),
        'kp_done': len(done),
        'kp_actual': len(recs),
        'kp_insufficient': sorted(r['id'] for r in recs if r.get('insufficient')),
        'failed_kp_ids': sorted(set(failed)),
        'pages_total': cover['pages_total'],
        'pages_covered': cover['covered'],
        'pages_pending': cover['uncovered'],
        'coverage': cover,
        'calls': calls,
        'runs': _v2_runs(calls),
        'run_no': int(run_no),
        'cost_rule': 'cost_cny = 每次调用的 (tokens_in/1e6*%.2f + tokens_out/1e6*%.2f) * %.1f（思考 token 已在 completion 里）；失败轮 token 记 0，但**照样占一行**（meta.calls 逐次可查）。cost_cny/usage_total_this_run = 本次（第 %d 轮）；cost_cny_all_calls/usage_total_all_calls = 这份产物从头到现在的累计' % (PRICE_IN, PRICE_OUT, FX, int(run_no)),
        'cost_cny': this['cost_cny'],
        'cost_cny_all_calls': agg['cost_cny'],
        'usage_total_this_run': dict(this, run=int(run_no)),
        'usage_total_all_calls': {
            'calls': agg['calls'],
            'calls_failed': agg['calls_failed'],
            'tokens_in': agg['tokens_in'],
            'tokens_out': agg['tokens_out'],
            'reasoning_tokens': agg.get('reasoning_tokens', 0),
        },
        'budget_cny': float(budget or 0.0),
    }
    meta.update({
        'budget_policy': 'unlimited（人拍板 2026-09-27：解除整理知识点的花费金额限制、预算无上限）：budget_cny 只记录调用方给的值、**不作为停止条件**；真实花费看 cost_cny_all_calls' if budget_unlimited else 'enforced（调用方显式要求硬上限）：本轮累计到 budget_cny 即停',
        'budget_enforced': not budget_unlimited,
        'budget_ignored_value': float(budget or 0.0) if budget_unlimited else 0.0,
        'reasoning_tokens_this_run': rt_this,
        'reasoning_token_share_this_run': round(rt_this / to_this, 4) if to_this else 0.0,
        'est_cost_cny': est.get('est_cost_cny'),
        'est_rule': est.get('est_rule'),
        'structure_calls': (est.get('est_calls_breakdown') or {}).get('structure') or 0,
        'structural_notes': {
            'structure': '结构由本管线生成：%d 段 map + %s；页归属兜底见 coverage.structure_conflicts / structure_filled / structure_fallback' % (len(est.get('segments') or []), '模型归并' if len(est.get('segments') or []) > 1 else '本地合成根'),
            'knowledge': '一条知识点 = 一个结构叶节点（v3 起页数不限、v4 沿用，覆盖页由模型判断）；页归属唯一（page_owner），同一页不会进两条知识点，仲裁过程见 coverage.pages_filled / pages_rejected / pages_excluded / pages_conflict_dropped',
            'terms_moved_out': '卡 C-0058：名词解释已摘出本管线（人拍板 2026-09-29「取消知识点总结管线与名词解释功能的偶联」）；它的口径与账见 logs/knowledge/<doc_id>-名词解释.json',
        },
        'elapsed_s': round(time.perf_counter() - started, 1),
        'status': status,
        'error': err,
        'generated_at': time.strftime('%Y-%m-%dT%H:%M:%S'),
        'stages': list(KP_STAGES),
    })
    return meta


def build_v2(
    doc_id: int,
    *,
    prompt_path=None,
    struct_prompt_path=None,
    out_path=None,
    db_path=None,
    dry_run: bool = False,
    limit: int = 0,
    max_cost: float = 0.0,
    force: bool = False,
    progress_on: bool = False,
    on_event=None,
    log=print,
    budget_unlimited: bool | None = None,
) -> dict:
    """整理一份课件（**v3：只读库里的文本**）。

    流程：读文本 → （必要时）模型生成结构 → 每个叶节点一条知识点 → 原子落盘（**只出知识点**）。

    - **推理模式**：结构与知识点两步都 `thinking=True` + `reasoning_effort="max"`
      （卡 C-0052，人拍板），`max_tokens` 见 `KP_MAX_TOKENS` / `STRUCT_MAX_TOKENS`；
    - **预算**：`budget_unlimited` 默认取模块级 `BUDGET_UNLIMITED=True`
      （人拍板解除花费上限）→ `max_cost>0` 也**只记录不拦停**，写进
      `meta.budget_enforced=False`；显式传 `budget_unlimited=False` 才恢复旧的到线即停；
    - **页数**：不再"每条 ≤3 页"，一个结构叶节点 = 一条知识点；
      覆盖页由模型判断（`pages` / `excluded_pages`），管线仲裁并逐页留痕；
    - **名词**：卡 C-0058 起**不在本管线里**（摘出成独立功能：`build_terms()` /
      `tools/terms_build.py`，产物 `logs/knowledge/<doc_id>-名词解释.json`）：
    - 断点续跑：产物是**本版**（`C-0052-v3`）且提示词 sha256 没变时，**复用已生成的结构**
      （省下结构那几次调用）与已完成的知识点，只补缺的条；
    - `force=True`：忽略已有产物，结构与知识点全部重生成；
    - `limit=N`：最多生成 N 条（调试用）；`progress_on=True`：向 `core/progress.py` 上报
      （看板单份按钮）；`on_event`：批量整理的回调（`core/kp_batch.py`）。
    """
    # C-0115：本函数体按 v1.1 原始字节码（core/kp_build.pyc，L4333-4732）逐行还原。
    # 还原口径：`co_lines()` 按源码行分组 → 逐行翻译；子函数声明**顺序**与真值一致
    # （_claim_pages / _release_pages / spent_now / ordered_recs / persist / _accept_rec / _kp_worker），
    # 因为它们重名于同名的**变量**（spent_now / ordered_recs / _release_pages），
    # 顺序错了就会让函数被变量覆盖 → "'float' object is not callable"。
    unlimited = BUDGET_UNLIMITED if budget_unlimited is None else bool(budget_unlimited)
    started = time.perf_counter()
    est = estimate_v2(doc_id, prompt_path=prompt_path,
                      struct_prompt_path=struct_prompt_path, db_path=db_path)
    if not est.get("ok"):
        return {"ok": False, "error": est.get("error"), "doc_id": int(doc_id)}
    if dry_run:
        return {"ok": True, "dry_run": True, **est}

    kp_prompt = load_kp_prompts_v2(prompt_path)
    st_prompt = load_struct_prompts(struct_prompt_path)
    ch = read_text_pages(doc_id, db_path)
    ch["pages_all"] = list(est["pages_all"])
    deck = est.get("deck") or est.get("title") or ("文档 %d" % int(doc_id))
    out = out_file(doc_id, out_path)
    est["budget_cny"] = float(max_cost or 0.0)

    if progress_on:
        progress.reset()
        progress.begin(
            "整理知识点(v4)：%s（doc#%d）" % (est.get("title") or "", doc_id),
            files_total=1,
        )
        _stage(0, note="读取库里的文本（%d 页 / %d 块）" % (len(ch["pages_all"]), ch["n_blocks"]))
        with _STATE_LOCK:
            _STATE.update({
                "running": True,
                "doc_id": int(doc_id),
                "kp_done": 0,
                "kp_total": 0,
                "cost_cny": 0.0,
            })

    _emit(on_event, {
        "ev": "stage",
        "stage": KP_STAGES[0],
        "stage_index": 0,
        "note": "读取库里的文本（%d 页）" % len(ch["pages_all"]),
    })
    log("推理模式：thinking=True · reasoning_effort=%s · 知识点 max_tokens=%d（思考 token 计入 completion）" % (REASONING_EFFORT, KP_MAX_TOKENS))
    if unlimited:
        log("预算策略：**不限**（人拍板 2026-09-27）。调用方给的 max_cost=%s 只记进 meta，不作为停止条件" % (("%.2f" % max_cost) if max_cost else "0（未给）"))
    else:
        log("预算策略：硬上限 CNY%.2f（调用方显式要求 enforce）" % float(max_cost or 0.0))

    calls = []
    prev_raw = _load_prev(out)
    prev = {} if force else prev_raw
    prev_meta = prev_raw.get("meta") if isinstance(prev_raw.get("meta"), dict) else {}

    # 账本继承：产物里已有的调用记录（带 run 轮次）原样接过来，本轮记为下一轮。
    calls = []
    for _c in (prev_meta.get("calls") or []):
        if not isinstance(_c, dict):
            continue
        _c = dict(_c)
        _c.setdefault("run", 1)
        calls.append(_c)
    run_no = max([int(c.get("run") or 1) for c in calls] or [0]) + 1
    if calls:
        log("调用账本：产物里已有 %d 次调用记录（%d 轮，CNY%.6f），本轮记为第 %d 轮" % (
            len(calls),
            len(_v2_runs(calls)),
            sum(float(c.get("cost_cny") or 0) for c in calls),
            run_no,
        ))

    prev_prompts = prev_meta.get("prompts") if isinstance(prev_meta.get("prompts"), dict) else {}
    prev_struct = prev.get("structure") if isinstance(prev.get("structure"), dict) else {}
    reuse = bool(
        (not force)
        and str(prev_meta.get("pipeline_version") or "") == PIPELINE_VERSION
        and isinstance(prev_struct.get("raw_root"), dict)
        and int(prev_struct.get("n_pages") or 0) == len(ch["pages_all"])
        and str((prev_prompts.get("structure") or {}).get("sha256") or "") == st_prompt["sha256"]
        and str((prev_prompts.get("knowledge") or {}).get("sha256") or "") == kp_prompt["sha256"]
    )

    if reuse:
        gen = {
            "root": prev_struct["raw_root"],
            "raw_drafts": [],
            "titles": {int(k): v for k, v in (prev_struct.get("page_titles") or {}).items()},
            "segments": prev_struct.get("segments") or [],
            "merged_by": prev_struct.get("merged_by") or "",
            "merge_note": "（续跑：复用产物里的结构）",
            "params": prev_struct.get("params") or {},
        }
        log("续跑：复用产物里的结构（%d 节点 / %d 叶节点），不重新生成、不重复花钱" % (
            prev_struct.get("n_nodes") or 0,
            prev_struct.get("n_leaves") or 0,
        ))
    else:
        if prev and not reuse:
            why = ("人显式要求重跑（--force）" if force else
                   "旧产物不是本版管线（%s）" % (prev_meta.get("pipeline_version") or "未知")
                   if str(prev_meta.get("pipeline_version") or "") != PIPELINE_VERSION else
                   "提示词换了（sha256 不一致），结构必须重生成")
            log(f"提示：{why} → 结构与知识点全部重新生成；如需留档请先备份 {out.name}")
        _stage(1, note="模型生成思维导图结构（%d 段）" % len(est.get("segments") or []),
               on=progress_on)
        _emit(on_event, {
            "ev": "stage",
            "stage": KP_STAGES[1],
            "stage_index": 1,
            "note": "生成结构",
        })
        gen = gen_structure_v2(doc_id, ch, st_prompt, deck, calls=calls, log=log,
                               run_no=run_no)

    norm = normalize_structure_v2(gen["root"], ch["pages_all"], deck, log=log)
    tasks = plan_kp_tasks_v2(norm["nodes"], norm["page_owner"], ch=ch)
    log("结构：%d 个节点 / %d 个叶节点 / 最深 %d 级 → %d 条知识点"
        "（v4：一个叶节点一条，页数不限；仅超 %d 字材料才按连续页切）"
        % (norm["n_nodes"], norm["n_leaves"], norm["max_level"], len(tasks),
           KP_MATERIAL_CHARS_MAX))
    done_map = {}
    if reuse:
        for r in (prev.get("knowledge_points") or []):
            if isinstance(r, dict) and str(r.get("summary") or "").strip():
                done_map[str(r.get("id") or "")] = r
    recs = [done_map[t["kp_id"]] for t in tasks if t["kp_id"] in done_map]
    if recs:
        log("续跑：产物里已有 %d 条知识点，跳过这些（剩余 %d 条）" % (
            len(recs), len(tasks) - len(recs)))

    # 预约表先把**已收录**的页登记上（续跑时这些页不能再被别的条占）。
    claimed = {}
    for _r in recs:
        for _p in (_r.get("pages") or []):
            try:
                claimed[int(_p)] = str(_r.get("id") or "")
            except (TypeError, ValueError):
                continue
    extra_fail = []
    claimed_lock = threading.Lock()

    # ---- 子函数：声明顺序与真值一致（变量重名，顺序即语义）
    def _claim_pages(kp_id, pages) -> list:
        """★**调用前预约**（卡 C-0063 最关键的改动）→ 见模块级 `claim_pages()`。

        为什么必须提前到调用之前：C-0062 报告 §7.1 的对抗实验实测——不预约时
        （即使**加锁**）4 条任务全都会收录同一页，`overlap_pages=[10,11]`；
        预约之后只有 1 条收录、3 条空，`overlap_pages=[]`。根因是那对
        "读-改-写"**跨了整个模型调用**（10~88 秒），锁保护不了这么长的窗口。
        """
        return claim_pages(claimed, claimed_lock, kp_id, pages)

    def _release_pages(kp_id, pages) -> None:
        """回滚预约（失败 / 未最终收录的页）→ 见模块级 `release_pages()`。"""
        release_pages(claimed, claimed_lock, kp_id, pages)

    def spent_now() -> float:
        """**本轮**累计花费（`max_cost` 是每次运行的硬上限，不是历史总额的上限）。"""
        return round(sum(
            float(c.get("cost_cny") or 0)
            for c in calls
            if int(c.get("run") or 0) == run_no
        ), 6)

    spent = spent_now()
    made = 0
    status, err = "done", ""
    if progress_on:
        with _STATE_LOCK:
            _STATE.update({"kp_done": len(recs), "kp_total": len(tasks)})

    def ordered_recs() -> list:
        return sorted(recs, key=lambda r: ((r.get("pages") or [0])[0], str(r.get("id") or "")))

    def persist() -> None:
        """落盘。**只由主线程调用**（并发路径里 worker 只调模型、不落盘）。

        卡 C-0063 在这里加一道**兜底断言**：落盘前用 `_v2_coverage()` 算一次
        `overlap_pages`，**不为空就拒绝落盘并抛错**（宁可失败也不出坏产物）。
        这是"调用前预约"之外的第二道守护（C-0062 报告 §7.1 的三条一起上：
        预约 + 落盘前断言 + 产物里保留 `meta.coverage.overlap_pages` 供事后查）。
        """
        cov_now = _v2_coverage(est, recs, norm, tasks)
        if cov_now.get('overlap_pages'):
            raise RuntimeError(
                f"★页归属出现重叠：overlap_pages={cov_now['overlap_pages']!s}"
                "（同一页进了多条知识点）。按卡 C-0063 的红线**拒绝落盘**——这是并发页仲裁的 bug，宁可这一轮失败，也不写出坏产物"
            )

        with _CALLS_LOCK:
            calls_snap = list(calls)

        doc = {
            "meta": _v2_meta(
                est, kp_prompt, st_prompt, norm, tasks, recs, calls_snap,
                status=status, started=started, budget=max_cost,
                struct_reused=reuse, failed=extra_fail, err=err,
                run_no=run_no,
                budget_unlimited=unlimited,
            ),
            "structure": _v2_structure_block(gen, norm, tasks, recs, ch),
            "knowledge_points": ordered_recs(),
        }

        with _SAVE_LOCK:
            _save(doc, out)

    def _accept_rec(task: dict, rec: dict, mine: list) -> None:
        """收下一条**成功**的记录：入列 → 收敛预约 → 进度 → 落盘 → 日志。

        并发与串行两条路径共用这个函数，且**只由主线程调用**，所以 `recs` /
        `done_map` / 进度 / 落盘在并发下也没有竞态（worker 线程不碰它们）。
        """
        nonlocal made, spent
        kp_id = str(task["kp_id"])
        recs.append(rec)
        done_map[kp_id] = rec
        keep = set()
        for _p in (rec.get("pages") or []):
            try:
                _n = int(_p)
            except (TypeError, ValueError):
                continue
            keep.add(_n)
            with claimed_lock:
                claimed[_n] = str(rec.get("id") or kp_id)
        _release_pages(kp_id, [p for p in mine if p not in keep])
        made += 1
        spent = spent_now()
        if progress_on:
            with _STATE_LOCK:
                _STATE["kp_done"] = len(recs)
                _STATE["cost_cny"] = spent
            progress.tick(
                made,
                total=len(tasks),
                note=f"{kp_id} → {rec.get('name') or ''}"
            )
        _emit(on_event, {
            "ev": "tick",
            "kp_done": len(recs),
            "kp_total": len(tasks),
            "cost_cny": spent,
            "note": rec.get("name") or "",
        })
        persist()
        log(
            "[%2d/%2d] %-12s p.%-8s → %-24s %d 字 / %d 要点 · CNY%.6f · 累计 CNY%.6f" % (
                made,
                len(tasks),
                kp_id,
                _pages_label(task["pages"]),
                (rec.get("name") or "")[:24],
                len(rec.get("summary") or ""),
                len(rec.get("points") or []),
                float((rec.get("usage") or {}).get("cost_cny") or 0),
                spent,
            )
        )

    persist()

    try:
        inner_w = max(1, min(inner_workers(), max(1, len(tasks))))
        pending = [t for t in tasks if t["kp_id"] not in done_map]
        use_parallel = inner_w > 1 and len(pending) > 1
        if use_parallel:
            log("知识点并发：%d 条待生成走 %d 路（进程级模型闸门 %d）；调用前预约已开启，落盘前断言 overlap_pages 必须为空" % (
                len(pending), inner_w, model_gate_limit(),
            ))
            # 卡 C-0123：并发路径原本**一个 stage 事件都不发**（只有串行补跑那条路
            # 才发 `KP_STAGES[2]`），所以看板上阶段名永远停在"生成结构"，
            # 就算底下已经在写第 14/20 条也看不出来 —— 这是"看着像卡死"的一半原因。
            _emit(on_event, {
                "ev": "stage",
                "stage": KP_STAGES[2],
                "stage_index": 2,
                "note": "写知识点（%d 条待生成）" % len(pending),
            })
            # 卡 C-0124：`_emit` 只喂**批量**那条回调，**单份**进度条读的是
            # `_STATE`（`progress_snapshot()`），而上面那个 `_emit` 不写它。实测
            # 后果：并发跑知识点时 `stage` 一直显示"生成结构"、`stage_index` 停在
            # 1，用户在界面上看到的是"阶段没变 + 条数在涨"这种自相矛盾的状态。
            # 这里补一次 `_stage(2)` 把单份进度条也切到"写知识点"；`on=progress_on`
            # 保证批量模式（`progress_on=False`）照旧不碰模块级 `_STATE`。
            _stage(2, note="写知识点（%d 条待生成）" % len(pending), on=progress_on)

        def _kp_worker(task: dict, mine: list) -> dict:
            """worker 线程：**只做"调模型 + 解析"**，异常也在这里兜住。

            不碰 `recs` / `claimed` 的收敛 / 进度 / 落盘（那些都在主线程），
            所以并发下这些共享状态没有竞态；唯一共享的 `claimed`（预约表）
            走 `claimed_lock`。异常不往外抛（外抛会让 `wait()` 那一圈丢掉
            别的在飞结果），改为把错误带回主线程**逐条记账**。
            """
            try:
                rec = gen_kp_v2(task, ch, kp_prompt, deck, calls=calls, log=log,
                                run_no=run_no, claimed=claimed, claimed_lock=claimed_lock)
                return {"ok": True, "rec": rec, "error": ""}
            except Exception as e:
                return {"ok": False, "rec": None, "error": f"{type(e).__name__}: {e}"}

        stop_note = ""
        futures = {}
        qi = 0
        stalled = ""
        task = None
        mine: list = []
        # 卡 C-0123：**不再用 `with ThreadPoolExecutor(...)`** —— 它的 `__exit__`
        # 会 `shutdown(wait=True)`，只要还剩一个挂起的 worker，主线程就跟着一起
        # 钉死（定罪栈：`build_v2` 卡在 `_cf.wait`，正是因为没人能走出这个 with）。
        # 改成显式创建 + `shutdown(wait=False)`：主线程必须能带着已完成的条数收尾落盘。
        ex = _cf.ThreadPoolExecutor(max_workers=inner_w, thread_name_prefix="kp-inner")
        try:
            while True:
                # 先把在飞数量填到闸门上限（每次填一条就交给下面 wait 消化）
                while (not stop_note) and len(futures) < inner_w and qi < len(pending):
                    task = pending[qi]
                    if limit and made >= int(limit):
                        stop_note = "已达 --limit %d 条上限，停止（剩余 %d 条下次续跑）" % (int(limit), len(pending) - qi)
                        break
                    if max_cost and not unlimited and spent >= max_cost:
                        stop_note = "★累计 CNY%.6f 已达硬上限 CNY%.2f：停止（剩余 %d 条下次续跑）" % (spent, max_cost, len(pending) - qi)
                        break
                    qi += 1
                    mine = _claim_pages(task["kp_id"], task["pages"])
                    fut = ex.submit(_kp_worker, {**task, "doc_id": int(doc_id)}, mine)
                    futures[fut] = (task, mine)
                if not futures:
                    break
                # 卡 C-0123：等完成**带判停线**。`done` 为空 = 在飞的调用全部超过
                # `stall_timeout()` 秒没有任何返回（正常单次调用实测最慢 87.9 秒）⇒
                # 判"停滞"：不再陪它等，带着已完成的条数收尾（落盘 + 明确原因），
                # 而不是永远停在"运行中"（用户看到的就是这个）。
                wait_to = stall_timeout()
                done, _still = _cf.wait(list(futures), timeout=(wait_to or None),
                                        return_when=_cf.FIRST_COMPLETED)
                if not done:
                    # 卡 C-0124：判停 = 主线程不再等这些 worker ⇒ 它们手里的模型槽
                    # 对本次运行已经没有意义，按账**当场抢回来**。不抢的话，实测
                    # `build_v2` 返回后 `inflight` 仍为 1（worker 阻塞在自己的
                    # `th.join(1800)` 里，要等硬墙到点才释放），下一次整理起手就
                    # 少一格并发。⚠ 必须在 `stalled` 置位**之前**调用，确保后面
                    # 无论走哪条收尾路径，槽位都已经回收。
                    freed = reclaim_stalled_slots()
                    stalled = ("★停滞：%d 个在飞模型调用超过 %.0f 秒没有任何返回"
                               "（模型接口挂起；已完成的 %d 条已落盘，剩余下次续跑；"
                               "已回收 %d 个滞留模型槽位）"
                               % (len(futures), wait_to, len(recs), freed))
                    log(stalled)
                    _emit(on_event, {"ev": "log", "msg": stalled})
                    break
                for fut in done:
                    task, mine = futures.pop(fut)
                    one = fut.result()
                    if not one.get("ok"):
                        log(f"★失败 {task['kp_id']}（p.{_pages_label(task['pages'])}）：{one.get('error')}")
                        _emit(on_event, {"ev": "log", "msg": f"★失败 {task['kp_id']}：{one.get('error')}"})
                        extra_fail.append(task["kp_id"])
                        _release_pages(task["kp_id"], mine)
                        persist()
                        continue
                    _accept_rec(task, one["rec"], mine)
        except Exception as e:
            # 兜底：`task` 可能还没被赋过值（极端早失败），这里不再让它变 NameError。
            kp_id = str((task or {}).get("kp_id") or "?")
            log(f"★失败 {kp_id}（p.{_pages_label((task or {}).get('pages') or [])}）：{type(e).__name__}: {e}")
            _emit(on_event, {"ev": "log", "msg": f"★失败 {kp_id}：{e}"})
            if task:
                extra_fail.append(task["kp_id"])
                _release_pages(task["kp_id"], mine)
            persist()
        finally:
            # 不等待：挂起的 worker 由它自己的硬墙（`_call_with_hard_timeout`）收，
            # 主线程必须能立刻走出去落盘。
            try:
                ex.shutdown(wait=False, cancel_futures=True)
            except TypeError:            # Python < 3.9 没有 cancel_futures
                ex.shutdown(wait=False)
        if stalled:
            # 停滞（挂起）**不是**"到达花费上限"：这一轮没能跑完，记 failed，
            # 但产物已经落盘、已完成的条数一条不丢（下次是续跑，不重复花钱）。
            # 也**不能**再走下面的串行补跑——那会立刻再次撞上同一个挂起。
            status, err = "failed", stalled
        elif stop_note:
            log(stop_note)
            status = "stopped"
        else:
            # 卡 C-0123：**并发那一轮已经判失败的条目，这里不再串行重跑一遍**。
            # 原来会重跑：挂起场景下每条都要再白等一个硬墙（用户那次 4 路并发 +
            # 55 条 = 十几轮），而且第一个再失败就抛出去，把整轮的 error 换成
            # 底层的 `RuntimeError: 调用失败：...`，反而看不出"到底做完了几条"。
            # 现在统一交给下面的 `if extra_fail:` 归纳成一句可读的结论。
            _failed_ids = set(extra_fail)
            for i, task in enumerate(tasks, 1):
                if task["kp_id"] in done_map:
                    continue
                if task["kp_id"] in _failed_ids:
                    log("跳过 %s：并发那一轮已判失败（见上面的失败行），下次续跑再试"
                        % task["kp_id"])
                    continue
                if limit and made >= int(limit):
                    log("已达 --limit %d 条上限，停止（剩余 %d 条下次续跑）" % (int(limit), len(tasks) - i + 1))
                    status = "stopped"
                    break
                if max_cost and not unlimited and spent >= max_cost:
                    log("★累计 CNY%.6f 已达硬上限 CNY%.2f：停止（剩余 %d 条下次续跑）" % (spent, max_cost, len(tasks) - i + 1))
                    status = "stopped"
                    break
                _stage(2, note="第 %d/%d 条 · %s（p.%s）" % (
                    i, len(tasks), task["kp_id"], _pages_label(task["pages"])), on=progress_on)
                _emit(on_event, {
                    "ev": "stage",
                    "stage": KP_STAGES[2],
                    "stage_index": 2,
                    "note": "第 %d/%d 条 · %s" % (i, len(tasks), task["kp_id"]),
                })
                mine = _claim_pages(task["kp_id"], task["pages"])
                rec = gen_kp_v2({**task, "doc_id": int(doc_id)}, ch, kp_prompt, deck,
                                calls=calls, log=log, run_no=run_no,
                                claimed=claimed, claimed_lock=claimed_lock)
                _accept_rec(task, rec, mine)
        if extra_fail:
            # 卡 C-0123：**部分失败不许报"完成"**。用户 2026-10-15 那份产物停在
            # kp_done=14/20、5/55，`meta.status` 却写着 "done" —— 界面因此完全
            # 看不出"其实没跑完"，这正是"看着像卡住"的另一半原因。
            # 现在只要有条目失败或挂起，就记 failed，并把"未完成/已落盘"写进
            # error；产物照旧落盘（每条一存），重跑就是续跑，不重复花钱。
            status = "failed"
            err = ("%d/%d 条知识点未完成（其中 %d 条失败或挂起）：已完成的 %d 条"
                   "已落盘，重跑即续跑（不重复花钱）"
                   % (len(tasks) - len(recs), len(tasks),
                      len(extra_fail), len(recs)))
        persist()
        _stage(3, note="写入产物", on=progress_on)
        _emit(on_event, {
            "ev": "stage",
            "stage": KP_STAGES[3],
            "stage_index": 3,
            "note": "写入产物",
        })
    except Exception as e:
        status, err = "failed", f"{type(e).__name__}: {e}"
        log("★整理中止：%s" % err)
        _emit(on_event, {"ev": "log", "msg": "★整理中止：%s" % err})

    try:
        persist()
    except Exception as e:
        log("★落盘失败：%s" % e)
        status, err = "failed", str(e)

    if progress_on:
        with _STATE_LOCK:
            _STATE["running"] = False
        progress.end(status if status != "stopped" else "done", error=err)
    _emit(on_event, {
        "ev": "end",
        "status": status,
        "error": err,
        "cost_cny": spent,
    })

    # 收尾日志读的是**刚落盘的产物**（而不是内存里的中间态），所以口径与看板一致。
    meta = _load_prev(out).get("meta") or {}
    cov = meta.get("coverage") or {}
    this = meta.get("usage_total_this_run") or {}
    log("完成：%d/%d 条知识点（覆盖 %d/%d 页）· 结构 %d 节点 / %d 叶节点（最深 %d 级）· 用时 %.0fs" % (
        meta.get("kp_actual", 0),
        meta.get("kp_total", 0),
        cov.get("n_covered", 0),
        cov.get("pages_total", 0),
        norm["n_nodes"],
        norm["n_leaves"],
        norm["max_level"],
        meta.get("elapsed_s", 0),
    ))
    log("本轮开销：CNY%.6f（调用 %d 次，失败 %d 次；入 %d / 出 %d token，其中思考 %d token = 输出的 %.1f%%）" % (
        this.get("cost_cny", 0),
        this.get("calls", 0),
        this.get("calls_failed", 0),
        this.get("tokens_in", 0),
        this.get("tokens_out", 0),
        this.get("reasoning_tokens", 0),
        100.0 * (meta.get("reasoning_token_share_this_run") or 0.0),
    ))
    log("这份产物累计开销：CNY%.6f（调用 %d 次 / %d 轮）· 产物 %s" % (
        meta.get("cost_cny_all_calls", 0),
        (meta.get("usage_total_all_calls") or {}).get("calls", 0),
        len(meta.get("runs") or []),
        out,
    ))

    return {
        "ok": True,
        "status": status,
        "error": err,
        "out_path": str(out),
        "meta": meta,
        "estimate": est,
    }


def estimate(doc_id: int, *, outline_path=None, prompt_path=None, db_path=None,
             struct_prompt_path=None, pipeline: str | None = None) -> dict:
    """dry-run 入口。**默认走 v2（只读文本）**；`pipeline="v1"` 走读 outline 的旧路。

    `outline_path` 只为兼容看板既有的调用签名而保留：v2 **不用它**，
    返回里 `outline_ignored=True`（v1 才读它）。
    """
    if str(pipeline or "").lower() == "v1":
        return estimate_v1(doc_id, outline_path=outline_path,
                           prompt_path=prompt_path, db_path=db_path)
    return estimate_v2(doc_id, prompt_path=prompt_path,
                       struct_prompt_path=struct_prompt_path, db_path=db_path)


# ---------------------------------------------------------------- v1 回退路径
# ⚠ [重建] 这一段原本是 **C-0045 那代管线**的实现（`build_v1` + `estimate_v1` +
# `prev_info_v1` + `load_kp_prompts` + `load_outline` …，恢复版里整段丢失）。
# 现状：只保留 `build_v1` 的签名与一具"明确拒绝"的函数体 ——
#   · 默认路径（v2/v3）**不受影响**：`build()` 只在 `pipeline="v1"` 时才调它，
#     而全仓库默认都是 `pipeline=None` ⇒ 走 `build_v2()`；
#   · 真需要 v1 时（`--pipeline v1` 对照/回退）会得到一句清楚的话，而不是
#     `NameError`；
#   · 待用 `ai_decompile.py --pyc "...\core\kp_build.pyc"` 重建后替换本函数体即可。


def build(doc_id: int, *, outline_path=None, prompt_path=None, out_path=None,
          db_path=None, dry_run: bool = False, limit: int = 0, max_cost: float = 0.0,
          force: bool = False, progress_on: bool = True, on_event=None, log=print,
          struct_prompt_path=None, pipeline: str | None = None,
          budget_unlimited: bool | None = None) -> dict:
    """整理一份课件的入口。**默认走 v3（只读库里的文本，自己生成结构，开推理模式）**。

    `pipeline="v1"` 走 C-0045 那代（读 `logs/outline/<doc_id>.json`），仅供对照与回退。
    `budget_unlimited`：None = 用模块级 `BUDGET_UNLIMITED`（默认 True，不限花费）；
    显式 False 才让 `max_cost` 恢复成硬上限。
    **v1 回退路径也吃同一个策略**（卡 C-0052）：默认不限时把 `max_cost` 归零传给
    `build_v1()`，于是 v1 的 `meta.budget_cny` 记 0（= 不限）——否则"整理知识点预算无上限"
    会在 `--pipeline v1` 这条路上落空。
    """
    if str(pipeline or "").lower() == "v1":
        unlimited = BUDGET_UNLIMITED if budget_unlimited is None else bool(budget_unlimited)
        mc = float(max_cost or 0.0)
        if unlimited and mc:
            log("预算策略：不限（人拍板 2026-09-27；v1 回退路径同样生效）："
                "--max-cost %.2f 只记录、不作为停止条件" % mc)
            mc = 0.0
        return build_v1(doc_id, outline_path=outline_path, prompt_path=prompt_path,
                        out_path=out_path, db_path=db_path, dry_run=dry_run,
                        limit=limit, max_cost=mc, force=force,
                        progress_on=progress_on, on_event=on_event, log=log)
    return build_v2(doc_id, prompt_path=prompt_path,
                    struct_prompt_path=struct_prompt_path, out_path=out_path,
                    db_path=db_path, dry_run=dry_run, limit=limit, max_cost=max_cost,
                    force=force, progress_on=progress_on, on_event=on_event, log=log,
                    budget_unlimited=budget_unlimited)


def prev_info(doc_id: int, out_path: str | os.PathLike | None = None,
              pipeline: str | None = None) -> dict:
    """已有产物的状态。默认按 v2 口径读（`pipeline="v1"` 走旧口径）。"""
    if str(pipeline or "").lower() == "v1":
        return prev_info_v1(doc_id, out_path=out_path)
    return prev_info_v2(doc_id, out_path=out_path)


def prompt_file_v2(path: str | os.PathLike | None = None) -> Path:
    """v2 知识点提示词真源（结构提示词是另一个文件，见 `default_struct_prompt_file()`）。"""
    return _prompt_path_v2(KP_PROMPT_NAME_V2, path)


TERMS_VERSION = PIPELINE_VERSION_V4
# 提炼的分段口径：与结构生成**同一套双阈值**（页数 / 字符数），
# 让"段"这个概念在两条管线里是同一件事，也让调用次数与页数无关地有上界。


TERMS_SEG_PAGES = STRUCT_SEG_PAGES


TERMS_SEG_CHARS = STRUCT_SEG_CHARS
# 喂给提炼模型的**每页字符预算**：比结构生成（420）宽得多——挑名词要看得到定义句，
# 而结构只需要看标题与层级；截断处标 `……`，绝不静默丢页。


TERMS_RETRY = STRUCT_RETRY
