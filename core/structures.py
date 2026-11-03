# Copyright (c) 2026 潜声海豚 <2227663973@qq.com>. All rights reserved.
# 本源码为著作权人私有作品，适用项目根目录 LICENSE（专有许可）。
# 允许个人安装与使用；禁止反向工程、修改、再分发、商业使用及声称原创。
"""化学结构式 / 矢量页补全：PPTX XML 零 AI 扫描 + 选择性结构页识图（默认 dry-run）。

## 这个模块解决什么问题（背景）

课件（PPT/PPTX）里的化学结构式大多是**PPT 内置矢量图形**（线条 + 形状 + 文本框），
入库时 python-pptx 只抽到一部分文字：纯线条画出来的母核/取代基/键型**在文字层里根本
不存在**，通用识图又按"讲清这页在表达什么"的目标刻意不转写细节，于是结构信息丢失。
真实数据（2026-10-23 盘点）：9 份药理学课件、381 张整页图（+81 份旧识图 md，132 MB）
**已在入库时缓存**，所以"只对结构页补一次专用识图"在工程上可行，不需要重新渲染、
也不需要全量逐页花钱（全量约 ¥1.4–4.6；选择性约 ¥0.2–0.9）。

方案讨论见 `docs/化学结构式识别-2026-10-23.md`（路线 A 文字层榨干 + B 选择性识图 + E 原图人工核对）。

## 本模块做四件事（全部**默认零 AI、零写盘**）

1. `scan_pptx(doc_id)`：用 `zipfile` 直接解析 `ppt/slides/slideN.xml`，统计每页的
   形状/连接线/组合/图片/矢量几何/文本量，按可调阈值挑出**疑似结构页**，
   写 `logs/knowledge/<doc>-结构页.json`（零模型调用）。
2. `scan_text_gaps(doc_id)`：对比 **python-pptx 顶层 shape 文本** 与 **XML 里全部
   `<a:t>` 文本**，逐页报告"入库时漏抽了多少字/哪些片段"（仅报告，不改入库）。
3. `find_page_images(doc_id)`：复用 `logs/figure_desc/<deck>/slide_*.png` 已有的整页图，
   返回缺图页；默认**不渲染**（可选 `render=True`，但那会起 PowerPoint，默认不跑）。
4. `build_structures(doc_id)`：**默认 dry-run**——0 调用、0 写盘，只返回候选页、
   预计调用数与费用上界；只有 `dry_run=False` 才用专用提示词
   `logs/knowledge/提示词-结构式识图.md` 调视觉模型，逐页记账、缓存复用、原子写、
   失败页记录 `error` 不崩。

## 为什么默认 dry-run

"真实构建"每页都是一次付费视觉调用。本模块把**"看一眼要花多少钱"**做成默认行为，
`--run` / `dry_run=False` 必须显式给出；CLI 在真实调用前还会打印预估值并要求确认
（非交互环境必须 `--yes`）。这条规矩来自项目红线：不允许"跑着跑着自动花钱"。

## 产物（都在 `logs/knowledge/`，`out_dir` 可覆盖）

- `<doc_id>-结构页.json`：`{meta:{version,doc_id,source_pptx,generated_at,n_pages,
  n_candidates,rule,thresholds}, candidates:[{page,reasons,shape_count,...,text_chars,
  keywords,score}]}`
- `<doc_id>-结构式.json`：`{meta:{version,doc_id,model,calls,cost_cny,tokens,elapsed_s,
  generated_at,prompt_sha256,...}, pages:{"<page>":{has_structure,structure,smiles,
  reaction,uncertainty,notes,image_hash,prompt_sha256,cost_cny,tokens,...}}}`

结构式产物是**旁证材料**，不直接写正文块；`uncertainty` 与原始页图路径供人工核对
（路线 E：模型看错时用户 1 秒能对照原图）。

## 与其它模块的边界

- 不改 `core/figures.py`（通用识图照旧）；这里直接复用其 `_http_post`（带重试）、
  `price_of`（价格口径）、`_safe_dirname` / `render_pages`（图目录与可选渲染）。
- 不写数据库：本模块只产出 JSON 旁证，入库/UI 接线由后续卡片决定。
- 测试 `tests/test_29_structure_scan.py` 全程 monkeypatch/注入假视觉函数，**不真实调 AI**。
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import sqlite3
import sys
import time
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

from . import config, figures
from .pipeline_runtime import context as runtime_context

# ---------------------------------------------------------------- 常量

#: 产物/规则版本。改扫描规则或产物 schema 时 +1（产物 meta.version 会记录）。
VERSION = "1"
SCAN_RULE_VERSION = "scan-v1"

#: 提示词真源文件名（运行期真源在 logs/knowledge/；tracked 副本在 docs/prompts/）。
PROMPT_NAME = "提示词-结构式识图.md"
PROMPT_TAG_SYS = "PROMPT-STRUCT-SYS"
PROMPT_TAG_USER = "PROMPT-STRUCT-USER"

#: 产物文件名模板。
SCAN_FILE_FMT = "{doc_id}-结构页.json"
STRUCT_FILE_FMT = "{doc_id}-结构式.json"

#: 每页识图调用的默认参数。
#: - max_tokens 只给 1024：输出是**严格 JSON 短结构**，不是长描述；历史通用识图
#:   （长描述 + 推理）平均输出 3207 tokens、峰值 12845，那套额度在这里是浪费。
#: - thinking 默认关：结构描述要的是"看图抄结构"，推理会显著增加输出 token；
#:   需要时可用 `thinking=True` 打开（产物 meta.thinking 会记录）。
STRUCT_MAX_TOKENS = 1024
STRUCT_THINKING = False

#: 费用预估假设（**只用于 dry-run 上界**，不参与真实记账）。
#: 输入 token 上界取自真实 `figure_desc` 表 81 页历史调用的峰值 2673（含整页图 +
#: 上下文提示词），这里取整到 3000 作为上界；输出按 max_tokens 计（最坏情况写满）。
EST_INPUT_TOKENS_MAX = 3000
#: DeepSeek 价格（USD/1M tokens，与 core/figures.py::_PRICE 同源）。
#: 真实记账走 `figures.price_of(usage)`（默认 off_peak，与入库识图一致）；
#: dry-run 同时给 peak / off_peak 两个上界，避免"按闲时价承诺、峰值价翻倍"的误导。
PRICE_ASSUMPTION_NOTE = (
    "输入 ≤%d tokens（取历史 figure_desc 81 页 prompt_tokens 峰值 2673 的上界）、"
    "输出 ≤max_tokens（默认 %d）；价格取 core/figures.py::_PRICE（USD/1M tokens，"
    "汇率 7.1）。真实记账按接口返回 usage 计。" % (EST_INPUT_TOKENS_MAX, STRUCT_MAX_TOKENS)
)

#: 结构页关键词（化学结构/反应相关）。命中即进候选，reasons 里记录命中词。
#: 分两档：**强词**单独命中即可（化学专用，误报低）；**弱词**是任务书逐字给定的
#: 宽词（反应/合成/取代/机理），在药理学课件里也大量出现（如"不良反应"含"反应"），
#: 必须"多个弱词"或"弱词 + 图/矢量证据"才算候选，避免把普通文字页全捞进来。
STRUCT_KEYWORDS_STRONG = (
    "结构式", "化学结构", "构效关系", "母核", "取代基", "官能团", "手性",
    "立体化学", "化学式", "分子式", "骨架", "衍生物", "化学键", "合成路线",
    "反应式", "构型", "药物设计", "前药", "构效",
)
STRUCT_KEYWORDS_WEAK = ("反应", "合成", "取代", "机理", "作用机理")
STRUCT_KEYWORDS = STRUCT_KEYWORDS_STRONG + STRUCT_KEYWORDS_WEAK

#: 扫描阈值（可通过 `scan_pptx(thresholds=...)` 逐项覆盖；产物 meta 会记录实际值）。
#: 默认值是 2026-10-24 在真实 9 份课件（381 页）上校准的：候选 47 页 / 12.3%，
#: 与方案讨论"结构页约占 10–20%"一致（`_work/structure/report.md` 有逐份数据）。
DEFAULT_SCAN_THRESHOLDS = {
    # 规则 1：矢量元素多 + 文本少（纯线条画的结构式/反应式页）
    "vec_geom_min": 8,
    "text_chars_max": 120,
    # 规则 2：强关键词直接进候选（化学专用词，误报低）
    "keyword_min_hits": 1,
    # 规则 3：弱关键词组合（≥2 个不同弱词，或 1 个弱词 + 图/矢量证据）
    "weak_keyword_min_hits": 2,
    "weak_evidence_geom_min": 8,
    "weak_evidence_pics_min": 1,
    # 规则 4：组合形状 + 图片多（ChemDraw/OLE 截图型结构页常见）
    "pic_combo_shapes": 10,
    "pic_combo_pics": 2,
    "group_pic_groups": 2,
    # 规则 5：嵌入 OLE 对象（ChemDraw/公式对象）直接进候选
    "ole_min": 1,
}

#: XML 命名空间（只用于取 local-name；不同机器导出的前缀可能不同）。
_P_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
_A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"


class StructureError(Exception):
    """可读的、预期内的失败（无源文件 / 无图 / 参数不合法等）。"""


# ---------------------------------------------------------------- 小工具

def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _atomic_write_json(data: dict, out: Path) -> Path:
    """原子写 JSON：先写 `.tmp` 再 `os.replace`（中断不会留下半截文件）。

    与 `core/kp_build.py::_save()`、`core/teach.py::_atomic_write_json` 同一做法；
    结构式产物里每一页都可能是钱，不能让一次 Ctrl-C 把已有页毁掉。
    """
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, out)
    return out


def _read_json(path: Path) -> dict | None:
    """读 JSON；不存在/损坏返回 None（读方自行降级，测试也依赖这一点）。"""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _local(tag: str) -> str:
    """`{namespace}tag` -> `tag`。"""
    return tag.rsplit("}", 1)[-1]


def _norm_space(s: str) -> str:
    """去掉所有空白（用于"这段文字有没有被漏抽"的包含判断）。"""
    return re.sub(r"\s+", "", s or "")


def default_out_dir() -> Path:
    """默认产物目录：与知识点/教材补充产物同目录（`logs/knowledge`）。"""
    return Path(config.LOG_DIR) / "knowledge"


def scan_path(doc_id: int, out_dir: Path | str | None = None) -> Path:
    return Path(out_dir or default_out_dir()) / SCAN_FILE_FMT.format(doc_id=int(doc_id))


def struct_path(doc_id: int, out_dir: Path | str | None = None) -> Path:
    return Path(out_dir or default_out_dir()) / STRUCT_FILE_FMT.format(doc_id=int(doc_id))


# ---------------------------------------------------------------- 文档 / 源文件定位

def _db_row(doc_id: int, db_path: Path | str | None = None) -> dict:
    """只读取 documents 行（**不拿写锁**，连接用完即关）。"""
    path = Path(db_path or config.DB_PATH)
    if not path.exists():
        raise StructureError("数据库不存在：%s" % path)
    try:
        conn = sqlite3.connect("file:%s?mode=ro" % path.as_posix(), uri=True,
                               timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute("SELECT * FROM documents WHERE id=?",
                               (int(doc_id),)).fetchone()
        finally:
            conn.close()
    except sqlite3.Error as e:
        raise StructureError("读文档表失败：%s" % e) from None
    if row is None:
        raise StructureError("文档不存在：doc_id=%s（库：%s）" % (doc_id, path))
    return dict(row)


def _resolve_source(doc_id: int, db_path: Path | str | None = None) -> tuple[dict, Path]:
    """定位课件源文件（.pptx）。

    优先级：`documents.abs_path`（入库时记的绝对路径）> 项目根 + `documents.path`
    > `raw/` + 文件名。只有 `.ppt` 时尝试同名 `.pptx`（本机 9 份课件都有 .pptx 双份）；
    都找不到就抛可读错误，列出试过的路径。
    """
    row = _db_row(doc_id, db_path)
    rel = str(row.get("path") or "").strip()
    tried: list[Path] = []
    cands: list[Path] = []
    abs_path = str(row.get("abs_path") or "").strip()
    if abs_path:
        cands.append(Path(abs_path))
    if rel:
        cands.append(Path(config.ROOT) / rel.replace("/", os.sep))
        cands.append(Path(config.RAW_DIR) / Path(rel).name)
    for p in cands:
        tried.append(p)
        if p.exists() and p.suffix.lower() == ".pptx":
            return row, p
    # 只有 .ppt（或显式不存在）时：找同名 .pptx 兄弟
    for p in cands:
        for alt in (p.with_suffix(".pptx"), p.with_suffix(".PPTX")):
            tried.append(alt)
            if alt.exists():
                return row, alt
    raise StructureError(
        "找不到课件源文件（doc_id=%s，title=%r）。试过：%s。"
        "只有 .ppt 时需要先转 .pptx 或重新入库。"
        % (doc_id, row.get("title"), "；".join(str(x) for x in tried)))


# ---------------------------------------------------------------- PPTX XML 扫描

_SLIDE_RE = re.compile(r"^ppt/slides/slide(\d+)\.xml$")


def _slide_parts(src: Path) -> list[tuple[int, str]]:
    """列出 (页号, zip 内路径)，按页号数字序。"""
    try:
        zf = zipfile.ZipFile(src)
    except (OSError, zipfile.BadZipFile) as e:
        raise StructureError("不是合法的 pptx（zip 解析失败）：%s（%s）" % (src, e)) from None
    try:
        out: list[tuple[int, str]] = []
        for name in zf.namelist():
            m = _SLIDE_RE.match(name)
            if m:
                out.append((int(m.group(1)), name))
        out.sort(key=lambda x: x[0])
        return out
    finally:
        zf.close()


@dataclass
class SlideStats:
    """一页的 XML 统计（全部零 AI）。"""

    page: int
    shape_count: int = 0        # p:sp
    connector_count: int = 0    # p:cxnSp（连接线，结构式里画键的常见载体）
    group_count: int = 0        # p:grpSp
    pic_count: int = 0          # p:pic
    geom_count: int = 0         # a:prstGeom（预设形状）
    custgeom_count: int = 0     # a:custGeom（自绘形状）
    line_count: int = 0         # a:ln（描边/线条属性）
    ole_count: int = 0          # p:oleObj（ChemDraw/公式等嵌入对象）
    table_count: int = 0        # a:tbl
    graphic_count: int = 0      # p:graphicFrame
    text_runs: list[str] = field(default_factory=list)
    text: str = ""              # 全部 <a:t> 文本（空格拼接，用于关键词）
    text_chars: int = 0         # 全部文本字符数（去首尾空白后累计）
    title: str = ""             # 页标题猜测（第一个非空文本 run）

    @property
    def vec_geom(self) -> int:
        """矢量元素总数（几何 + 自定义几何 + 连接线）——规则 1 的判据。

        ⚠ **不含 `a:ln`**：`a:ln` 是描边/线条属性，每个带边框的文本框都会出现，
        在真实课件上实测它约等于 shape 数（文本框页也有 30~40 个 ln），
        算进来会让"文本很多的普通页"也满足"矢量元素多"（校准前候选 143/381=37.5%，
        剔除 ln 后 47/381=12.3%）。`line_count` 仍保留在产物里供人工核对。
        """
        return self.geom_count + self.custgeom_count + self.connector_count

    def to_dict(self) -> dict:
        return {
            "page": self.page,
            "shape_count": self.shape_count,
            "connector_count": self.connector_count,
            "group_count": self.group_count,
            "pic_count": self.pic_count,
            "geom_count": self.geom_count,
            "custgeom_count": self.custgeom_count,
            "line_count": self.line_count,
            "ole_count": self.ole_count,
            "table_count": self.table_count,
            "graphic_count": self.graphic_count,
            "vec_geom": self.vec_geom,
            "text_chars": self.text_chars,
            "text_preview": self.text[:120],
            "title": self.title,
        }


def _parse_slide_xml(page: int, xml_bytes: bytes) -> SlideStats:
    """解析一页 slideN.xml，统计形状/矢量/文本。"""
    st = SlideStats(page=page)
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as e:
        # 单页 XML 坏不拖垮整份：记录在 text 里当诊断线索（scan 仍继续）
        st.text = "[XML 解析失败] %s" % e
        return st
    runs: list[str] = []
    for el in root.iter():
        tag = _local(el.tag)
        if tag == "sp":
            st.shape_count += 1
        elif tag == "cxnSp":
            st.connector_count += 1
        elif tag == "grpSp":
            st.group_count += 1
        elif tag == "pic":
            st.pic_count += 1
        elif tag == "prstGeom":
            st.geom_count += 1
        elif tag == "custGeom":
            st.custgeom_count += 1
        elif tag == "ln":
            st.line_count += 1
        elif tag == "oleObj":
            st.ole_count += 1
        elif tag == "tbl":
            st.table_count += 1
        elif tag == "graphicFrame":
            st.graphic_count += 1
        elif tag == "t":
            txt = el.text or ""
            if txt.strip():
                runs.append(txt.strip())
    st.text_runs = runs
    st.text = " ".join(runs)
    st.text_chars = sum(len(r) for r in runs)
    st.title = runs[0][:80] if runs else ""
    return st


def _scan_all_slides(src: Path) -> list[SlideStats]:
    """解析整份 pptx 的全部 slide XML（零 AI、只读）。"""
    parts = _slide_parts(src)
    if not parts:
        raise StructureError("pptx 里没有任何 ppt/slides/slideN.xml：%s" % src)
    out: list[SlideStats] = []
    try:
        with zipfile.ZipFile(src) as zf:
            for page, name in parts:
                try:
                    data = zf.read(name)
                except KeyError:
                    continue
                out.append(_parse_slide_xml(page, data))
    except (OSError, zipfile.BadZipFile) as e:
        raise StructureError("读取 pptx 失败：%s（%s）" % (src, e)) from None
    return out


def _keyword_hits(stats: SlideStats) -> dict:
    """关键词命中：分强/弱两档；短词被长词包含时只留长的（"构效" ⊂ "构效关系"）。

    在**去空白**文本上匹配（`_norm_space`）：PPT 常把一句话拆进多个 `<a:t>` run，
    拼接后才是完整词（如"化学结构"被拆成"化学"+"结构"）。
    """
    hay = _norm_space(stats.text)

    def _scan(words):
        hit = [kw for kw in words if kw in hay]
        return [kw for kw in hit if not any(kw != o and kw in o for o in words if o in hay)]

    return {"strong": _scan(STRUCT_KEYWORDS_STRONG),
            "weak": _scan(STRUCT_KEYWORDS_WEAK)}


def _judge_candidate(stats: SlideStats, th: dict) -> dict | None:
    """按阈值判定一页是不是"疑似结构页"；是则返回候选条目，否则 None。

    规则（任一命中即候选，reasons 记录人可读理由）：
      A 矢量元素多 + 文本少（纯线条画的结构式/反应式）
      B 强关键词命中（结构式/母核/取代基/官能团/手性…，化学专用）
      C 弱关键词组合（≥2 个弱词，或 1 个弱词 + 图/矢量证据）
      D 组合形状 + 图片多；E 组合形状多且含图片；F 嵌入 OLE 对象
    """
    kws = _keyword_hits(stats)
    strong, weak = kws["strong"], kws["weak"]
    reasons: list[str] = []
    score = 0.0

    if stats.vec_geom >= th["vec_geom_min"] and stats.text_chars <= th["text_chars_max"]:
        reasons.append("A 矢量元素多且文本少（vec_geom=%d≥%d，text_chars=%d≤%d）"
                       % (stats.vec_geom, th["vec_geom_min"],
                          stats.text_chars, th["text_chars_max"]))
        score += 3.0
    if len(strong) >= th["keyword_min_hits"]:
        reasons.append("B 强结构关键词：%s" % "、".join(strong))
        score += 2.0 + 0.5 * len(strong)
    if len(weak) >= th["weak_keyword_min_hits"]:
        reasons.append("C 弱结构关键词组合：%s" % "、".join(weak))
        score += 1.5 + 0.5 * len(weak)
    elif weak and (stats.pic_count >= th["weak_evidence_pics_min"]
                   or (stats.geom_count + stats.custgeom_count)
                   >= th["weak_evidence_geom_min"]):
        reasons.append("C 弱结构关键词+图形证据：%s（pic=%d，geom=%d）"
                       % ("、".join(weak), stats.pic_count,
                          stats.geom_count + stats.custgeom_count))
        score += 1.5
    if stats.shape_count >= th["pic_combo_shapes"] and stats.pic_count >= th["pic_combo_pics"]:
        reasons.append("D 组合形状+图片多（shape=%d≥%d，pic=%d≥%d）"
                       % (stats.shape_count, th["pic_combo_shapes"],
                          stats.pic_count, th["pic_combo_pics"]))
        score += 2.0
    if stats.group_count >= th["group_pic_groups"] and stats.pic_count >= 1:
        reasons.append("E 组合形状多且含图片（grpSp=%d≥%d，pic=%d）"
                       % (stats.group_count, th["group_pic_groups"], stats.pic_count))
        score += 2.0
    if stats.ole_count >= th["ole_min"]:
        reasons.append("F 含嵌入 OLE 对象（可能是 ChemDraw/公式，ole=%d）" % stats.ole_count)
        score += 3.0
    if not reasons:
        return None
    return {
        "page": stats.page,
        "reasons": reasons,
        "score": round(score, 2),
        "keywords": strong + weak,
        "keywords_strong": strong,
        "keywords_weak": weak,
        **stats.to_dict(),
    }


def _scan_thresholds(overrides: dict | None = None) -> dict:
    th = dict(DEFAULT_SCAN_THRESHOLDS)
    for k, v in (overrides or {}).items():
        if k not in th:
            raise StructureError("未知扫描阈值：%r（可用：%s）"
                                 % (k, "、".join(sorted(th))))
        try:
            th[k] = type(DEFAULT_SCAN_THRESHOLDS[k])(v)
        except (TypeError, ValueError):
            raise StructureError("扫描阈值 %r 的值不合法：%r" % (k, v)) from None
    return th


def scan_thresholds(overrides: dict | None = None) -> dict:
    """公开读取当前扫描阈值（测试/报告用）。"""
    return _scan_thresholds(overrides)


def scan_pptx(doc_id: int, *, db_path: Path | str | None = None,
              dry_run: bool = False, out_dir: Path | str | None = None,
              thresholds: dict | None = None, log=None) -> dict:
    """扫描一份课件的 PPTX XML，挑出疑似结构页（**零 AI**）。

    - `dry_run=True`：只返回结果，**不落盘**；
    - `dry_run=False`（默认）：原子写 `<out_dir>/<doc_id>-结构页.json`；
    - `thresholds`：逐项覆盖 `DEFAULT_SCAN_THRESHOLDS`（产物 meta 记录实际值）。

    返回 dict：
        {ok, doc_id, source_pptx, n_pages, n_candidates, candidates: [...],
         page_stats: [...], meta: {...}, written: path|None}
    失败（无文档/无源文件/坏 pptx）返回 {ok: False, error: "可读原因"}，不抛异常。
    """
    try:
        row, src = _resolve_source(doc_id, db_path)
        slides = _scan_all_slides(src)
        th = _scan_thresholds(thresholds)
    except StructureError as e:
        return {"ok": False, "doc_id": int(doc_id), "error": str(e)}
    candidates: list[dict] = []
    page_stats: list[dict] = []
    for st in slides:
        page_stats.append(st.to_dict())
        cand = _judge_candidate(st, th)
        if cand is not None:
            candidates.append(cand)
    candidates.sort(key=lambda c: (-c["score"], c["page"]))
    # 源文件体检：有的课件 raw 里的 .pptx 是**纯文本转换件**（每页只有 1 个文本框、
    # 0 图片/矢量），原始结构式只存在旧版 .ppt 里 —— 这时 0 候选不代表"没有结构式"，
    # 而是要提示用户用 PowerPoint 把 .ppt 重新另存为 .pptx 后再扫（真实例子 doc#46）。
    warnings: list[str] = []
    if slides:
        avg_shapes = sum(s.shape_count + s.pic_count for s in slides) / len(slides)
        if avg_shapes <= 1.2:
            warnings.append(
                "源 pptx 疑似纯文本转换件（平均每页 %.1f 个形状/图片）：矢量结构式可能"
                "在旧版 .ppt 里；请用 PowerPoint 把 .ppt 另存为 .pptx 后重新扫描。"
                % avg_shapes)
    rule = ("A 矢量元素≥%(vec_geom_min)d 且文本≤%(text_chars_max)d"
            " | B 强关键词≥%(keyword_min_hits)d 个"
            " | C 弱关键词≥%(weak_keyword_min_hits)d 个，或 1 个+图/矢量证据"
            " | D shape≥%(pic_combo_shapes)d 且 pic≥%(pic_combo_pics)d"
            " | E grpSp≥%(group_pic_groups)d 且 pic≥1 | F oleObj≥%(ole_min)d") % th
    meta = {
        "version": VERSION,
        "rule_version": SCAN_RULE_VERSION,
        "doc_id": int(doc_id),
        "doc_title": row.get("title") or "",
        "source_pptx": str(src),
        "generated_at": _now_iso(),
        "n_pages": len(slides),
        "n_candidates": len(candidates),
        "rule": rule,
        "thresholds": th,
        "keywords_strong": list(STRUCT_KEYWORDS_STRONG),
        "keywords_weak": list(STRUCT_KEYWORDS_WEAK),
        "warnings": warnings,
        "note": "零 AI 扫描；candidates 按 score 降序。原图复用 logs/figure_desc/<deck>/slide_*.png。",
    }
    result = {
        "ok": True,
        "doc_id": int(doc_id),
        "source_pptx": str(src),
        "n_pages": len(slides),
        "n_candidates": len(candidates),
        "candidates": candidates,
        "page_stats": page_stats,
        "warnings": warnings,
        "meta": meta,
        "written": None,
    }
    if not dry_run:
        out = scan_path(doc_id, out_dir)
        _atomic_write_json({"meta": meta, "candidates": candidates}, out)
        result["written"] = str(out)
        if log:
            log("结构页扫描：doc#%d %d 页 → %d 个候选 → %s"
                % (doc_id, len(slides), len(candidates), out))
    return result


# ---------------------------------------------------------------- 文本漏抽对比

def scan_text_gaps(doc_id: int, *, db_path: Path | str | None = None,
                   limit_pages: int = 0, log=None) -> dict:
    """对比 python-pptx 顶层 shape 文本 vs XML 全部 `<a:t>`，报告每页漏抽。

    **仅报告，不改入库**：这是"入库时到底漏了多少字"的证据，供后续决定是否重入库。
    `limit_pages>0` 时只比较前 N 页（调试用）。

    漏抽判据（可解释、保守）：XML 里某个文本 run（去空白后）在 python-pptx 顶层文本里
    **找不到**，就算漏抽片段；`missing_chars` 是这些片段的字符数之和。
    顶层文本本身取自 `slide.shapes`（不递归 group、不含表格单元格文本），
    这正是"只抽顶层 shape 文本"的入库口径的对照物。

    返回 dict：
        {ok, doc_id, source_pptx, n_pages, n_compared, pages_with_gaps,
         total_xml_chars, total_missing_chars, coverage_ratio, pages: [...]}
    """
    try:
        row, src = _resolve_source(doc_id, db_path)
        slides = _scan_all_slides(src)
    except StructureError as e:
        return {"ok": False, "doc_id": int(doc_id), "error": str(e)}
    try:
        from pptx import Presentation
    except ImportError:
        return {"ok": False, "doc_id": int(doc_id),
                "error": "缺少 python-pptx，无法做文本覆盖对比（pip install python-pptx）"}
    try:
        prs = Presentation(str(src))
    except Exception as e:  # noqa: BLE001  （第三方库对坏文件会抛各种异常）
        return {"ok": False, "doc_id": int(doc_id),
                "error": "python-pptx 打不开源文件：%s（%s）" % (src, e)}

    xml_by_page = {st.page: st for st in slides}
    pptx_slides = list(prs.slides)
    n = len(pptx_slides)
    if limit_pages and limit_pages > 0:
        n = min(n, int(limit_pages))
    pages: list[dict] = []
    total_xml = total_missing = 0
    pages_with_gaps = 0
    for idx in range(1, n + 1):
        slide = pptx_slides[idx - 1]
        st = xml_by_page.get(idx) or SlideStats(page=idx)
        top_texts: list[str] = []
        top_err = ""
        try:
            for shape in slide.shapes:
                try:
                    if getattr(shape, "has_text_frame", False):
                        t = shape.text_frame.text or ""
                        if t.strip():
                            top_texts.append(t)
                except Exception as e:  # noqa: BLE001  （单 shape 坏不拖垮整页）
                    top_err = "%s: %s" % (type(e).__name__, e)
        except Exception as e:  # noqa: BLE001
            top_err = "%s: %s" % (type(e).__name__, e)
        pptx_text = _norm_space("\n".join(top_texts))
        missing_runs: list[str] = []
        for run in st.text_runs:
            nr = _norm_space(run)
            if not nr:
                continue
            if nr in pptx_text:
                continue
            missing_runs.append(run)
        miss_chars = sum(len(_norm_space(r)) for r in missing_runs)
        total_xml += st.text_chars
        total_missing += miss_chars
        if miss_chars > 0:
            pages_with_gaps += 1
        pages.append({
            "page": idx,
            "pptx_top_chars": sum(len(_norm_space(t)) for t in top_texts),
            "xml_chars": st.text_chars,
            "missing_chars": miss_chars,
            "missing_runs": len(missing_runs),
            "samples": [r[:80] for r in missing_runs[:5]],
            "error": top_err,
        })
    coverage = (1.0 - total_missing / total_xml) if total_xml else 1.0
    result = {
        "ok": True,
        "doc_id": int(doc_id),
        "doc_title": row.get("title") or "",
        "source_pptx": str(src),
        "n_pages": len(slides),
        "n_compared": n,
        "pages_with_gaps": pages_with_gaps,
        "total_xml_chars": total_xml,
        "total_missing_chars": total_missing,
        "coverage_ratio": round(coverage, 4),
        "pages": pages,
        "note": "仅报告：对比 python-pptx 顶层 shape 文本 与 XML 全部 <a:t>；不改入库。",
    }
    if log:
        log("文本漏抽对比：doc#%d 比较 %d 页，%d 页有漏抽，覆盖 %.2f%%"
            % (doc_id, n, pages_with_gaps, coverage * 100))
    return result


# ---------------------------------------------------------------- 整页图复用

_IMG_RE = re.compile(r"^slide_(\d+)\.png$")


def page_image_dir(src: Path) -> Path:
    """整页图缓存目录（与入库同一口径：`logs/figure_desc/<clean stem>/`）。"""
    log_dir = Path(getattr(config, "LOG_DIR", Path(config.ROOT) / "logs"))
    return log_dir / "figure_desc" / figures._safe_dirname(Path(src).stem)


def _list_page_images(directory: Path) -> dict[int, Path]:
    out: dict[int, Path] = {}
    try:
        for p in sorted(Path(directory).glob("slide_*.png")):
            m = _IMG_RE.match(p.name)
            if m:
                out[int(m.group(1))] = p
    except OSError:
        pass
    return out


def find_page_images(doc_id: int, *, db_path: Path | str | None = None,
                     pages: list[int] | None = None, render: bool = False,
                     log=None) -> dict:
    """复用 `logs/figure_desc` 里已缓存的整页图；**默认不渲染**。

    - 返回 {page: 图片路径}、页数、缺图页；
    - `render=True` 时才调 `figures.render_pages` 补缺图（会起 PowerPoint，
      默认不跑；失败（没装 Office/脚本缺失）记录 error 不崩）；
    - `pages` 限定只关心哪些页（默认全部 1..n_pages）。

    返回 dict：
        {ok, doc_id, source_pptx, image_dir, n_pages, images_count,
         images: {"<page>": path}, missing_pages: [...], rendered: int, error: ""}
    """
    try:
        _, src = _resolve_source(doc_id, db_path)
        slides = _scan_all_slides(src)
    except StructureError as e:
        return {"ok": False, "doc_id": int(doc_id), "error": str(e)}
    n_pages = len(slides)
    want = [int(p) for p in pages] if pages else list(range(1, n_pages + 1))
    from . import figure_cache
    outdir = figures.document_image_dir(src, doc_id)
    legacy = page_image_dir(src)
    if not outdir.is_dir() and not render and legacy.is_dir():
        # 仅老版本数据尚无专属缓存时兼容读取；新版本不回退到旧图。
        if not figure_cache.document_directories(config.LOG_DIR, doc_id):
            outdir = legacy
    elif render:
        figures._adopt_verified_legacy_renders(src, outdir)
    images = _list_page_images(outdir)
    missing = [p for p in want if p not in images]
    rendered = 0
    error = ""
    if render and missing:
        try:
            got = figures.render_pages(src, outdir, pages=missing)
            rendered = len(got or {})
            images = _list_page_images(outdir)
            missing = [p for p in want if p not in images]
        except Exception as e:  # noqa: BLE001  （渲染依赖 PowerPoint，失败要可读）
            error = "%s: %s" % (type(e).__name__, e)
    result = {
        "ok": True,
        "doc_id": int(doc_id),
        "source_pptx": str(src),
        "image_dir": str(outdir),
        "n_pages": n_pages,
        "images_count": len([p for p in images if p in want]),
        "images": {str(p): str(images[p]) for p in want if p in images},
        "missing_pages": missing,
        "rendered": rendered,
        "error": error,
        "note": ("整页图来自入库时缓存（零渲染成本）；缺图页不会自动渲染。"
                 if not rendered else "本轮补渲染了 %d 页。" % rendered),
    }
    if log:
        log("整页图：doc#%d 需要 %d 页，已有 %d，缺 %d%s"
            % (doc_id, len(want), len(want) - len(missing), len(missing),
               ("（渲染失败：%s）" % error) if error else ""))
    return result


# ---------------------------------------------------------------- 提示词

def prompt_file(path: Path | str | None = None) -> Path:
    """提示词真源：显式给定 > 本学科 `logs/knowledge/` > 项目根 > 随包资源目录。"""
    if path:
        return Path(path)
    name = PROMPT_NAME
    own = Path(config.LOG_DIR) / "knowledge" / name
    if own.exists():
        return own
    root = Path(config.ROOT) / "logs" / "knowledge" / name
    if root.exists():
        return root
    return Path(config.BUNDLE_ROOT) / "logs" / "knowledge" / name


def _grab_block(md: str, tag: str, path: Path) -> str:
    """逐字提取 `<!-- TAG-BEGIN -->` 与 `<!-- TAG-END -->` 之间的 ```text 代码块。"""
    m = re.search(r"<!-- %s-BEGIN -->\s*```text\n(.*?)\n```\s*<!-- %s-END -->"
                  % (re.escape(tag), re.escape(tag)), md, re.S)
    if not m:
        raise StructureError("提示词真源里找不到 %s 代码块：%s" % (tag, path))
    return m.group(1)


def load_prompt(path: Path | str | None = None) -> dict:
    """从提示词真源逐字提取 SYS/USER 并算文件 sha256（记进产物 meta）。"""
    p = prompt_file(path)
    try:
        blob = p.read_bytes()
    except OSError as e:
        raise StructureError("提示词真源不可读：%s（%s）" % (p, e)) from None
    md = blob.decode("utf-8", "replace")
    sys_p = _grab_block(md, PROMPT_TAG_SYS, p)
    user_p = _grab_block(md, PROMPT_TAG_USER, p)
    return {"sys": sys_p, "user": user_p, "sha256": hashlib.sha256(blob).hexdigest(),
            "path": str(p), "chars": len(sys_p) + len(user_p)}


def build_user_prompt(template: str, *, deck_title: str, page: int,
                      page_text: str, reasons: list[str] | None = None) -> str:
    """用真源里的 USER 模板拼每页提示词（占位符用 replace，模板里可含花括号）。"""
    reasons_txt = "；".join(reasons or []) or "人工指定页（未经启发式筛选）"
    text = (page_text or "").strip()
    if len(text) > 900:
        text = text[:900] + " …（已截断）"
    out = template
    for key, val in (("{deck_title}", deck_title or ""),
                     ("{page}", str(int(page))),
                     ("{reasons}", reasons_txt),
                     ("{page_text}", text)):
        out = out.replace(key, val)
    return out


# ---------------------------------------------------------------- 视觉调用（可注入）

def _api_key() -> str | None:
    """视觉模型 key（与识图管线同一把：环境变量 > DSH 凭据）。测试会 monkeypatch。"""
    try:
        return figures.load_deepseek_key()
    except Exception:  # noqa: BLE001
        return None


def _vision_model() -> str:
    try:
        return str(config.value_of("DEEPSEEK_VISION_MODEL", "deepseek-flash") or
                   "deepseek-flash").strip() or "deepseek-flash"
    except Exception:  # noqa: BLE001
        return "deepseek-flash"


def _cost_of(usage: dict) -> float:
    """按与入库识图同一价格口径记账（`figures.price_of`，默认 off_peak）。"""
    try:
        return float(figures.price_of(usage or {}))
    except Exception:  # noqa: BLE001
        return 0.0


def estimate_cost(calls: int, *, max_tokens: int = STRUCT_MAX_TOKENS,
                  est_in: int = EST_INPUT_TOKENS_MAX) -> dict:
    """dry-run 费用上界（**不含真实调用**）。

    返回 {calls, est_tokens_in, est_tokens_out, cost_cny_peak, cost_cny_off_peak, assumptions}。
    上界口径：输入按 EST_INPUT_TOKENS_MAX、输出按 max_tokens **写满**；真实记账按 usage。
    """
    calls = max(0, int(calls))
    usage = {"prompt_tokens": int(est_in), "completion_tokens": int(max_tokens)}
    peak = round(float(figures.price_of(usage, when="peak")) * calls, 6)
    off = round(float(figures.price_of(usage, when="off_peak")) * calls, 6)
    return {"calls": calls, "est_tokens_in": int(est_in),
            "est_tokens_out": int(max_tokens),
            "cost_cny_peak": peak, "cost_cny_off_peak": off,
            "assumptions": [PRICE_ASSUMPTION_NOTE]}


def _vision_call(img_path: Path, prompt: str, *, model: str,
                 max_tokens: int, thinking: bool, run_context=None) -> dict:
    """真实视觉调用（危险：花钱）。只在 `build_structures(dry_run=False)` 里被调。

    复用 `core/figures.py::_http_post`（带重试/超时），不复制网络代码。
    返回 {ok, text, usage, elapsed, model, error}；网络/HTTP 错误收在 error 里，不抛。
    """
    from . import agent_bridge
    vctx = (run_context if run_context is not None
            and runtime_context.runtime_enabled() else None)
    if vctx is not None:
        model = str(vctx.model)
        max_tokens = int(vctx.max_tokens or 0)
        thinking = bool(vctx.thinking)
    key = (config.pipeline_key(runtime_context.canonical_credential_ref(
        str(getattr(vctx, "credential_ref", "") or vctx.pipeline))) if vctx is not None else "") or _api_key()
    if not key and not agent_bridge.has_adapter(
            str(vctx.pipeline) if vctx is not None else "structures"):
        return {"ok": False, "error": "没有 DEEPSEEK_API_KEY", "usage": {},
                "elapsed": 0.0, "model": model, "text": ""}
    try:
        b64 = base64.b64encode(Path(img_path).read_bytes()).decode()
    except OSError as e:
        return {"ok": False, "error": "读整页图失败：%s（%s）" % (img_path, e),
                "usage": {}, "elapsed": 0.0, "model": model, "text": ""}
    payload = {
        "model": model,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url",
                 "image_url": {"url": "data:image/png;base64,%s" % b64}},
            ],
        }],
        "stream": False,
    }
    if int(max_tokens or 0) > 0:
        payload["max_tokens"] = int(max_tokens)
    if not thinking:
        payload["thinking"] = {"type": "disabled"}
    t0 = time.perf_counter()
    with agent_bridge.pipeline_scope(
            str(vctx.pipeline) if vctx is not None else "structures"):
        st, body = figures._http_post(
            "%s/chat/completions" % str(getattr(config, "DEEPSEEK_BASE_URL",
                                                "https://api.deepseek.com")).rstrip("/"),
            payload, key or "", run_context=vctx)
    el = time.perf_counter() - t0
    if st != 200 or not isinstance(body, dict):
        return {"ok": False, "error": "HTTP %s: %s" % (st, str(body)[:300]),
                "usage": {}, "elapsed": el, "model": model, "text": ""}
    usage = body.get("usage", {}) or {}
    content = (body.get("choices") or [{}])[0].get("message", {}).get("content", "")
    return {"ok": True, "text": content or "", "usage": usage, "elapsed": el,
            "model": body.get("model", model), "error": ""}


def _extract_json(text: str) -> dict | None:
    """从模型输出里抠出 JSON 对象（容忍 markdown 围栏/前后废话）。"""
    s = (text or "").strip()
    if not s:
        return None
    # 1) 优先 fenced code block 里的内容
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", s, re.S)
    cand = m.group(1) if m else s
    # 2) 第一个 { 到最后一个 }
    i, j = cand.find("{"), cand.rfind("}")
    if i < 0 or j <= i:
        return None
    try:
        obj = json.loads(cand[i:j + 1])
    except ValueError:
        return None
    return obj if isinstance(obj, dict) else None


_UNCERTAINTY = ("high", "medium", "low")


def _normalize_struct(obj: dict) -> dict:
    """把模型 JSON 归一成固定 schema（字段缺失给默认，布尔/枚举做保守修正）。"""
    has = obj.get("has_structure")
    if isinstance(has, str):
        has = has.strip().lower() in ("true", "1", "yes", "是")
    structure = str(obj.get("structure") or "").strip()
    reaction = str(obj.get("reaction") or "").strip()
    smiles = str(obj.get("smiles") or "").strip()
    if has is None:
        has = bool(structure or reaction or smiles)
    if not has:
        # 明确"无结构"时，不保留模型可能顺手写的猜测内容（宁空勿错）
        structure = reaction = smiles = ""
    unc = str(obj.get("uncertainty") or "").strip().lower()
    if unc not in _UNCERTAINTY:
        unc = "high" if not has else "medium"
    notes = str(obj.get("notes") or "").strip()
    return {"has_structure": bool(has), "structure": structure, "smiles": smiles,
            "reaction": reaction, "uncertainty": unc, "notes": notes}


# ---------------------------------------------------------------- 构建（默认 dry-run）

def _load_struct_out(doc_id: int, out_dir: Path | str | None) -> dict:
    data = _read_json(struct_path(doc_id, out_dir)) or {}
    if not isinstance(data.get("pages"), dict):
        data["pages"] = {}
    return data


def _cache_usable(entry: dict | None, img_path: Path, prompt_sha: str) -> bool:
    """缓存复用判据：同一张图 + 同一版提示词 + 上次成功（无 error）。"""
    if not entry or entry.get("error"):
        return False
    try:
        ih = _sha256_file(img_path)
    except OSError:
        return False
    return (str(entry.get("image_hash") or "") == ih
            and str(entry.get("prompt_sha256") or "") == prompt_sha)


def build_structures(doc_id: int, *, pages: list[int] | None = None,
                     dry_run: bool = True, force: bool = False, limit: int = 0,
                     db_path: Path | str | None = None,
                     out_dir: Path | str | None = None,
                     prompt_path: Path | str | None = None,
                     model: str | None = None,
                     max_tokens: int = STRUCT_MAX_TOKENS,
                     thinking: bool = STRUCT_THINKING,
                     vision_fn=None, log=print, run_context=None) -> dict:
    """对候选结构页做**专用识图**（默认 dry-run：0 调用、0 写盘）。

    - `pages`：只处理这些页（默认=扫描出的候选页，按 score 降序，最多 `limit` 个）；
    - `dry_run=True`（默认）：只返回候选/预计调用数/费用上界，**不写盘、不调模型**；
    - `force=True`：忽略缓存，重跑选中页；
    - `limit`：本轮最多**真实调用** N 页（缓存命中不计；调试用）；
    - `vision_fn`：可注入的视觉函数（测试用假函数，签名同 `_vision_call`）；
      注入时不需要真实 API key。

    缓存：`logs/knowledge/<doc>-结构式.json` 里已有页按
    `image_hash + prompt_sha256` 复用（上次失败的页不复用，会重试）。
    失败页记 `error` 继续下一页，不中断。

    返回 dict（dry-run 与真实构建共用外壳）：
        {ok, doc_id, dry_run, dry_run_note, source_pptx, out_path, prompt_path,
         prompt_sha256, model, candidates: [...], pages: [选中的页],
         pages_missing_image: [...], estimate: {...}, calls: [...],
         n_calls, n_cached, tokens: {in,out}, cost_cny, elapsed_s, written,
         errors: [...], meta: {...}}
    """
    # P0-F：结构式入口只建一次 context；PIPELINE_RUNTIME=0 不建。
    run_ctx = (run_context if run_context is not None
               else (runtime_context.make_context("structures", doc_id=doc_id)
                     if runtime_context.runtime_enabled() else None))
    if run_ctx is not None:
        model = str(run_ctx.model)
        max_tokens = int(run_ctx.max_tokens or 0)
        thinking = bool(run_ctx.thinking)
    try:
        row, src = _resolve_source(doc_id, db_path)
        slides = _scan_all_slides(src)
        prompt = load_prompt(prompt_path)
    except StructureError as e:
        return {"ok": False, "doc_id": int(doc_id), "error": str(e)}
    page_stats = {st.page: st for st in slides}
    n_pages = len(slides)
    th = _scan_thresholds()
    candidates = [c for c in (_judge_candidate(st, th) for st in slides) if c]
    if pages:
        try:
            want_pages = [int(p) for p in pages]
        except (TypeError, ValueError):
            return {"ok": False, "doc_id": int(doc_id),
                    "error": "pages 需要整数页号列表：%r" % (pages,)}
        bad = [p for p in want_pages if p < 1 or p > n_pages]
        if bad:
            return {"ok": False, "doc_id": int(doc_id),
                    "error": "页号越界：%s（这份课件共 %d 页）" % (bad, n_pages)}
        target_pages = sorted(set(want_pages))
        target_reasons = {p: (next((c["reasons"] for c in candidates
                                    if c["page"] == p), ["人工指定页"]))
                          for p in target_pages}
    else:
        target_pages = [int(c["page"]) for c in candidates]
        target_reasons = {int(c["page"]): c["reasons"] for c in candidates}
    # 整页图复用（不渲染）
    img_result = find_page_images(doc_id, db_path=db_path, pages=target_pages)
    images = {int(k): Path(v) for k, v in (img_result.get("images") or {}).items()}
    missing_img = list(img_result.get("missing_pages") or [])
    usable_pages = [p for p in target_pages if p in images]
    existing = _load_struct_out(doc_id, out_dir)
    old_pages = existing.get("pages") or {}
    prompt_sha = prompt["sha256"]
    model = model or _vision_model()
    out_path = struct_path(doc_id, out_dir)

    # 先算缓存命中（dry-run 也要给"真实需要几次调用"）
    cached_pages: list[int] = []
    todo_pages: list[int] = []
    for p in usable_pages:
        if not force and _cache_usable(old_pages.get(str(p)), images[p], prompt_sha):
            cached_pages.append(p)
        else:
            todo_pages.append(p)
    if limit and limit > 0:
        run_pages = todo_pages[:int(limit)]
        skipped_by_limit = todo_pages[int(limit):]
    else:
        run_pages = todo_pages
        skipped_by_limit = []
    estimate = estimate_cost(len(run_pages), max_tokens=max_tokens)
    result = {
        "ok": True,
        "doc_id": int(doc_id),
        "dry_run": bool(dry_run),
        "source_pptx": str(src),
        "out_path": str(out_path),
        "prompt_path": prompt["path"],
        "prompt_sha256": prompt_sha,
        "model": model,
        "max_tokens": int(max_tokens),
        "thinking": bool(thinking),
        "n_pages": n_pages,
        "candidates": candidates,
        "pages": usable_pages,
        "pages_cached": cached_pages,
        "pages_missing_image": missing_img,
        "pages_skipped_by_limit": skipped_by_limit,
        "estimate": estimate,
        "calls": [],
        "n_calls": 0,
        "n_cached": len(cached_pages),
        "tokens": {"in": 0, "out": 0},
        "cost_cny": 0.0,
        "elapsed_s": 0.0,
        "written": None,
        "errors": [],
        "meta": {},
    }
    if dry_run:
        result["dry_run_note"] = (
            "dry-run：0 次模型调用、0 写盘。上面 estimate 是费用**上界**；"
            "真实调用请显式加 --run（CLI 会再次确认）。"
        )
        result["plan"] = [{"page": p,
                           "reasons": target_reasons.get(p, []),
                           "image": str(images[p])} for p in run_pages]
        if log:
            log("结构式构建（dry-run）：doc#%d 候选 %d 页，有图 %d 页，缓存 %d 页，"
                "本轮预计调用 %d 页，费用上界 ¥%.4f（闲时）/ ¥%.4f（峰值）"
                % (doc_id, len(candidates), len(usable_pages), len(cached_pages),
                   len(run_pages), estimate["cost_cny_off_peak"],
                   estimate["cost_cny_peak"]))
        return result

    # ---------- 真实构建（dry_run=False） ----------
    from . import agent_bridge
    key = (config.pipeline_key(runtime_context.canonical_credential_ref(
        str(getattr(run_ctx, "credential_ref", "") or run_ctx.pipeline))) if run_ctx is not None else "") or _api_key()
    if vision_fn is None and not key and not agent_bridge.has_adapter(
            str(run_ctx.pipeline) if run_ctx is not None else "structures"):
        return {"ok": False, "doc_id": int(doc_id),
                "error": "没有 DEEPSEEK_API_KEY：真实构建需要视觉模型密钥；"
                         "dry-run 不受影响（可先 --scan/--dry-run 看计划）。"}
    call_fn = vision_fn or _vision_call
    call_kw = ({"run_context": run_ctx} if vision_fn is None and run_ctx is not None
               else {})
    started = time.perf_counter()
    new_entries = 0
    data = {"meta": {}, "pages": dict(old_pages)}
    for p in run_pages:
        img = images[p]
        user_prompt = build_user_prompt(
            prompt["user"], deck_title=str(row.get("title") or ""), page=p,
            page_text=page_stats[p].text if p in page_stats else "",
            reasons=target_reasons.get(p))
        try:
            call_result = call_fn(
                img, "%s\n\n%s" % (prompt["sys"], user_prompt),
                model=model, max_tokens=int(max_tokens), thinking=bool(thinking),
                **call_kw)
        except Exception as e:  # noqa: BLE001  （注入的假函数/网络都算失败页）
            call_result = {"ok": False, "error": "%s: %s" % (type(e).__name__, e),
                           "usage": {}, "elapsed": 0.0, "model": model, "text": ""}
        usage = call_result.get("usage") or {}
        cost = _cost_of(usage) if call_result.get("ok") else 0.0
        raw_text = call_result.get("text") or ""
        entry = {
            "page": p,
            "image": str(img),
            "image_hash": _sha256_file(img),
            "prompt_sha256": prompt_sha,
            "model": call_result.get("model") or model,
            "thinking": bool(thinking),
            "calls": 1,
            "cost_cny": round(cost, 6),
            "tokens": {"in": int(usage.get("prompt_tokens") or 0),
                       "out": int(usage.get("completion_tokens") or 0)},
            "elapsed_s": round(float(call_result.get("elapsed") or 0.0), 2),
            "generated_at": _now_iso(),
            "error": "",
        }
        if not call_result.get("ok"):
            entry.update({"has_structure": False, "structure": "", "smiles": "",
                          "reaction": "", "uncertainty": "high", "notes": "",
                          "error": str(call_result.get("error") or "未知错误")[:500]})
            result["errors"].append({"page": p, "error": entry["error"]})
        else:
            obj = _extract_json(raw_text)
            if obj is None:
                entry.update({"has_structure": False, "structure": "", "smiles": "",
                              "reaction": "", "uncertainty": "high", "notes": "",
                              "error": "模型输出不是合法 JSON：%s"
                                       % raw_text[:160].replace("\n", " ")})
                result["errors"].append({"page": p, "error": entry["error"]})
            else:
                entry.update(_normalize_struct(obj))
                entry["raw_excerpt"] = raw_text[:400]
        data["pages"][str(p)] = entry
        new_entries += 1
        result["calls"].append({
            "page": p, "ok": call_result.get("ok") and not entry.get("error"),
            "cached": False, "cost_cny": entry["cost_cny"],
            "tokens": entry["tokens"], "elapsed_s": entry["elapsed_s"],
            "error": entry.get("error", ""),
        })
        result["n_calls"] += 1
        result["tokens"]["in"] += entry["tokens"]["in"]
        result["tokens"]["out"] += entry["tokens"]["out"]
        result["cost_cny"] = round(result["cost_cny"] + entry["cost_cny"], 6)
        # 逐页原子写：已经花掉的页，不能因为后面某页崩溃而丢
        meta = {
            "version": VERSION,
            "doc_id": int(doc_id),
            "doc_title": row.get("title") or "",
            "source_pptx": str(src),
            "model": model,
            "thinking": bool(thinking),
            "max_tokens": int(max_tokens),
            "prompt_sha256": prompt_sha,
            "prompt_path": prompt["path"],
            "generated_at": _now_iso(),
            "n_pages": len(data["pages"]),
            "n_cached": len(cached_pages) + sum(
                1 for k in data["pages"]
                if str(k) not in {str(x) for x in run_pages}),
            "n_errors": sum(1 for v in data["pages"].values() if v.get("error")),
            "elapsed_s": round(time.perf_counter() - started, 1),
        }
        data["meta"] = meta
        # 每页都重算累计（真实记账以产物为准，断点续跑也能对上）
        data["meta"].update({
            "calls": sum(int(v.get("calls") or 0) for v in data["pages"].values()),
            "cost_cny": round(sum(float(v.get("cost_cny") or 0.0)
                                  for v in data["pages"].values()), 6),
            "tokens": {"in": sum(int((v.get("tokens") or {}).get("in") or 0)
                                 for v in data["pages"].values()),
                       "out": sum(int((v.get("tokens") or {}).get("out") or 0)
                                  for v in data["pages"].values())},
        })
        _atomic_write_json(data, out_path)
        if log:
            if entry.get("error"):
                log("  第 %d 页失败：%s" % (p, entry["error"][:120]))
            else:
                log("  第 %d 页完成：has_structure=%s uncertainty=%s ¥%.4f"
                    % (p, entry["has_structure"], entry["uncertainty"],
                       entry["cost_cny"]))
    result["elapsed_s"] = round(time.perf_counter() - started, 1)
    result["n_cached"] = len(cached_pages)
    result["meta"] = data["meta"]
    result["written"] = str(out_path) if new_entries else None
    # 全缓存命中且无新增：不写盘（避免 mtime 抖动），但把产物现状回填给调用方
    if not new_entries and out_path.exists():
        prev = _read_json(out_path) or {}
        result["meta"] = prev.get("meta") or {}
        result["out_path"] = str(out_path)
    result["ok"] = True
    if log:
        log("结构式构建完成：doc#%d 真实调用 %d 页 / 缓存 %d 页，本次 ¥%.4f，累计 ¥%.4f"
            % (doc_id, result["n_calls"], result["n_cached"], result["cost_cny"],
               float((result["meta"] or {}).get("cost_cny") or 0.0)))
    return result


# ---------------------------------------------------------------- 状态摘要

def structure_status(doc_id: int, *, out_dir: Path | str | None = None) -> dict:
    """读结构页/结构式产物的摘要（只读，供后续 API/UI）。

    返回：
        {ok, doc_id, scan:{exists,...}, structures:{exists,...}}
    产物缺失/损坏不报错（`exists=False` + 可读 error），看板可以据此显示"未生成"。
    """
    sp = scan_path(doc_id, out_dir)
    op = struct_path(doc_id, out_dir)
    scan_data = _read_json(sp)
    struct_data = _read_json(op)
    scan = {"exists": scan_data is not None, "path": str(sp)}
    if scan_data is not None:
        meta = scan_data.get("meta") or {}
        cands = scan_data.get("candidates") or []
        scan.update({
            "version": meta.get("version"),
            "generated_at": meta.get("generated_at"),
            "n_pages": int(meta.get("n_pages") or 0),
            "n_candidates": int(meta.get("n_candidates") or len(cands)),
            "rule": meta.get("rule") or "",
            "candidates_pages": [int(c.get("page") or 0) for c in cands],
        })
    structures = {"exists": struct_data is not None, "path": str(op)}
    if struct_data is not None:
        meta = struct_data.get("meta") or {}
        pages = struct_data.get("pages") or {}
        structures.update({
            "version": meta.get("version"),
            "generated_at": meta.get("generated_at"),
            "model": meta.get("model") or "",
            "calls": int(meta.get("calls") or 0),
            "cost_cny": float(meta.get("cost_cny") or 0.0),
            "tokens": meta.get("tokens") or {"in": 0, "out": 0},
            "prompt_sha256": meta.get("prompt_sha256") or "",
            "n_pages": len(pages),
            "n_errors": sum(1 for v in pages.values() if v.get("error")),
            "pages": sorted(int(k) for k in pages),
            "with_structure": sorted(int(k) for k, v in pages.items()
                                     if v.get("has_structure")),
        })
    return {"ok": True, "doc_id": int(doc_id), "scan": scan, "structures": structures}


__all__ = [
    "VERSION", "PROMPT_NAME", "STRUCT_KEYWORDS", "STRUCT_KEYWORDS_STRONG",
    "STRUCT_KEYWORDS_WEAK", "DEFAULT_SCAN_THRESHOLDS",
    "StructureError", "scan_pptx", "scan_text_gaps", "find_page_images",
    "scan_thresholds", "load_prompt", "prompt_file", "build_user_prompt",
    "build_structures", "structure_status", "estimate_cost",
    "scan_path", "struct_path", "default_out_dir", "page_image_dir",
]
