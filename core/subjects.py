# Copyright (c) 2026 潜声海豚 <2227663973@qq.com>. All rights reserved.
# 本源码为著作权人私有作品，适用项目根目录 LICENSE（专有许可）。
# 允许个人安装与使用；禁止反向工程、修改、再分发、商业使用及声称原创。
# 本软件不含源码，按「现状」提供；详见 NOTICE.txt。     
# File-ID: SA-19-2afa98d5   (project study_agent; keep this line)
"""多学科知识库：学科注册表 / 新建 / 切换（卡 C-0039）。

设计要点
--------
1. **默认学科 = 项目根**。它继续用 `raw/ index/ notes/ logs/` 这套既有目录，
   **零迁移、零复制**，现有 39 份资料原位不动。切换实现就是把这 5 个路径
   重新绑回项目根。
2. **新建学科 = 只建空 schema**。新学科落在 `subjects/<slug>/`，里面只有
   空目录 + 一个刚 `init_db` 出来的空库（约 1~2 MB）。**绝不复制**默认库的
   82.5 MB 数据——那既慢又会把两个学科的资料混在一起。
3. **切库靠"重绑 config 属性"，不靠 reload，也不靠环境变量。**
   本项目全部消费方写的都是 `config.RAW_DIR` / `config.LOG_DIR` 这种
   **属性访问**（core 里没有一处 `from .config import DB_PATH`），
   所以 `setattr(config, ...)` 之后立即全进程生效。
   `importlib.reload` 会重新执行 `_load_env()` 并重建所有 Path 常量，
   副作用面大（例如 `db._REAL_DB_PATH` 会被一起改掉，护栏就失效了），
   因此**不采用**。
4. **嵌入模型跨学科共享**：`EMBED_MODEL_DIR` / `MODEL_DIR` 不属于学科路径，
   切库不碰它，`core/embed.get_embedder()` 的进程内单例因此原样复用，
   切库没有任何模型加载开销。

⚠ 切库会改全局的 `config.*`，是**进程级**状态。调用方必须在
`tools/dashboard.py::_lock` 里串行调用（看板已如此），且**不得在有入库/
整理任务在跑时切换**（看板在路由层拦了，见 `api_subjects_switch`）。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import time
from datetime import datetime
from pathlib import Path

from . import config

# ---------------------------------------------------------------- 常量
# 学科目录名（在项目根下）。注册表放在**项目根的 logs/** 里，
# 因为"有哪些学科、当前用哪个"是**跨学科**的元信息，不属于任何一个学科。
SUBJECTS_DIRNAME = "subjects"
REGISTRY_NAME = "subjects.json"
SUBJECT_META = "subject.json"
DEFAULT_SLUG = "default"
DEFAULT_NAME = "默认"
REGISTRY_VERSION = 1

# 学科自己的子目录名，与默认学科的布局**逐字一致**——
# 这是"新学科与老学科功能完全相同"的结构前提。
_SUBDIRS = ("raw", "index", "notes", "logs")

# config 里那 5 个可覆盖的路径属性 ↔ 环境变量键。
# 键名与 `core/config.py::_path()` 里用的完全一样，别改。
_ENV_KEYS = {
    "RAW_DIR": "KB_RAW",
    "NOTES_DIR": "KB_NOTES",
    "INDEX_DIR": "KB_INDEX",
    "DB_PATH": "KB_DB",
    "LOG_DIR": "KB_LOG",
}

# 看板读知识点目录时会看这个环境变量（`KB_KNOWLEDGE_DIR`）。
# 它是"全局单库"时代的覆盖开关，与多学科互斥：切库时必须清掉，
# 否则新学科的知识点会被写到那个固定目录里去（隔离就破了）。
_KNOWLEDGE_DIR_ENV = "KB_KNOWLEDGE_DIR"

_NAME_MAX = 40
_SLUG_MAX = 60

# 默认学科的 5 条路径快照，**在模块首次导入时固定**。
# 此刻 config 还没被任何 switch 改过，取到的就是 .env / 默认值算出来的真身。
_DEFAULT_PATHS: dict[str, Path] = {
    "RAW_DIR": Path(config.RAW_DIR),
    "NOTES_DIR": Path(config.NOTES_DIR),
    "INDEX_DIR": Path(config.INDEX_DIR),
    "DB_PATH": Path(config.DB_PATH),
    "LOG_DIR": Path(config.LOG_DIR),
}

# 当前学科（slug）。进程启动时是 default，`restore()` 会按注册表纠正。
_CURRENT = DEFAULT_SLUG


class SubjectError(Exception):
    """学科操作失败（名字非法、重名、slug 不存在……）。消息直接给用户看。"""


# ---------------------------------------------------------------- 基础路径
def root() -> Path:
    """项目根。它**永远不变**，是算 `documents.path` 相对路径的基准。"""
    return Path(config.ROOT)


def subjects_root() -> Path:
    return root() / SUBJECTS_DIRNAME


def registry_path() -> Path:
    """注册表位置：项目根 `logs/subjects.json`。

    `logs/` 取的是**项目根下那个**，不是 `config.LOG_DIR`——
    切到别的学科后 `config.LOG_DIR` 会变，注册表就跟着漂了。
    """
    return root() / "logs" / REGISTRY_NAME


def subject_dir(slug: str) -> Path:
    return subjects_root() / slug


def paths_of(slug: str) -> dict[str, Path]:
    """某学科的 5 条路径。default 返回项目根那套（快照值，不是 config 现值）。"""
    slug = _check_slug(slug)
    if slug == DEFAULT_SLUG:
        return dict(_DEFAULT_PATHS)
    d = subject_dir(slug)
    if not d.is_dir():
        raise SubjectError("学科不存在：%s" % slug)
    return {
        "RAW_DIR": d / "raw",
        "NOTES_DIR": d / "notes",
        "INDEX_DIR": d / "index",
        "DB_PATH": d / "index" / "study.db",
        "LOG_DIR": d / "logs",
    }


def current_paths() -> dict[str, Path]:
    """**当前真实**路径。`/api/subjects` 会原样吐给前端，用于人眼核对隔离。"""
    return {k: Path(getattr(config, k)) for k in _ENV_KEYS}


def current() -> str:
    return _CURRENT


# ---------------------------------------------------------------- 名字校验
_ILLEGAL_RE = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
_WS_RE = re.compile(r"[\s\u3000]+")
_DASH_RE = re.compile(r"-{2,}")


def slugify(name: str) -> str:
    """学科名 → 目录名。

    中文**原样保留**（Windows 路径完全支持，项目里已有 `raw/药理`、
    `logs/胰岛素…_pages` 这类中文目录）；只做三件事：空白折叠成 `-`、
    去掉文件系统非法字符、去掉首尾的点和横线（首尾点会被 Windows 吃掉）。
    不做拼音转换：拼音既不可逆又会撞名（"数学"/"数序"同音），
    而且中文目录名对人更可读。
    """
    s = _WS_RE.sub("-", (name or "").strip())
    s = _ILLEGAL_RE.sub("", s)
    s = _DASH_RE.sub("-", s)
    s = s.strip(".-")
    if len(s) > _SLUG_MAX:
        s = s[:_SLUG_MAX].strip(".-")
    return s


def _norm_name(name: str) -> str:
    return _WS_RE.sub(" ", (name or "").strip()).casefold()


def validate_name(name, exclude_slug: str | None = None) -> str:
    """校验并返回规整后的学科名。不合法就抛 `SubjectError`（消息可直接展示）。

    `exclude_slug`（卡 C-0085）：**改自己的名**时把自己排除在重名检查之外——
    否则「药物化学 → 药物化学（改个空格）」这种会被自己挡住。
    默认 None = 老行为（新建学科时的全量重名检查），一个字都没变。
    """
    if not isinstance(name, str):
        raise SubjectError("学科名不能为空")
    clean = _WS_RE.sub(" ", name.strip())
    if not clean:
        raise SubjectError("学科名不能为空")
    if len(clean) > _NAME_MAX:
        raise SubjectError("学科名太长（最多 %d 字，当前 %d 字）" % (_NAME_MAX, len(clean)))
    # 非法字符**直接拒绝**，不静默删掉：`a/b` 被悄悄建成 `ab` 会让人以为
    # 自己建错了学科。宁可在弹窗里说清楚。
    bad = sorted(set(_ILLEGAL_RE.findall(clean)))
    if bad:
        raise SubjectError("学科名不能包含这些字符：%s"
                           % " ".join(repr(c) if c.isprintable() else "控制字符" for c in bad))
    if not slugify(clean):
        raise SubjectError("学科名里没有可用作目录名的字符，请换一个")
    key = _norm_name(clean)
    for s in list_subjects():
        if exclude_slug is not None and s["slug"] == exclude_slug:
            continue
        if _norm_name(s["name"]) == key:
            raise SubjectError("已存在同名学科「%s」，请换个名字" % s["name"])
    return clean


def _check_slug(slug) -> str:
    if not isinstance(slug, str) or not slug.strip():
        raise SubjectError("学科标识不能为空")
    slug = slug.strip()
    if slug != Path(slug).name or slug in (".", "..") or _ILLEGAL_RE.search(slug):
        raise SubjectError("学科标识非法：%s" % slug)
    return slug


# ---------------------------------------------------------------- 注册表
def _read_registry() -> dict:
    """读注册表；坏了/不在都返回空壳（学科列表的真源是目录扫描，不靠它）。"""
    p = registry_path()
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def _write_registry(cur: str, subjects: list[dict],
                    default_name: str | None = None) -> None:
    p = registry_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    if default_name is None:
        # 没显式给就**沿用现有值**（卡 C-0085）：切库 / 新建学科不该把
        # 默认学科的名字顺手抹回"默认"。
        default_name = str(_read_registry().get("default_name") or "")
    payload = {
        "version": REGISTRY_VERSION,
        "current": cur,
        "updated_at": time.time(),
        # 默认学科没有自己的 subject.json（它就是项目根），所以它的**显示名**
        # 落在注册表里这一格（卡 C-0085）。空串 = 尚未改过名 → 用 DEFAULT_NAME。
        "default_name": default_name,
        # default 不落盘：它就是项目根，没有自己的元数据文件，
        # 每次由 `list_subjects()` 现算。写进去反而会有一份可能过期的副本。
        "subjects": [{"slug": s["slug"], "name": s["name"],
                      "created_at": s.get("created_at"),
                      "note": s.get("note", "")}
                     for s in subjects if s["slug"] != DEFAULT_SLUG],
    }
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, p)          # 原子替换：写一半被打断也不会留下坏注册表


def _read_meta(slug: str) -> dict:
    p = subject_dir(slug) / SUBJECT_META
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def _default_name() -> str:
    """默认学科的显示名（卡 C-0085）：注册表里的 `default_name`，没有就用 `DEFAULT_NAME`。

    默认学科**没有** `subjects/<slug>/subject.json`（它就是项目根），所以它的名字
    只能落在跨学科的注册表里——与 `current` 同处一室，也不给项目根新增一个
    可能被误认成"学科元数据"的文件。
    """
    v = _read_registry().get("default_name")
    if isinstance(v, str) and v.strip():
        return _WS_RE.sub(" ", v.strip())
    return DEFAULT_NAME


def _default_entry() -> dict:
    return {
        "slug": DEFAULT_SLUG,
        "name": _default_name(),
        "dir": str(root()),
        "created_at": None,
        "note": "项目根下的原有资料库（raw/ index/ notes/ logs/）",
        "builtin": True,
        "db_exists": _DEFAULT_PATHS["DB_PATH"].exists(),
    }


def list_subjects() -> list[dict]:
    """全部学科：default 恒在第一，其余按目录扫描（真源是 `subjects/<slug>/`）。

    **不依赖注册表**：注册表丢了、坏了，学科一个都不会少——
    每个学科自己的 `subject.json` 才是它的身份。注册表只额外记住"当前是哪个"。
    """
    out = [_default_entry()]
    sr = subjects_root()
    try:
        dirs = sorted(p for p in sr.iterdir() if p.is_dir())
    except OSError:
        dirs = []
    for d in dirs:
        if d.name.startswith("."):
            continue
        meta = _read_meta(d.name)
        out.append({
            "slug": d.name,
            "name": meta.get("name") or d.name,
            "dir": str(d),
            "created_at": meta.get("created_at"),
            "note": meta.get("note", ""),
            "builtin": False,
            "db_exists": (d / "index" / "study.db").exists(),
        })
    return out


def get(slug: str) -> dict:
    slug = _check_slug(slug)
    for s in list_subjects():
        if s["slug"] == slug:
            return s
    raise SubjectError("学科不存在：%s" % slug)


# ---------------------------------------------------------------- 绑定 / 切换
def _bind(paths: dict) -> None:
    """把 5 条路径绑到 config 上，并同步写回环境变量。

    写环境变量是**冗余保险**：当前管线全在进程内跑（无子进程），
    但将来若有子进程（如另起 python 跑入库），子进程 `import core.config`
    时会从 env 取值，那时这一步就是必需的。成本只有 5 次 setenv。
    """
    for attr, key in _ENV_KEYS.items():
        val = Path(paths[attr])
        setattr(config, attr, val)
        os.environ[key] = str(val)
    # 与多学科互斥的全局覆盖开关，必须清掉（见文件头注释）
    os.environ.pop(_KNOWLEDGE_DIR_ENV, None)


def _ensure_layout(paths: dict) -> None:
    for attr in ("RAW_DIR", "NOTES_DIR", "INDEX_DIR", "LOG_DIR"):
        Path(paths[attr]).mkdir(parents=True, exist_ok=True)
    Path(paths["DB_PATH"]).parent.mkdir(parents=True, exist_ok=True)


def switch(slug: str, *, save: bool = True) -> dict:
    """切到某学科：重绑路径 + 落注册表。返回该学科条目（含新路径）。"""
    global _CURRENT
    entry = get(slug)
    paths = paths_of(slug)
    _ensure_layout(paths)
    _bind(paths)
    _CURRENT = entry["slug"]
    if save:
        _write_registry(_CURRENT, list_subjects())
    return entry


def restore() -> dict:
    """看板启动时按注册表恢复上次的学科。注册表缺失/指向不存在的学科 → 回默认。

    必须在**第一次连库之前**调用（`dashboard.main()` 里就是这么排的）。
    """
    reg = _read_registry()
    want = reg.get("current") or DEFAULT_SLUG
    try:
        return switch(want, save=False)
    except SubjectError:
        return switch(DEFAULT_SLUG, save=False)


# ---------------------------------------------------------------- 新建
def _unique_slug(base: str) -> str:
    taken = {s["slug"] for s in list_subjects()}
    if base not in taken:
        return base
    for i in range(2, 200):
        cand = "%s-%d" % (base, i)
        if cand not in taken:
            return cand
    raise SubjectError("同名学科太多，请换个更具体的名字")


def create(name: str, note: str = "", *, use: bool = False) -> dict:
    """新建学科：建目录 + 建**空库**（完整 schema）+ 写元数据。

    ★ 刻意**不复制任何默认库数据**：`init_db()` 只跑 `SCHEMA` + 迁移 + FTS 重建，
      新库初始就 1 MB 出头。复制 82.5 MB 既慢，又会让新学科一出生就带着
      别人家的课件和知识点——那是需求明令禁止的"课件与知识点必须独立"。

    `use=True` 时建完直接切过去（看板走的就是这条：点「新建」→ 输名字 →
    确定 → 立刻停在新学科，见 `api_subjects_create`）。
    """
    from . import db  # 延迟导入：避免 core 模块间的循环依赖

    clean = validate_name(name)
    slug = _unique_slug(slugify(clean))
    d = subject_dir(slug)
    if d.exists():
        raise SubjectError("目录已存在：%s" % d)

    # 直接构造路径（此刻目录还没建，`paths_of()` / `get()` 都还看不到这个 slug）
    paths = {
        "RAW_DIR": d / "raw",
        "NOTES_DIR": d / "notes",
        "INDEX_DIR": d / "index",
        "DB_PATH": d / "index" / "study.db",
        "LOG_DIR": d / "logs",
    }

    try:
        for sub in _SUBDIRS:
            (d / sub).mkdir(parents=True, exist_ok=True)

        # ★ 建空库。显式传路径 → 与当前 config 无关，绝不会碰到默认库。
        conn = db.connect(paths["DB_PATH"])
        try:
            info = db.init_db(conn)
        finally:
            conn.close()

        meta = {
            "name": clean,
            "slug": slug,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "note": note or "",
            "layout": {k: str(v) for k, v in paths.items()},
            "schema": info,
        }
        (d / SUBJECT_META).write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        # 建到一半失败就整个清掉，不留半个学科在列表里
        shutil.rmtree(d, ignore_errors=True)
        raise

    entry = {
        "slug": slug,
        "name": clean,
        "dir": str(d),
        "created_at": meta["created_at"],
        "note": meta["note"],
        "builtin": False,
        "db_exists": True,
        "schema": info,
    }
    _write_registry(_CURRENT, list_subjects())
    if use:
        switch(slug)
    return entry


# ---------------------------------------------------------------- 重命名（卡 C-0085）
def rename(slug: str, name: str) -> dict:
    """改学科**显示名**。`slug`（目录名）与 5 条路径**一个字都不动**。

    人 2026-10-12 的原话：「还要有**重命名**功能，如**重命名学科库**」。

    ★ 为什么只有这一条路（只改 name、绝不搬目录）：
      · slug 是 `create()` 时按当时的名字算一次（`slugify`），此后与 name **解耦**——
        `list_subjects()` 扫目录 + 读 `subject.json` 的 name，`paths_of(slug)`
        按 slug 现算，所以改名后目录名与显示名不一致**完全不影响任何功能**；
      · 搬目录要连带改 `subject.json.layout` 的 5 条绝对路径、注册表里的 slug、
        以及所有正在跑的连接与 `config.*` 绑定——任何一处漏掉都留下误导性记录
        （风险 ⑤，卡面明令建议不搬）。

    默认学科（`default`）走注册表的 `default_name` 一格：它没有 `subject.json`，
    但这不代表它不能改名——人的默认库往往资料最多，最需要改名。

    返回改名后的学科条目，并附 `renamed` 明细（old / new / 落在哪个文件 / 有没有搬目录）。
    """
    slug = _check_slug(slug)
    entry = get(slug)                      # 不存在 → SubjectError（原话给前端）
    old = entry["name"]

    if entry.get("builtin"):
        clean = validate_name(name, exclude_slug=DEFAULT_SLUG)
        _write_registry(_CURRENT, list_subjects(), default_name=clean)
        out = get(DEFAULT_SLUG)
        out["renamed"] = {"slug": DEFAULT_SLUG, "old": old, "new": clean,
                          "where": str(registry_path()), "moved_dir": False,
                          "note": "默认学科没有 subject.json，名字记在注册表 default_name"}
        return out

    clean = validate_name(name, exclude_slug=slug)
    meta = _read_meta(slug)
    meta["name"] = clean
    meta["renamed_at"] = datetime.now().isoformat(timespec="seconds")
    p = subject_dir(slug) / SUBJECT_META
    tmp = p.with_suffix(".json.tmp")
    try:
        tmp.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, p)                 # 原子替换：写一半被打断也不会留下坏 subject.json
    except OSError as e:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise SubjectError("写 subject.json 失败：%s" % e)
    # 注册表里的 name 只是副本（真源是目录扫描 + subject.json），一并同步，
    # 免得 `/api/subjects` 与注册表两处名字不一致让人以为没改成功。
    _write_registry(_CURRENT, list_subjects())
    out = get(slug)
    out["renamed"] = {"slug": slug, "old": old, "new": clean,
                      "where": str(p), "moved_dir": False,
                      "note": "slug 目录名与 5 条路径未动（改名不改库的位置）"}
    return out


def info() -> dict:
    """给 `/api/subjects` 的完整快照：列表 + 当前 + **当前真实路径**。"""
    return {
        "current": _CURRENT,
        "subjects": list_subjects(),
        "paths": {k: str(v) for k, v in current_paths().items()},
        "root": str(root()),
        "subjects_root": str(subjects_root()),
        "registry": str(registry_path()),
        "default_paths": {k: str(v) for k, v in _DEFAULT_PATHS.items()},
    }