# Copyright (c) 2026 潜声海豚 <2227663973@qq.com>. All rights reserved.
# 本源码为著作权人私有作品，适用项目根目录 LICENSE（专有许可）。
# 允许个人安装与使用；禁止反向工程、修改、再分发、商业使用及声称原创。
# 本软件不含源码，按「现状」提供；详见 NOTICE.txt。     
# File-ID: SA-21-3f81c9d2   (project study_agent; keep this line)
r"""课件分享包：导出（入库数据 + 向量 + 知识点产物）与导入（零 API 调用重建）。

## 这是什么

一份课件在库里 = `documents` 一行 + `chunks` N 行 + `embeddings` N 行 +
`figure_desc` M 行 + `logs/knowledge/<doc_id>-末级知识点总结.json`（可能还有
`<doc_id>-名词解释.json`）。把它打成一个 zip 交给别人，对方**不用重新抽取、
不用重新切块、不用重新嵌入、不用重新跑 AI 总结**，就能得到同样的库内容与知识点。

⇒ 一次付费的成果可以被 N 个人复用（摊平 API 费用），也让"用更贵的模型做一次总结"
在多人之间摊薄成本。

## 红线：导入侧零模型调用

本模块**不导入** `core.embed` / `core.kp_build` / `core.chat`，也不发起任何网络请求
（标准库只用到 zipfile/json/sqlite3/…）。导入路径上没有任何一次嵌入或对话调用，
因此在调用它的进程里既不会加载 ONNX 模型、也不会产生费用。
唯一的"模型相关"动作是**比对模型名与维度**（纯字符串/整数比较），不一致就拒绝。

## 包格式（单课件一个 zip，条目名一律用 `/` 分隔）

```
manifest.json          格式版本 / 导出时间 / 来源 / 模型信息 / 每个条目的 sha256
doc.json               documents 行（去掉 id/path/abs_path 等本机字段）
chunks.jsonl           每行一个块：id(源)/ord/text/heading_path/topic/source_ref/…
embeddings.jsonl       每行 {ord, src_id, model, dim, vec_b64}
figure_desc.jsonl      每行一页识图描述（含 description 正文）
knowledge/<源doc_id>-末级知识点总结.json   （存在才带）
knowledge/<源doc_id>-名词解释.json         （存在才带）
original/<原课件文件名>                    （可选，勾选才带）
pages/<目录>/slide_NN.png                  （可选，勾选才带）
README.txt             告诉接收方这是什么、怎么导入
```

## 为什么用 `ord`+`src_id` 双键，而不是只用 `ord`

实测源库的 `chunks.ord` **不唯一**：正文块是 0..n-1，识图块用 `9000 + 页号*10 + k`
（同一页最多 10 个识图块，因此 9 个块共享同一个 ord，见 doc#5 = 9014 有 9 行）。
只用 ord 关联会让同页的多个向量错位。所以 `embeddings.jsonl` 同时带 `src_id`
（**源库** chunk id，仅作包内关联键，**不是**新库主键）与 `ord`：
优先按 src_id 精确关联；只有旧包没带 src_id 时才回落到 ord 顺序。

## 导入时的三处一致性（缺一个看板就关联不上）

1. `chunks` 与 `embeddings` 用 src_id/ord 对应，导入后校验行数与正文块 ord 连续；
2. 产物**文件名**里的 doc_id 与产物内 `meta.doc_id` **同时**改成新 doc_id；
3. 产物内引用的块 id（`sources[].chunk_ids` / `material_block_ids` /
   `material[].chunk_id` / `terms[].evidence[].chunk_id`）全部按 源id→新id 重映射，
   映射不到的引用**移除/置空**并计数（宁可少一条引用，也不留一个指向别人块的假引用）。

## 安全（导入别人的包 = 接收外部数据）

- zip 条目名：反斜杠、绝对路径、盘符、UNC、`..`、设备名（CON/NUL…）一律拒绝；
- 体积：条目数 ≤ `MAX_ENTRIES`，单条目解压 ≤ `MAX_ENTRY_UNCOMPRESSED`，
  总解压 ≤ `MAX_TOTAL_UNCOMPRESSED`，单条目压缩比 > `MAX_COMPRESS_RATIO` 判为炸弹；
- 结构：必需条目缺失 / JSON 类型不符 / 行内字段缺失 / 行数不符 → 拒绝；
- 完整性：逐条目比对 manifest 里的 sha256，不符即拒绝（包坏了或被改过）;
- 模型：`embeddings.model`+`dim` 与本库当前嵌入模型不一致 → **拒绝**并给可操作说明
  （错向的向量比报错更坑：检索会返回荒谬结果）；
- 来源：包信息写进 `documents.extra.shared_from`，事后可追溯谁导的、从哪个库导的。

## 写库口径

- 分配新 `doc_id`（AUTOINCREMENT，不覆盖现有）；
- `path` 写**接收方本库**的相对路径（与 `ingest._rel` 同语义）：带原文件就
  `raw\<原文件名>`，不带就 `raw\_share_import\<标题>-<hash8>.sharepack`（占位，
  明确标注来自分享包）；`role`/`corpus` 照抄源库，来源信息进 `extra`；
- 不动表结构（不加列、不改表），只写既有列；
- `text_norm` **不带在包里**，导入时用 `db.normalize_for_match(text)` 重算
  （纯函数、零成本，省掉约 40% 的包体积，且保证与接收方的检索规则一致）。
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import re
import shutil
import sqlite3
import time
import zipfile
from pathlib import Path
from typing import Callable, Iterator

from . import config, db

# ---------------------------------------------------------------- 常量

FORMAT = "study_agent.courseware_share"
FORMAT_VERSION = 1

MANIFEST = "manifest.json"
DOC = "doc.json"
CHUNKS = "chunks.jsonl"
EMBEDDINGS = "embeddings.jsonl"
FIGURES = "figure_desc.jsonl"
READ_ME = "README.txt"
KNOW_DIR = "knowledge"
ORIG_DIR = "original"
PAGE_DIR = "pages"

REQUIRED_ENTRIES = (MANIFEST, DOC, CHUNKS, EMBEDDINGS)

# 产物文件名规则，与 `core/kp_build.py::KP_SUFFIX / TERMS_SUFFIX` **逐字一致**
# （tools/verify_c0079_share.py 里有断言，防两处漂移）。这里不 import kp_build：
# 那个模块会连带拉起整条整理管线，而分享包的两个动作都不需要它。
KP_SUFFIX = "-末级知识点总结.json"
TERMS_SUFFIX = "-名词解释.json"
# 卡 C-0095：预习产物一并进包（人的原话 2026-10-14「该功能加载后，**导出的包也要有
# 以上产物**」）。两个后缀分别与 `core/preview.py::PREVIEW_SUFFIX`（C-0094 预习总结）
# 和 `core/sections.py::ARTIFACT_SUFFIX`（C-0092 章节细目）**逐字一致**；同样**不 import**
# 那两个模块——`core/preview.py` 会连带拉起 kp_build 与整条预习管线，而导出/导入
# 两个动作都不需要它们，一条常量不值得换来一串重依赖。
PREVIEW_SUFFIX = "-预习总结.json"
SECTIONS_SUFFIX = "-章节细目.json"
KNOW_SUFFIXES = (KP_SUFFIX, TERMS_SUFFIX, PREVIEW_SUFFIX, SECTIONS_SUFFIX)

# 识图块的 ord 偏移（`core/figures.py` 写库时用 `9000 + page*10 + k`）。
# 这里只用它区分"正文块 / 识图块"，**不重排、不改写**任何 ord。
FIG_ORD_BASE = 9000

# 导入时占位路径的目录名与后缀（不带原文件时用；一眼看出"这行不是本机 raw 文件"）
PLACEHOLDER_DIR = "_share_import"
PLACEHOLDER_EXT = ".sharepack"

# 默认导出目录（相对项目根）
SHARE_DIRNAME = "share"

# ---------------- 安全上限（报告里要写清这几个数） ----------------
# 依据：实测一份带页图+原文件的包 = 3~30 MB（有机化学 18 份里最大 28 MB 页图 + 8 MB 原文件）。
# 上限取的是"正常包绝对碰不到、zip 炸弹一定撞死"的量级。
MAX_ENTRIES = 4000                 # 条目数上限（一份课件的页图最多 ~100 张 + 4 个数据文件）
MAX_ENTRY_UNCOMPRESSED = 512 * 1024 ** 2   # 单条目解压上限 512 MB
MAX_TOTAL_UNCOMPRESSED = 2 * 1024 ** 3     # 总解压上限 2 GB
MAX_COMPRESS_RATIO = 200           # 单条目压缩比上限（正常 zip 的 png 约 1.0，jsonl 约 5~20）
MAX_META_BYTES = 8 * 1024 ** 2     # manifest/doc.json 上限
MAX_LINE_BYTES = 16 * 1024 ** 2    # jsonl 单行上限（最大块文本约 3KB，留足余量）

_BAD_NAME_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WS_RE = re.compile(r"[\s\u3000]+")
_DRIVE_RE = re.compile(r"^[A-Za-z]:")
_DEV_RE = re.compile(r"^(con|prn|aux|nul|com[1-9]|lpt[1-9])(\..*)?$", re.I)
_SLIDE_RE = re.compile(r"^slide_(\d+)\.png$", re.I)

Progress = Callable[[str, int, int], None]


class ShareError(Exception):
    """分享包的导出/导入失败。消息**直接给人看**，因此都写成可操作的中文说明。"""


# ---------------------------------------------------------------- 小工具

def clean_name(name: str, maxlen: int = 60) -> str:
    """标题 → 可做文件名/zip 名的清洗结果。

    与 `core/subjects.py::slugify` 同一个思路（中文原样保留、只去掉文件系统
    非法字符），但**不做**空白折叠成 `-`：zip 名和原文件名要让人一眼认出是哪份课件。
    """
    s = _BAD_NAME_RE.sub("_", (name or "").strip())
    s = _WS_RE.sub(" ", s).strip(" .")
    s = s[:maxlen].strip(" .")
    return s or "courseware"


def safe_dirname(name: str) -> str:
    """Windows 目录名清洗，与 `core/figures.py::_safe_dirname` **逐字一致**。

    页图目录名由它决定（`logs/figure_desc/<清洗后的文件名 stem>/`）。
    规则必须与 figures 一致，否则"以后重跑识图"会找不到这里的图、
    白花一次渲染/调用；一致性由 `tools/verify_c0079_share.py` 断言守住。
    """
    return _BAD_NAME_RE.sub("_", name)[:60]


def rel_to(path: Path, root: Path) -> str:
    """与 `core/ingest.py::_rel` 同语义：能相对项目根就相对，否则给绝对路径。"""
    try:
        return str(Path(path).resolve().relative_to(Path(root).resolve()))
    except ValueError:
        return str(Path(path).resolve())


def sha256_file(path: Path, block: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(block)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def _iso(ts: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))


def _now() -> float:
    return time.time()


def _ipc(progress: Progress | None, stage: str, done: int, total: int) -> None:
    if progress is not None:
        try:
            progress(stage, int(done), int(total))
        except Exception:  # noqa: BLE001 —— 进度回调绝不能影响导入本身
            pass


def _subject_of(log_dir: Path, root: Path) -> str:
    """从 LOG_DIR 反推学科名（默认学科 = 项目根）。纯展示用，读不到就退回 slug。"""
    d = Path(log_dir).parent
    try:
        if d.resolve() == Path(root).resolve():
            return "默认"
    except OSError:
        pass
    meta = d / "subject.json"
    try:
        return json.loads(meta.read_text(encoding="utf-8")).get("name") or d.name
    except (OSError, ValueError):
        return d.name


# ---------------------------------------------------------------- 包的目录/条目名

def _check_entry_name(name: str) -> None:
    r"""条目名安全校验：拒绝一切可能逃出解压目录的名字。

    为什么反斜杠也一并拒绝：zip 规范里分隔符只有 `/`，出现 `\` 的包要么是
    Windows 老工具造的、要么是**故意**用 `..\..\` 绕过按 `/` 切分的检查。
    正常导出（本模块）永远只用 `/`，拒绝它不会误伤真包。
    """
    if not name or not name.strip():
        raise ShareError("包里有一个空条目名，拒绝导入")
    if "\x00" in name:
        raise ShareError("包内条目名含 NUL 字符，拒绝导入：%r" % name)
    if "\\" in name:
        raise ShareError(
            "包内条目用了反斜杠分隔（%s）：拒绝导入。\n"
            "正常分享包只用 / 分隔；含反斜杠的包可能是路径穿越构造。" % name)
    if name.startswith("/"):
        raise ShareError("包内有绝对路径条目（%s）：拒绝导入" % name)
    if _DRIVE_RE.match(name):
        raise ShareError("包内有带盘符的条目（%s）：拒绝导入" % name)
    parts = [p for p in name.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise ShareError("包内条目含 .. 路径穿越（%s）：拒绝导入" % name)
    if parts and _DEV_RE.match(parts[-1]):
        raise ShareError("包内条目是 Windows 设备名（%s）：拒绝导入" % name)


def _scan_zip(zf: zipfile.ZipFile) -> dict[str, zipfile.ZipInfo]:
    """安全扫描：条目名 + 条目数 + 体积/压缩比。返回 {名字: ZipInfo}（跳过目录项）。"""
    infos = zf.infolist()
    if len(infos) > MAX_ENTRIES:
        raise ShareError("包的条目数 %d 超过上限 %d，拒绝导入（疑似构造包）"
                         % (len(infos), MAX_ENTRIES))
    out: dict[str, zipfile.ZipInfo] = {}
    total = 0
    for i in infos:
        _check_entry_name(i.filename)
        if i.filename.endswith("/"):
            continue
        if i.filename in out:
            raise ShareError("包里有两个同名条目（%s）：拒绝导入（会被覆盖成不可预期的内容）"
                             % i.filename)
        if i.file_size > MAX_ENTRY_UNCOMPRESSED:
            raise ShareError("条目 %s 解压后 %d 字节，超过单条目上限 %d 字节，拒绝导入"
                             % (i.filename, i.file_size, MAX_ENTRY_UNCOMPRESSED))
        if i.compress_size > 0 and i.file_size / max(i.compress_size, 1) > MAX_COMPRESS_RATIO:
            raise ShareError(
                "条目 %s 压缩比 %.0f 倍，超过上限 %d 倍，判定为 zip 炸弹，拒绝导入"
                % (i.filename, i.file_size / max(i.compress_size, 1), MAX_COMPRESS_RATIO))
        total += i.file_size
        if total > MAX_TOTAL_UNCOMPRESSED:
            raise ShareError("包解压总量超过上限 %d GB，拒绝导入（疑似 zip 炸弹）"
                             % (MAX_TOTAL_UNCOMPRESSED // (1024 ** 3)))
        out[i.filename] = i
    return out


class _Entry:
    """流式读一个 zip 条目：逐行 yield 文本，读完核对 sha256。

    为什么要流式：一份包的 `chunks.jsonl`/`embeddings.jsonl` 可能有几十 MB，
    整份 `read()` 进内存会随课件页数线性膨胀。这里固定 1 MB 缓冲。
    """

    def __init__(self, zf: zipfile.ZipFile, name: str, want_sha: str = "",
                 limit: int = MAX_LINE_BYTES):
        self.zf = zf
        self.name = name
        self.want = want_sha or ""
        self.limit = limit
        self.bytes = 0
        self.sha = ""

    def _open(self):
        try:
            return self.zf.open(self.name)
        except KeyError:
            raise ShareError("包里缺少必需条目：%s" % self.name)
        except zipfile.BadZipFile as e:
            raise ShareError("包已损坏（%s）：%s" % (self.name, e))

    def text(self, max_bytes: int = MAX_META_BYTES) -> str:
        """小文件（manifest/doc.json）整份读，带 sha256 核对与体积上限。"""
        h = hashlib.sha256()
        buf = bytearray()
        with self._open() as src:
            while True:
                b = src.read(1 << 20)
                if not b:
                    break
                h.update(b)
                buf += b
                if len(buf) > max_bytes:
                    raise ShareError("条目 %s 超过 %d MB 上限，拒绝导入"
                                     % (self.name, max_bytes // (1024 ** 2)))
        self.sha = h.hexdigest()
        self.bytes = len(buf)
        if self.want and self.sha != self.want:
            raise ShareError("包内文件校验失败（sha256 不符）：%s\n"
                             "包可能没拷完整或被改过，请让导出方重新导出。" % self.name)
        try:
            return buf.decode("utf-8")
        except UnicodeDecodeError as e:
            raise ShareError("条目 %s 不是 UTF-8 文本：%s" % (self.name, e))

    def data(self) -> bytes:
        return self.text().encode("utf-8")

    def write_to(self, dest: Path, hash_check: bool = True) -> tuple[int, str]:
        """二进制条目流式落盘（原文件 / 页图）。返回 (字节数, sha256)。"""
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".part")
        h = hashlib.sha256()
        n = 0
        try:
            with self._open() as src, open(tmp, "wb") as out:
                while True:
                    b = src.read(1 << 20)
                    if not b:
                        break
                    out.write(b)
                    h.update(b)
                    n += len(b)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        self.sha = h.hexdigest()
        self.bytes = n
        if hash_check and self.want and self.sha != self.want:
            tmp.unlink(missing_ok=True)
            raise ShareError("包内文件校验失败（sha256 不符）：%s" % self.name)
        os.replace(tmp, dest)
        return n, self.sha

    def lines(self) -> Iterator[dict]:
        """逐行 yield 解析后的 JSON 对象；读完核对 sha256。"""
        h = hashlib.sha256()
        buf = b""
        n = 0

        def _one(raw: bytes) -> dict:
            if not raw.strip():
                return {}
            if len(raw) > self.limit:
                raise ShareError("条目 %s 有一行超过 %d MB 上限，拒绝导入"
                                 % (self.name, self.limit // (1024 ** 2)))
            try:
                obj = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, ValueError) as e:
                raise ShareError("条目 %s 第 %d 行不是合法 JSON：%s" % (self.name, n, e))
            if not isinstance(obj, dict):
                raise ShareError("条目 %s 第 %d 行不是 JSON 对象" % (self.name, n))
            return obj

        with self._open() as src:
            while True:
                blk = src.read(1 << 20)
                if not blk:
                    break
                h.update(blk)
                buf += blk
                while True:
                    idx = buf.find(b"\n")
                    if idx < 0:
                        break
                    line, buf = buf[:idx], buf[idx + 1:]
                    n += 1
                    obj = _one(line)
                    if obj:
                        yield obj
            if buf.strip():
                n += 1
                obj = _one(buf)
                if obj:
                    yield obj
        self.sha = h.hexdigest()
        if self.want and self.sha != self.want:
            raise ShareError("包内文件校验失败（sha256 不符）：%s\n"
                             "包可能没拷完整或被改过，请让导出方重新导出。" % self.name)


def _manifest_sha(manifest: dict, name: str) -> str:
    for f in (manifest.get("files") or []):
        if isinstance(f, dict) and f.get("name") == name:
            return str(f.get("sha256") or "")
    return ""


# ---------------------------------------------------------------- 只读：看包

def read_manifest(zip_path: Path | str) -> dict:
    """只读包的 manifest（含安全扫描）。不做数据库相关的校验。"""
    p = Path(zip_path)
    if not p.is_file():
        raise ShareError("找不到分享包：%s" % p)
    try:
        with zipfile.ZipFile(p) as zf:
            _scan_zip(zf)
            if MANIFEST not in {i.filename for i in zf.infolist()}:
                raise ShareError("这不是分享包（缺 manifest.json）：%s" % p)
            raw = zf.read(MANIFEST)
    except zipfile.BadZipFile as e:
        raise ShareError("不是有效的 zip 文件：%s（%s）" % (p, e))
    try:
        m = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as e:
        raise ShareError("manifest.json 不是合法 JSON：%s" % e)
    if not isinstance(m, dict):
        raise ShareError("manifest.json 顶层不是 JSON 对象")
    return m


def inspect_package(zip_path: Path | str, *, deep: bool = True) -> dict:
    """给界面/CLI 看的"包里有什么"：结构校验 + 计数，**不写任何库**。

    `deep=True` 时把 jsonl 完整读一遍（含 sha256 核对与字段校验），
    这样"能不能导入"在真正导入之前就有答案。
    """
    p = Path(zip_path)
    if not p.is_file():
        raise ShareError("找不到分享包：%s" % p)
    out: dict = {"zip": str(p), "bytes": p.stat().st_size, "warnings": []}
    try:
        zf = zipfile.ZipFile(p)
    except zipfile.BadZipFile as e:
        raise ShareError("不是有效的 zip 文件：%s（%s）" % (p, e))
    with zf:
        members = _scan_zip(zf)
        for req in REQUIRED_ENTRIES:
            if req not in members:
                raise ShareError("这不是分享包（缺必需条目 %s）：%s" % (req, p))
        manifest = json.loads(_Entry(zf, MANIFEST).text())
        out["manifest"] = manifest
        out["format_ok"] = _check_manifest(manifest)
        doc = json.loads(_Entry(zf, DOC, _manifest_sha(manifest, DOC)).text())
        out["doc"] = _check_doc(doc)
        out["entries"] = sorted(members)
        out["n_entries"] = len(members)
        if not deep:
            return out
        n_chunk = _validate_chunks(zf, manifest)
        out["counts"] = dict(n_chunk)
        out["embeddings"] = _validate_embeddings(zf, manifest)
        out["knowledge"] = _check_knowledge(zf, manifest, members)
        out["includes"] = {
            "original": any(m.startswith(ORIG_DIR + "/") for m in members),
            "pages": sum(1 for m in members if m.startswith(PAGE_DIR + "/")),
        }
    return out


# ---------------------------------------------------------------- 导出

def _put_text(zf: zipfile.ZipFile, name: str, text: str,
              compress: int = zipfile.ZIP_DEFLATED) -> tuple[int, str]:
    return _put_stream(zf, name, [text.encode("utf-8")], compress)


def _put_stream(zf: zipfile.ZipFile, name: str, blocks,
                compress: int = zipfile.ZIP_DEFLATED) -> tuple[int, str]:
    """流式写一个 zip 条目，同时算 sha256。返回 (字节数, sha256)。"""
    h = hashlib.sha256()
    n = 0
    zi = zipfile.ZipInfo(name, date_time=time.localtime()[:6])
    zi.compress_type = compress
    zi.external_attr = 0o644 << 16
    with zf.open(zi, "w") as dst:
        for b in blocks:
            if isinstance(b, str):
                b = b.encode("utf-8")
            dst.write(b)
            h.update(b)
            n += len(b)
    return n, h.hexdigest()


def _iter_chunk_lines(conn: sqlite3.Connection, doc_id: int) -> Iterator[str]:
    cur = conn.execute(
        "SELECT id,ord,text,heading_path,topic,source_ref,char_len,token_est,"
        "content_hash,created_at FROM chunks WHERE doc_id=? ORDER BY ord, id", (doc_id,))
    while True:
        rows = cur.fetchmany(200)
        if not rows:
            break
        for r in rows:
            yield json.dumps({
                "id": int(r["id"]), "ord": int(r["ord"]), "text": r["text"],
                "heading_path": r["heading_path"], "topic": r["topic"],
                "source_ref": r["source_ref"], "char_len": r["char_len"],
                "token_est": r["token_est"], "content_hash": r["content_hash"],
                "created_at": r["created_at"],
            }, ensure_ascii=False) + "\n"


def _iter_embedding_lines(conn: sqlite3.Connection, doc_id: int) -> Iterator[str]:
    cur = conn.execute(
        "SELECT c.ord AS ord, c.id AS src_id, e.model AS model, e.dim AS dim, "
        "e.vec AS vec FROM chunks c JOIN embeddings e ON e.chunk_id=c.id "
        "WHERE c.doc_id=? ORDER BY c.ord, c.id", (doc_id,))
    while True:
        rows = cur.fetchmany(100)
        if not rows:
            break
        for r in rows:
            yield json.dumps({
                "ord": int(r["ord"]), "src_id": int(r["src_id"]),
                "model": r["model"], "dim": int(r["dim"]),
                "vec_b64": base64.b64encode(r["vec"]).decode("ascii"),
            }, ensure_ascii=False) + "\n"


def _iter_figure_lines(conn: sqlite3.Connection, doc_id: int) -> Iterator[str]:
    cur = conn.execute(
        "SELECT page,page_title,image_hash,model,thinking,tokens_in,tokens_out,"
        "cost_cny,n_chunks,created_at,description,no_info FROM figure_desc "
        "WHERE doc_id=? ORDER BY page", (doc_id,))
    for r in cur.fetchall():
        yield json.dumps({k: r[k] for k in r.keys()}, ensure_ascii=False) + "\n"


def _readme(manifest: dict, doc: dict) -> str:
    src = manifest.get("source") or {}
    emb = manifest.get("embedding") or {}
    inc = manifest.get("includes") or {}
    cnt = manifest.get("counts") or {}
    lines = [
        "课件分享包（study_agent share package）",
        "=" * 40,
        "",
        "这是什么：",
        "  某位用户在本地知识库里已经做好的一份课件，被打包成这个文件。",
        "  里面装的是**已经抽取、切块、向量化、并且做过 AI 知识点总结**的成果。",
        "  导入方**不需要重新调用任何 AI 接口、也不需要重新嵌入**，",
        "  就能得到同样的库内容、同样的检索能力、同样的知识点总结。",
        "",
        "包里的东西：",
        "  文档      ：%s（%s）" % (src.get("title") or "", src.get("kind") or ""),
        "  来源学科  ：%s" % (src.get("subject") or "未知"),
        "  文本块    ：%d 块" % int(cnt.get("chunks") or 0),
        "  向量      ：%d 条（模型 %s，维度 %s）"
        % (int(cnt.get("embeddings") or 0), emb.get("model"), emb.get("dim")),
        "  识图描述  ：%d 页" % int(cnt.get("figure_desc") or 0),
        "  知识点产物：%d 个文件" % len(manifest.get("knowledge") or []),
        "  原课件    ：%s" % ("有（original/ 目录）" if inc.get("original") else "未包含"),
        "  页图      ：%s" % ("有（pages/ 目录）" if inc.get("pages") else "未包含"),
        "",
        "怎么导入：",
        "  本机知识库（G:\\study_agent）下执行：",
        "    python tools\\share.py inspect --zip \"<这个文件>\"    先看看包里有什么",
        "    python tools\\share.py import  --zip \"<这个文件>\"    导入当前学科",
        "  也可以在知识库看板里点「导入分享包」。",
        "",
        "导入时会发生什么：",
        "  · 校验包结构、校验每个文件的 sha256、**校验向量模型与本机一致**；",
        "  · 分配一个新的 doc_id（绝不覆盖本机已有资料），写入 documents/chunks/",
        "    embeddings/figure_desc 并维护全文索引；",
        "  · 知识点产物按新的 doc_id 改名（文件名与文件内 meta.doc_id 同时改）；",
        "  · 全程**零次模型调用、零花费**。",
        "",
        "如果导入被拒绝：",
        "  · 提示\"向量模型不一致\"⇒ 让导出方用与你相同的嵌入模型重新导出，",
        "    或在本机重新入库原课件（那需要本机嵌入模型，不产生接口费用）；",
        "  · 提示\"包已损坏\"⇒ 文件没拷完整，重新传一次。",
        "",
        "导出信息：",
        "  导出时间：%s" % manifest.get("created_at"),
        "  导出者  ：%s" % (manifest.get("exported_by") or "未署名"),
        "  工具    ：%s（格式版本 %s）"
        % (manifest.get("tool"), manifest.get("format_version")),
        "  备注    ：%s" % (manifest.get("note") or "无"),
        "",
    ]
    return "\n".join(lines)


def export_doc(doc_id: int, out_path: Path | str | None = None, *,
               db_path: Path | str | None = None, with_original: bool = False,
               with_pages: bool = False, subject: str = "", exported_by: str = "",
               note: str = "", log_dir: Path | str | None = None,
               raw_dir: Path | str | None = None, root: Path | str | None = None,
               progress: Progress | None = None) -> dict:
    """把一份课件导出成分享包。返回导出报告（含包路径、计数、体积）。

    默认 `with_original=False, with_pages=False`（大管家 2026 拍板）：
    包最小的组合才是"摊平 API 费用"的主路径——块与向量已足够检索与问答，
    原文件只是"万一想重建"的保险，页图只是"接收方没装 Office 时想看原图"的加分项。
    两个开关都由调用方（CLI / C-0080 界面）勾选。
    """
    root = Path(root if root is not None else config.ROOT)
    log_dir = Path(log_dir if log_dir is not None else config.LOG_DIR)
    raw_dir = Path(raw_dir if raw_dir is not None else config.RAW_DIR)
    dbp = Path(db_path if db_path is not None else config.DB_PATH)

    conn = db.connect(dbp)
    try:
        row = conn.execute("SELECT * FROM documents WHERE id=?", (int(doc_id),)).fetchone()
        if row is None:
            raise ShareError("库里没有 id=%d 的课件（可能已被删除）" % int(doc_id))
        doc = dict(row)
        title = (doc.get("title") or "").strip() or Path(doc["path"]).stem
        base = clean_name(title)
        if out_path is None:
            out_path = root / SHARE_DIRNAME / ("%s-share.zip" % base)
        out = Path(out_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_name(out.name + ".part")

        warnings: list[str] = []
        counts = {
            "chunks": conn.execute("SELECT COUNT(*) FROM chunks WHERE doc_id=?",
                                   (doc_id,)).fetchone()[0],
            "embeddings": conn.execute(
                "SELECT COUNT(*) FROM chunks c JOIN embeddings e ON e.chunk_id=c.id "
                "WHERE c.doc_id=?", (doc_id,)).fetchone()[0],
            "figure_desc": conn.execute("SELECT COUNT(*) FROM figure_desc WHERE doc_id=?",
                                        (doc_id,)).fetchone()[0],
        }
        emb = conn.execute(
            "SELECT model, dim, COUNT(*) c FROM chunks c JOIN embeddings e "
            "ON e.chunk_id=c.id WHERE c.doc_id=? GROUP BY model, dim "
            "ORDER BY c DESC LIMIT 1", (doc_id,)).fetchone()
        if emb is None:
            # 没有向量也能导（对方拿到的包检索不了，只能看文本），但必须明说
            warnings.append("这份课件在本库里没有向量（embeddings 0 行），"
                            "包导入后无法做向量检索")
            model, dim = str(getattr(config, "EMBED_MODEL_REPO", "")), int(config.EMBED_DIM)
        else:
            model, dim = str(emb["model"]), int(emb["dim"])
        if counts["embeddings"] != counts["chunks"]:
            warnings.append("块数与向量数不等（%d 块 / %d 向量），"
                            "包仍可用，但缺失向量的块只能走关键词检索"
                            % (counts["chunks"], counts["embeddings"]))

        # ---- 原文件 ----
        src_file = Path(str(doc.get("abs_path") or ""))
        if not src_file.is_file():
            alt = root / str(doc["path"])
            if alt.is_file():
                src_file = alt
        orig_info: dict | None = None
        if with_original:
            if not src_file.is_file():
                warnings.append("勾选了「带原课件文件」，但本机找不到源文件（%s），"
                                "已降级为不带原文件" % (doc.get("abs_path") or doc.get("path")))
            else:
                orig_info = {"name": src_file.name, "bytes": src_file.stat().st_size,
                             "sha256": sha256_file(src_file), "entry": None}

        # ---- 识图页图 ----
        # 目录名由**源库**的文件名 stem 决定（与 figures 写图时同一个规则）
        page_files: list[tuple[Path, str]] = []
        pages_bytes = 0
        if with_pages:
            src_stem = Path(src_file.name).stem if src_file.is_file() else Path(doc["path"]).stem
            from . import figure_cache
            pdir = figure_cache.directory(log_dir, doc_id, doc.get("content_hash"))
            if pdir is None or not pdir.is_dir():
                if figure_cache.document_directories(log_dir, doc_id):
                    pdir = None  # 版本变更时不能悄悄导出旧图
                else:
                    pdir = log_dir / "figure_desc" / safe_dirname(src_stem)
            if pdir is not None and pdir.is_dir():
                for f in sorted(pdir.glob("slide_*.png")):
                    page_files.append((f, "%s/%s/%s" % (PAGE_DIR, safe_dirname(src_stem), f.name)))
                    pages_bytes += f.stat().st_size
            if not page_files:
                warnings.append("勾选了「带识图页图」，但本机没有渲染好的页图（%s），"
                                "已降级为不带页图" % pdir)

        # ---- 知识点产物 ----
        know_items: list[dict] = []
        for suffix in KNOW_SUFFIXES:
            kf = log_dir / "knowledge" / ("%d%s" % (int(doc_id), suffix))
            if not kf.is_file():
                continue
            item = {"name": kf.name, "entry": "%s/%s" % (KNOW_DIR, kf.name),
                    "source_doc_id": int(doc_id), "suffix": suffix,
                    "bytes": kf.stat().st_size, "sha256": sha256_file(kf)}
            try:
                obj = json.loads(kf.read_text(encoding="utf-8"))
                meta = obj.get("meta") if isinstance(obj, dict) else None
                meta_doc_id = (meta or {}).get("doc_id") if isinstance(meta, dict) else None
                item["meta_doc_id"] = int(meta_doc_id) if meta_doc_id is not None else None
                if item["meta_doc_id"] != int(doc_id):
                    warnings.append(
                        "产物 %s 里 meta.doc_id=%s 与文件名里的 doc_id=%d 不一致"
                        "（源库自身的问题）；导入时会统一改写成接收方的新 doc_id"
                        % (kf.name, meta_doc_id, int(doc_id)))
                if suffix == KP_SUFFIX:
                    kps = obj.get("knowledge_points") if isinstance(obj, dict) else None
                    item["n_points"] = len(kps) if isinstance(kps, list) else 0
                elif suffix == TERMS_SUFFIX:
                    ts = obj.get("terms") if isinstance(obj, dict) else None
                    item["n_points"] = len(ts) if isinstance(ts, list) else 0
                elif suffix == PREVIEW_SUFFIX:
                    # C-0094 预习总结：条目 = 节级细拆的节（每节带 key_points /
                    # chunk_ids / material_block_ids）。按**本文件里真实有的节数**数，
                    # 与上面两条同口径（不依赖产物自己声明的 meta.n_points）。
                    psecs = obj.get("sections") if isinstance(obj, dict) else None
                    item["n_points"] = len(psecs) if isinstance(psecs, list) else 0
                elif suffix == SECTIONS_SUFFIX:
                    # C-0092 章节细目：条目 = 全书抽出来的节（chapters[].sections）。
                    chs = obj.get("chapters") if isinstance(obj, dict) else None
                    n_sec = 0
                    if isinstance(chs, list):
                        for ch in chs:
                            sub = ch.get("sections") if isinstance(ch, dict) else None
                            if isinstance(sub, list):
                                n_sec += len(sub)
                    item["n_points"] = n_sec
            except (OSError, ValueError) as e:
                warnings.append("产物 %s 读不出 JSON（%s），仍原样打包" % (kf.name, e))
            know_items.append(item)

        _ipc(progress, "export", 0, counts["chunks"])
        files: list[dict] = []
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
            n, h = _put_stream(zf, CHUNKS, _iter_chunk_lines(conn, doc_id))
            files.append({"name": CHUNKS, "bytes": n, "sha256": h})
            n, h = _put_stream(zf, EMBEDDINGS, _iter_embedding_lines(conn, doc_id))
            files.append({"name": EMBEDDINGS, "bytes": n, "sha256": h})
            n, h = _put_stream(zf, FIGURES, _iter_figure_lines(conn, doc_id))
            files.append({"name": FIGURES, "bytes": n, "sha256": h})

            for item in know_items:
                kf = log_dir / "knowledge" / item["name"]
                n, h = _put_stream(zf, item["entry"], _file_blocks(kf))
                files.append({"name": item["entry"], "bytes": n, "sha256": h})
                item["bytes"], item["sha256"] = n, h

            if orig_info is not None:
                n, h = _put_stream(zf, "%s/%s" % (ORIG_DIR, orig_info["name"]),
                                   _file_blocks(src_file))
                orig_info.update({"entry": "%s/%s" % (ORIG_DIR, orig_info["name"]),
                                  "bytes": n, "sha256": h})
                files.append({"name": orig_info["entry"], "bytes": n, "sha256": h})

            n_pages = 0
            pg_bytes = 0
            for f, entry in page_files:
                # png 已经是压缩数据，再 deflate 只费 CPU 不省体积
                n, h = _put_stream(zf, entry, _file_blocks(f), zipfile.ZIP_STORED)
                files.append({"name": entry, "bytes": n, "sha256": h})
                n_pages += 1
                pg_bytes += n
            _ipc(progress, "export", counts["chunks"], counts["chunks"])

            # doc.json：documents 行去掉本机字段
            doc_out = {
                "title": title, "kind": doc.get("kind"), "file_name": None,
                "content_hash": doc.get("content_hash"),
                "size_bytes": doc.get("size_bytes"), "mtime": doc.get("mtime"),
                "n_chunks": doc.get("n_chunks"), "n_chars": doc.get("n_chars"),
                "status": doc.get("status"), "role": doc.get("role"),
                "corpus": doc.get("corpus"), "warnings": doc.get("warnings"),
                "extra": doc.get("extra"), "indexed_at": doc.get("indexed_at"),
                "source_path": doc.get("path"),
                "source_abs_name": Path(str(doc.get("abs_path") or doc.get("path"))).name,
            }
            doc_out["file_name"] = doc_out["source_abs_name"] or (
                "%s.%s" % (base, doc.get("kind") or "bin"))
            n, h = _put_text(zf, DOC, json.dumps(doc_out, ensure_ascii=False, indent=2))
            files.append({"name": DOC, "bytes": n, "sha256": h})

            manifest = {
                "format": FORMAT,
                "format_version": FORMAT_VERSION,
                "tool": "study_agent core/share.py",
                "created_at": _iso(_now()),
                "created_at_ts": _now(),
                "exported_by": exported_by or "",
                "note": note or "",
                "source": {
                    "subject": subject or _subject_of(log_dir, root),
                    "db": rel_to(dbp, root),
                    "root_name": root.name,
                    "doc_id": int(doc_id),
                    "title": title,
                    "path": doc.get("path"),
                    "kind": doc.get("kind"),
                    "content_hash": doc.get("content_hash"),
                    "n_chunks": doc.get("n_chunks"),
                    "indexed_at": doc.get("indexed_at"),
                },
                "embedding": {"model": model, "dim": int(dim)},
                "includes": {"original": orig_info is not None,
                             "pages": n_pages > 0},
                "original": orig_info,
                "pages": ({"dir": "%s/%s" % (PAGE_DIR, safe_dirname(
                    Path(src_file.name).stem if src_file.is_file()
                    else Path(doc["path"]).stem)),
                    "n": n_pages, "bytes": pg_bytes} if n_pages else None),
                "knowledge": [{k: v for k, v in it.items() if k != "suffix"}
                              for it in know_items],
                "counts": {"chunks": counts["chunks"], "embeddings": counts["embeddings"],
                           "figure_desc": counts["figure_desc"], "pages": n_pages},
                "files": files,
                "entries_sha256": hashlib.sha256(
                    "\n".join("%s:%s" % (f["name"], f["sha256"])
                              for f in sorted(files, key=lambda x: x["name"]))
                    .encode("utf-8")).hexdigest(),
                "writes_required": {
                    "documents": 1, "chunks": counts["chunks"],
                    "embeddings": counts["embeddings"],
                    "figure_desc": counts["figure_desc"],
                    "knowledge_files": len(know_items),
                    "pages": n_pages,
                },
                "no_model_calls_on_import": True,
                "warnings": warnings,
            }
            n, h = _put_text(zf, MANIFEST,
                             json.dumps(manifest, ensure_ascii=False, indent=2))
            files.append({"name": MANIFEST, "bytes": n, "sha256": h})
            n, h = _put_text(zf, READ_ME, _readme(manifest, doc_out))
            files.append({"name": READ_ME, "bytes": n, "sha256": h})

        os.replace(tmp, out)
    finally:
        conn.close()

    size = out.stat().st_size
    _ipc(progress, "export", 1, 1)
    return {
        "status": "exported", "zip": str(out), "bytes": size,
        "counts": counts, "includes": manifest["includes"],
        "embedding": manifest["embedding"], "warnings": warnings,
        "manifest": manifest, "n_pages": manifest["counts"]["pages"],
        "original_bytes": (orig_info or {}).get("bytes", 0),
        "pages_bytes": (manifest.get("pages") or {}).get("bytes", 0),
    }


def _file_blocks(path: Path, block: int = 1 << 20) -> Iterator[bytes]:
    with open(path, "rb") as f:
        while True:
            b = f.read(block)
            if not b:
                break
            yield b


# ---------------------------------------------------------------- 校验（结构）

def _check_manifest(m: dict) -> dict:
    """manifest 必需字段/类型校验。返回给报告用的摘要。"""
    if not isinstance(m, dict):
        raise ShareError("manifest.json 顶层不是 JSON 对象")
    if m.get("format") != FORMAT:
        raise ShareError("这不是 study_agent 分享包（manifest.format=%r）"
                         % (m.get("format"),))
    ver = m.get("format_version")
    if not isinstance(ver, int) or ver < 1:
        raise ShareError("manifest.format_version 非法：%r" % (ver,))
    if ver > FORMAT_VERSION:
        raise ShareError("包的格式版本 %d 高于本程序支持的 %d，请升级后再导入"
                         % (ver, FORMAT_VERSION))
    src = m.get("source")
    if not isinstance(src, dict) or not isinstance(src.get("doc_id"), int):
        raise ShareError("manifest.source.doc_id 缺失或不是整数")
    emb = m.get("embedding")
    if not isinstance(emb, dict) or not emb.get("model") or not isinstance(emb.get("dim"), int):
        raise ShareError("manifest.embedding 缺失 model/dim，无法校验向量兼容性")
    if not isinstance(m.get("counts"), dict):
        raise ShareError("manifest.counts 缺失或类型不符")
    if not isinstance(m.get("files"), list):
        raise ShareError("manifest.files 缺失或类型不符（无法核对文件完整性）")
    return {"format_version": ver, "source": src, "embedding": emb,
            "counts": m["counts"], "includes": m.get("includes") or {}}


def _check_doc(doc: dict) -> dict:
    """doc.json 必需字段/类型校验。"""
    if not isinstance(doc, dict):
        raise ShareError("doc.json 顶层不是 JSON 对象")
    need = ("title", "kind", "content_hash")
    for k in need:
        v = doc.get(k)
        if not isinstance(v, str) or not v.strip():
            raise ShareError("doc.json 缺字段或类型不符：%s（%r）" % (k, v))
    if not isinstance(doc.get("file_name"), str) or not doc.get("file_name").strip():
        raise ShareError("doc.json 缺 file_name（接收方要靠它决定 raw\\ 下的文件名）")
    return doc


def _validate_chunks(zf: zipfile.ZipFile, manifest: dict) -> dict:
    """流式校验 chunks.jsonl：字段、计数、正文块 ord 连续。"""
    ent = _Entry(zf, CHUNKS, _manifest_sha(manifest, CHUNKS))
    n = 0
    body_ords: list[int] = []
    seen_ord: dict[int, int] = {}
    bad: list[str] = []
    for i, obj in enumerate(ent.lines(), 1):
        if not isinstance(obj.get("ord"), int):
            bad.append("第 %d 行缺 ord（或不是整数）" % i)
            continue
        if not isinstance(obj.get("text"), str):
            bad.append("第 %d 行缺 text（或不是字符串）" % i)
            continue
        o = int(obj["ord"])
        seen_ord[o] = seen_ord.get(o, 0) + 1
        if o < FIG_ORD_BASE:
            body_ords.append(o)
        n += 1
        if len(bad) > 5:
            break
    if bad:
        raise ShareError("chunks.jsonl 结构校验失败：\n  " + "\n  ".join(bad[:5]))
    want = manifest.get("counts", {}).get("chunks")
    if isinstance(want, int) and want != n:
        raise ShareError("chunks.jsonl 行数 %d 与 manifest 声明的 %d 不符，包不完整" % (n, want))
    body_sorted = sorted(body_ords)
    gaps = [i for i in range(len(body_sorted)) if body_sorted[i] != i]
    out = {"total": n, "body": len(body_ords),
           "body_ord_ok": not gaps and len(set(body_sorted)) == len(body_sorted),
           "max_ord": max(seen_ord) if seen_ord else -1}
    if gaps:
        out["body_ord_gap_at"] = body_sorted[gaps[0]] if gaps else None
    return out


def _validate_embeddings(zf: zipfile.ZipFile, manifest: dict) -> dict:
    """流式校验 embeddings.jsonl：模型/维度/向量长度/行数。"""
    ent = _Entry(zf, EMBEDDINGS, _manifest_sha(manifest, EMBEDDINGS))
    emb = manifest["embedding"]
    model, dim = str(emb["model"]), int(emb["dim"])
    n = 0
    for i, obj in enumerate(ent.lines(), 1):
        if str(obj.get("model")) != model or int(obj.get("dim") or 0) != dim:
            raise ShareError("embeddings.jsonl 第 %d 行的模型/维度与 manifest 不符" % i)
        v = obj.get("vec_b64")
        if not isinstance(v, str):
            raise ShareError("embeddings.jsonl 第 %d 行缺 vec_b64" % i)
        try:
            blob = base64.b64decode(v, validate=True)
        except (binascii.Error, ValueError) as e:
            raise ShareError("embeddings.jsonl 第 %d 行 vec_b64 不是合法 base64：%s" % (i, e))
        if len(blob) != dim * 4:
            raise ShareError("embeddings.jsonl 第 %d 行向量长 %d 字节，与 dim=%d（应为 %d）不符"
                             % (i, len(blob), dim, dim * 4))
        n += 1
    want = manifest.get("counts", {}).get("embeddings")
    if isinstance(want, int) and want != n:
        raise ShareError("embeddings.jsonl 行数 %d 与 manifest 声明的 %d 不符" % (n, want))
    return {"total": n, "model": model, "dim": dim}


def _check_knowledge(zf: zipfile.ZipFile, manifest: dict,
                     members: dict) -> list[dict]:
    """校验知识点产物：文件名里的 doc_id / 文件内 meta.doc_id / manifest.source.doc_id 三者一致。"""
    src_doc_id = int(manifest["source"]["doc_id"])
    out: list[dict] = []
    for item in (manifest.get("knowledge") or []):
        if not isinstance(item, dict) or not item.get("entry"):
            raise ShareError("manifest.knowledge 里有条目缺 entry")
        entry = str(item["entry"])
        if entry not in members:
            raise ShareError("manifest 声明了知识点产物 %s，但包里没有这个文件" % entry)
        name = Path(entry).name
        m = re.match(r"^(\d+)(-.+\.json)$", name)
        if not m:
            raise ShareError("知识点产物文件名不符合 <doc_id>-<名称>.json：%s" % name)
        file_doc_id = int(m.group(1))
        if file_doc_id != src_doc_id:
            raise ShareError("知识点产物 %s 文件名里的 doc_id=%d 与包声明的源 doc_id=%d 不一致，"
                             "拒绝导入（导入方无法判断它属于哪份课件）"
                             % (name, file_doc_id, src_doc_id))
        obj = json.loads(_Entry(zf, entry, _manifest_sha(manifest, entry)).text())
        if not isinstance(obj, dict) or not isinstance(obj.get("meta"), dict):
            raise ShareError("知识点产物 %s 缺 meta 对象" % name)
        meta_doc_id = obj["meta"].get("doc_id")
        if meta_doc_id != src_doc_id:
            raise ShareError("知识点产物 %s 内 meta.doc_id=%r 与文件名/doc_id（%d）不一致，"
                             "拒绝导入（只改一处会让看板关联不上）"
                             % (name, meta_doc_id, src_doc_id))
        out.append({"entry": entry, "name": name, "suffix": m.group(2),
                    "source_doc_id": src_doc_id, "obj": obj,
                    "n_points": len(obj.get("knowledge_points") or obj.get("terms") or [])})
    return out


# ---------------------------------------------------------------- 知识点产物重写

_CHUNK_KEYS = ("chunk_id", "chunk_ids", "material_block_ids", "block_ids")


def _remap_ids(obj, id_map: dict[int, int], stats: dict):
    """把产物里所有块 id 引用按 源id→新id 重映射。

    - 列表（`chunk_ids` / `material_block_ids`）：映射不到的**移除**；
    - 标量（`chunk_id`）：映射不到置 `None`（同一条记录里的 page/ref 等仍有价值）。
    同时把任意层级的 `doc_id` 改成新 doc_id（一份产物只属于一份课件，不会误伤）。
    """
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k == "doc_id" and isinstance(v, int):
                out[k] = int(id_map[_SELF_DOC]) if _SELF_DOC in id_map else v
                continue
            if k in _CHUNK_KEYS:
                if isinstance(v, list):
                    keep = []
                    for x in v:
                        if isinstance(x, int):
                            nv = id_map.get(x)
                            if nv is None:
                                stats["dropped"] += 1
                                continue
                            stats["remapped"] += 1
                            keep.append(nv)
                        else:
                            keep.append(x)
                    out[k] = keep
                elif isinstance(v, int):
                    nv = id_map.get(v)
                    if nv is None:
                        stats["dropped"] += 1
                        out[k] = None
                    else:
                        stats["remapped"] += 1
                        out[k] = nv
                else:
                    out[k] = v
                continue
            out[k] = _remap_ids(v, id_map, stats)
        return out
    if isinstance(obj, list):
        return [_remap_ids(x, id_map, stats) for x in obj]
    return obj


# 哨兵键：`_remap_ids` 里用它把"这份产物属于哪个 doc"塞进 id_map
_SELF_DOC = -1


def rewrite_knowledge(obj: dict, new_doc_id: int, id_map: dict[int, int]) -> tuple[dict, dict]:
    """改写成接收方口径：meta.doc_id / 每条 doc_id + 全部块 id 引用。"""
    m = dict(id_map)
    m[_SELF_DOC] = int(new_doc_id)
    stats = {"remapped": 0, "dropped": 0}
    out = _remap_ids(obj, m, stats)
    # 兜底：万一产物里 doc_id 不是 int（如字符串），这里强制统一
    if isinstance(out.get("meta"), dict):
        out["meta"]["doc_id"] = int(new_doc_id)
    if isinstance(out.get("knowledge_points"), list):
        for kp in out["knowledge_points"]:
            if isinstance(kp, dict):
                kp["doc_id"] = int(new_doc_id)
    return out, stats


# ---------------------------------------------------------------- 导入

def _is_triggered(conn: sqlite3.Connection) -> bool:
    row = conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='trigger' "
                       "AND name='chunks_ai'").fetchone()
    return bool(row and row[0])


def _ensure_fts(conn: sqlite3.Connection, doc_id: int) -> dict:
    """保证新导入的块能被全文索引命中。

    正常路径下 `chunks_ai` 触发器会在 INSERT 时自动写索引；这里额外做两件事：
    ① 触发器缺失（老库）就地 rebuild 一次；② **探测**新块的文本能否被 MATCH 命中，
    把"检索能不能命中"变成一条可验证的证据而不是假设。
    """
    has_fts = conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' "
                           "AND name='chunks_fts'").fetchone()[0]
    if not has_fts:
        return {"fts": False, "reason": "本库没有 chunks_fts（SQLite 无 FTS5），"
                                        "检索会自动降级为 LIKE"}
    rebuilt = False
    if not _is_triggered(conn):
        conn.execute("INSERT INTO chunks_fts(chunks_fts) VALUES('rebuild')")
        rebuilt = True
    probe = ""
    hit = 0
    for i in range(3):
        row = conn.execute(
            "SELECT text FROM chunks WHERE doc_id=? AND LENGTH(text)>=12 LIMIT 1 OFFSET ?",
            (doc_id, i)).fetchone()
        if row is None:
            break
        s = re.sub(r'["\'\s]', "", str(row["text"]))[:12]
        if len(s) < 3:
            continue
        probe = s
        try:
            hit = conn.execute("SELECT COUNT(*) FROM chunks_fts WHERE chunks_fts MATCH ?",
                               ('"%s"' % s,)).fetchone()[0]
        except sqlite3.OperationalError:
            hit = 0
        if hit:
            break
    if not hit and not rebuilt:
        conn.execute("INSERT INTO chunks_fts(chunks_fts) VALUES('rebuild')")
        rebuilt = True
        if probe:
            try:
                hit = conn.execute("SELECT COUNT(*) FROM chunks_fts WHERE chunks_fts MATCH ?",
                                   ('"%s"' % probe,)).fetchone()[0]
            except sqlite3.OperationalError:
                hit = 0
    return {"fts": True, "ok": bool(hit), "rebuilt": rebuilt,
            "probe": probe, "hits": int(hit)}


def local_embedding_identity(conn: sqlite3.Connection, model: str | None = None,
                             dim: int | None = None) -> tuple[str, int]:
    """本库当前嵌入模型：库里已有向量就按**库里实际用的**算（多数票），
    空库才回落到 config。

    为什么以库为准：检索时新块与老块要放在同一个向量空间里比较，
    老块用的是哪个模型，新块就必须用哪个——config 只是"还没有任何向量时"的默认。
    """
    row = conn.execute("SELECT model, dim, COUNT(*) c FROM embeddings "
                       "GROUP BY model, dim ORDER BY c DESC LIMIT 1").fetchone()
    if row is not None:
        return str(row["model"]), int(row["dim"])
    return (str(model or getattr(config, "EMBED_MODEL_REPO", "")),
            int(dim or config.EMBED_DIM))


def _unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    for i in range(2, 500):
        cand = path.with_name("%s-%d%s" % (path.stem, i, path.suffix))
        if not cand.exists():
            return cand
    raise ShareError("同名文件太多，无法为 %s 找到可用文件名" % path.name)


def _unique_rel(conn: sqlite3.Connection, rel: str) -> str:
    if not conn.execute("SELECT 1 FROM documents WHERE path=?", (rel,)).fetchone():
        return rel
    p = Path(rel)
    for i in range(2, 500):
        cand = str(p.with_name("%s-%d%s" % (p.stem, i, p.suffix)))
        if not conn.execute("SELECT 1 FROM documents WHERE path=?", (cand,)).fetchone():
            return cand
    raise ShareError("path 冲突太多，无法为 %s 找到可用值" % rel)


def import_package(zip_path: Path | str, *, db_path: Path | str | None = None,
                   log_dir: Path | str | None = None, raw_dir: Path | str | None = None,
                   root: Path | str | None = None, embed_model: str | None = None,
                   embed_dim: int | None = None, duplicate: bool = False,
                   dry_run: bool = False, progress: Progress | None = None) -> dict:
    """把一个分享包导入当前库。**全程零模型调用、零费用。**

    `duplicate=False`（默认）时，若本库已有相同 `content_hash` 的课件 → 不重复导入，
    返回 `status="skipped"` 与已存在的 doc_id（卡面的"冲突处理"）。
    想强行再导一份（用于对比/多来源），传 `duplicate=True`。
    """
    root = Path(root if root is not None else config.ROOT)
    log_dir = Path(log_dir if log_dir is not None else config.LOG_DIR)
    raw_dir = Path(raw_dir if raw_dir is not None else config.RAW_DIR)
    dbp = Path(db_path if db_path is not None else config.DB_PATH)
    zp = Path(zip_path)
    if not zp.is_file():
        raise ShareError("找不到分享包：%s" % zp)
    pkg_sha = sha256_file(zp)

    warnings: list[str] = []
    conn = db.connect(dbp)
    conn.isolation_level = None            # 手动事务：写库要么全成、要么全不成
    written: list[Path] = []
    made_dirs: list[Path] = []
    try:
        db.init_db(conn)                   # 幂等：确保 schema/FTS 就绪（不含任何新列）
        try:
            zf = zipfile.ZipFile(zp)
        except zipfile.BadZipFile as e:
            raise ShareError("不是有效的 zip 文件：%s（%s）" % (zp, e))
        with zf:
            members = _scan_zip(zf)
            for req in REQUIRED_ENTRIES:
                if req not in members:
                    raise ShareError("这不是分享包（缺必需条目 %s）：%s" % (req, zp))
            mtext = _Entry(zf, MANIFEST).text()
            try:
                manifest = json.loads(mtext)
            except ValueError as e:
                raise ShareError("manifest.json 不是合法 JSON：%s" % e)
            info = _check_manifest(manifest)
            doc_text = _Entry(zf, DOC, _manifest_sha(manifest, DOC)).text()
            try:
                doc = json.loads(doc_text)
            except ValueError as e:
                raise ShareError("doc.json 不是合法 JSON：%s" % e)
            _check_doc(doc)

            # ---- 1. 向量兼容性硬校验（最重要的一道闸）----
            lm, ld = local_embedding_identity(conn, embed_model, embed_dim)
            pm, pd = str(info["embedding"]["model"]), int(info["embedding"]["dim"])
            if (pm, pd) != (lm, ld):
                raise ShareError(
                    "向量模型与本库不一致，已拒绝导入（**不会**自动重算——重算要调用嵌入模型）。\n"
                    "  包里的向量：%s（dim=%d）\n  本库当前用：%s（dim=%d）\n"
                    "  后果：维度/模型不同的向量混在一张表里，检索会返回荒谬结果，比报错更坑。\n"
                    "怎么办：① 让导出方用 %s（dim=%d）重新导出；\n"
                    "        ② 或让导出方只分享原课件（勾选「带原课件文件」），"
                    "你在本机重新入库（用你自己的本地嵌入模型，不产生接口费用）。"
                    % (pm, pd, lm, ld, lm, ld))

            # ---- 2. 结构与完整性校验（整包过一遍，任何一处不符都不写库）----
            #
            # ⚠ 顺序：**先证明包是完整合法的，再谈"本库是不是已经有了"**。
            # 反过来（先查冲突）会让一个被改坏的包因为"本库已有同一份课件"而被
            # 悄悄 skipped——用户以为包是好的、转手分享，别人导入时才炸。
            # 校验在前 ⇒ 坏包在任何库里都给同一句"包有问题"。
            n_chunks = _validate_chunks(zf, manifest)
            n_emb = _validate_embeddings(zf, manifest)
            know = _check_knowledge(zf, manifest, members)
            n_fig = 0
            if FIGURES in members:
                for obj in _Entry(zf, FIGURES, _manifest_sha(manifest, FIGURES)).lines():
                    if not isinstance(obj.get("page"), int):
                        raise ShareError("figure_desc.jsonl 有行缺 page（或不是整数）")
                    n_fig += 1
                want_fig = manifest.get("counts", {}).get("figure_desc")
                if isinstance(want_fig, int) and want_fig != n_fig:
                    raise ShareError("figure_desc.jsonl 行数 %d 与 manifest 声明的 %d 不符"
                                     % (n_fig, want_fig))
            page_entries = sorted(m for m in members
                                  if m.startswith(PAGE_DIR + "/") and _SLIDE_RE.match(
                                      Path(m).name))
            orig_entry = str((manifest.get("original") or {}).get("entry") or "")
            if orig_entry and orig_entry not in members:
                raise ShareError("manifest 声明带原课件 %s，但包里没有它" % orig_entry)

            if not n_chunks["body_ord_ok"]:
                warnings.append("源库正文块的 ord 不是 0..n-1 连续（首个异常处 ord=%s），"
                                "已按原样导入" % n_chunks.get("body_ord_gap_at"))

            # ---- 3. 冲突：同一份课件（content_hash 相同）默认不重复导 ----
            hit = conn.execute("SELECT id,path,title FROM documents WHERE content_hash=? "
                               "ORDER BY id LIMIT 1", (doc["content_hash"],)).fetchone()
            conflict = ({"doc_id": int(hit["id"]), "existing_path": hit["path"],
                         "existing_title": hit["title"]} if hit is not None else None)

            if dry_run:
                return {
                    "status": "dry_run", "ok": True, "zip": str(zp),
                    "package_sha256": pkg_sha, "source": info["source"],
                    "title": doc["title"], "counts": info["counts"],
                    "knowledge": [{"file": k["name"], "n_points": k["n_points"]}
                                  for k in know],
                    "pages": len(page_entries), "original": orig_entry or None,
                    "embedding": {"model": pm, "dim": pd, "local": [lm, ld]},
                    "conflict": conflict,
                    "would_skip": bool(conflict) and not duplicate,
                    "model_calls": 0, "cost_cny": 0.0, "warnings": warnings,
                }

            if conflict is not None and not duplicate:
                return {
                    "status": "skipped", "reason": "本库已有同一份课件（content_hash 相同）",
                    **conflict, "package_sha256": pkg_sha,
                    "source": info["source"], "counts": info["counts"],
                    "model_calls": 0, "cost_cny": 0.0,
                    "hint": "如需再导入一份副本（例如对比不同来源），用 --duplicate",
                }

            # ---- 4. 落库 ----
            orig_name = (Path(str(orig_entry)).name if orig_entry
                         else str(doc.get("file_name") or "courseware.bin"))
            new_file = None
            if orig_entry:
                raw_dir.mkdir(parents=True, exist_ok=True)
                target = _unique_path(raw_dir / clean_name(orig_name, 120))
                n_b, _h = _Entry(zf, orig_entry, _manifest_sha(manifest, orig_entry)
                                 ).write_to(target)
                written.append(target)
                new_file = target
                if n_b != int(doc.get("size_bytes") or n_b):
                    warnings.append("原文件字节数 %d 与 doc.json 记录的 %d 不同"
                                    % (n_b, int(doc.get("size_bytes") or 0)))
                rel = _unique_rel(conn, rel_to(target, root))
                abs_path = str(target)
            else:
                stem = Path(str(doc.get("file_name") or doc["title"])).stem
                stem = clean_name(stem, 50)
                h8 = str(doc["content_hash"])[:8]
                rel = _unique_rel(conn, str(
                    Path(rel_to(raw_dir, root)) / PLACEHOLDER_DIR /
                    ("%s-%s%s" % (stem, h8, PLACEHOLDER_EXT))))
                abs_path = str(root / rel)
                warnings.append("包内不含原课件文件，documents.path 用占位路径（%s）："
                                "这是「来自分享包」的标记，不是本机真实文件" % rel)

            extra_new = {}
            try:
                old_extra = json.loads(doc.get("extra") or "{}")
                if isinstance(old_extra, dict):
                    extra_new.update(old_extra)
            except ValueError:
                pass
            extra_new["shared_from"] = {
                "format": FORMAT, "format_version": int(info["format_version"]),
                "package": zp.name, "package_path": str(zp),
                "package_sha256": pkg_sha,
                "exported_at": manifest.get("created_at"),
                "exported_by": manifest.get("exported_by") or "",
                "note": manifest.get("note") or "",
                "source_subject": info["source"].get("subject") or "",
                "source_db": info["source"].get("db") or "",
                "source_doc_id": int(info["source"]["doc_id"]),
                "source_path": info["source"].get("path"),
                "imported_at": _iso(_now()),
                "importer": "study_agent core/share.py",
            }
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "INSERT INTO documents(path,abs_path,kind,title,content_hash,size_bytes,"
                "mtime,n_chunks,n_chars,status,role,corpus,warnings,extra,indexed_at,"
                "updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (rel, abs_path, doc.get("kind") or "text", doc["title"],
                 doc["content_hash"], doc.get("size_bytes"), doc.get("mtime"),
                 doc.get("n_chunks"), doc.get("n_chars"),
                 doc.get("status") or "ok", doc.get("role") or "courseware",
                 doc.get("corpus") or "main", doc.get("warnings") or "[]",
                 json.dumps(extra_new, ensure_ascii=False),
                 doc.get("indexed_at") or _now(), _now()))
            new_doc_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])

            src_to_new: dict[int, int] = {}
            ord_to_new: dict[int, list[int]] = {}
            _ipc(progress, "chunks", 0, info["counts"].get("chunks") or 0)
            n = 0
            for obj in _Entry(zf, CHUNKS, _manifest_sha(manifest, CHUNKS)).lines():
                o = int(obj["ord"])
                text = obj["text"]
                conn.execute(
                    "INSERT INTO chunks(doc_id,ord,text,text_norm,heading_path,topic,"
                    "source_ref,char_len,token_est,content_hash,created_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (new_doc_id, o, text, db.normalize_for_match(text),
                     obj.get("heading_path"), obj.get("topic"), obj.get("source_ref"),
                     obj.get("char_len") if obj.get("char_len") is not None else len(text),
                     obj.get("token_est"), obj.get("content_hash"),
                     obj.get("created_at") or _now()))
                nid = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
                if isinstance(obj.get("id"), int):
                    src_to_new[int(obj["id"])] = nid
                ord_to_new.setdefault(o, []).append(nid)
                n += 1
                if n % 200 == 0:
                    _ipc(progress, "chunks", n, info["counts"].get("chunks") or n)
            _ipc(progress, "chunks", n, n)

            _ipc(progress, "embeddings", 0, n_emb["total"])
            used: set[int] = set()
            m = 0
            for obj in _Entry(zf, EMBEDDINGS, _manifest_sha(manifest, EMBEDDINGS)).lines():
                blob = base64.b64decode(obj["vec_b64"], validate=True)
                nid = None
                if isinstance(obj.get("src_id"), int):
                    nid = src_to_new.get(int(obj["src_id"]))
                if nid is None:
                    cands = [c for c in ord_to_new.get(int(obj.get("ord") or -1), [])
                             if c not in used]
                    if not cands:
                        raise ShareError("向量找不到对应的块（src_id=%r ord=%r）："
                                         "包内 chunks 与 embeddings 不匹配"
                                         % (obj.get("src_id"), obj.get("ord")))
                    nid = cands[0]
                conn.execute("INSERT INTO embeddings(chunk_id,model,dim,vec) VALUES(?,?,?,?)",
                             (nid, pm, pd, blob))
                used.add(nid)
                m += 1
                if m % 200 == 0:
                    _ipc(progress, "embeddings", m, n_emb["total"])
            _ipc(progress, "embeddings", m, m)
            if m != n:
                raise ShareError("写入的向量数 %d 与块数 %d 不符：包内 chunks/embeddings 不匹配"
                                 % (m, n))

            if FIGURES in members:
                for obj in _Entry(zf, FIGURES, _manifest_sha(manifest, FIGURES)).lines():
                    conn.execute(
                        "INSERT INTO figure_desc(doc_id,page,page_title,image_hash,model,"
                        "thinking,tokens_in,tokens_out,cost_cny,n_chunks,created_at,"
                        "description,no_info) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (new_doc_id, int(obj["page"]), obj.get("page_title"),
                         obj.get("image_hash") or "", obj.get("model"),
                         int(obj.get("thinking") or 0), int(obj.get("tokens_in") or 0),
                         int(obj.get("tokens_out") or 0), float(obj.get("cost_cny") or 0.0),
                         int(obj.get("n_chunks") or 0), obj.get("created_at") or _now(),
                         obj.get("description"), int(obj.get("no_info") or 0)))

            # 知识点产物：改文件名 + 改 meta.doc_id + 重映射块引用
            k_out: list[dict] = []
            remap_stats = {"remapped": 0, "dropped": 0}
            if know:
                kdir = log_dir / "knowledge"
                kdir.mkdir(parents=True, exist_ok=True)
            for k in know:
                obj, st = rewrite_knowledge(k["obj"], new_doc_id, src_to_new)
                remap_stats["remapped"] += st["remapped"]
                remap_stats["dropped"] += st["dropped"]
                dest = kdir / ("%d%s" % (new_doc_id, k["suffix"]))
                dest.write_text(json.dumps(obj, ensure_ascii=False, indent=2),
                                encoding="utf-8")
                written.append(dest)
                k_out.append({"file": k["name"], "new_file": dest.name,
                              "new_path": str(dest), "n_points": k["n_points"],
                              "remapped_refs": st["remapped"], "dropped_refs": st["dropped"]})

            # 页图：写到本库 logs/figure_desc/<新文件名 stem>/，供"以后重跑识图"复用
            n_pages = 0
            if page_entries:
                stem = Path(new_file.name).stem if new_file else clean_name(
                    str(doc.get("file_name") or doc["title"]), 60)
                from . import figure_cache
                pdir = figure_cache.directory(log_dir, new_doc_id, doc.get("content_hash"))
                if pdir is None:
                    raise ShareError("导入页图时无法确认文档内容指纹")
                if not pdir.exists():
                    made_dirs.append(pdir)
                pdir.mkdir(parents=True, exist_ok=True)
                for i, entry in enumerate(page_entries, 1):
                    dest = pdir / Path(entry).name
                    if dest.exists():
                        warnings.append("页图 %s 已存在，跳过（不覆盖本机已有的图）" % dest.name)
                        continue
                    _Entry(zf, entry, _manifest_sha(manifest, entry)).write_to(dest)
                    written.append(dest)
                    n_pages += 1
                    _ipc(progress, "pages", i, len(page_entries))

            fts = _ensure_fts(conn, new_doc_id)
            if fts.get("fts") and not fts.get("ok"):
                warnings.append("全文索引探测未命中所导入的块（probe=%r），"
                                "检索可能只能靠 LIKE 兜底" % fts.get("probe"))
        conn.commit()
    except BaseException:
        try:
            conn.rollback()
        except sqlite3.Error:
            pass
        for p in reversed(written):
            try:
                Path(p).unlink(missing_ok=True)
            except OSError:
                pass
        for d in reversed(made_dirs):
            try:
                d.rmdir()
            except OSError:
                pass
        raise
    finally:
        conn.close()

    return {
        "status": "imported", "doc_id": new_doc_id, "title": doc["title"],
        "zip": str(zp), "package_sha256": pkg_sha,
        "path": rel, "file": new_file.name if new_file else None,
        "counts": {"documents": 1, "chunks": n, "embeddings": m, "figure_desc": n_fig,
                   "knowledge": len(k_out), "pages": n_pages},
        "source": info["source"], "embedding": {"model": pm, "dim": pd},
        "knowledge": k_out, "remap": remap_stats,
        "fts": fts, "warnings": warnings,
        "model_calls": 0, "cost_cny": 0.0,
    }
