# Copyright (c) 2026 潜声海豚 <2227663973@qq.com>. All rights reserved.
# 本源码为著作权人私有作品，适用项目根目录 LICENSE（专有许可）。
# 允许个人安装与使用；禁止反向工程、修改、再分发、商业使用及声称原创。
# File-ID: SA-KP-IMPORT-20261018   (project study_agent; keep this line)
r"""整理产物 → 知识点表导入（M0-3 逻辑内核，M0-6b 看板入口复用）。

一句话：把 `logs/knowledge/<doc_id>-末级知识点总结.json` 离线解析进
`knowledge_points` + `kp_sources`，**零 AI 调用、零网络、幂等**。

本模块是「逻辑内核」（纯函数，不 print、不 exit、不解析命令行）；
命令行壳是 `tools/import_kp_artifacts.py`，看板 UI 入口直接调本模块。

=======================================================================
API 契约（供 dashboard / 其它智能体接线用）
=======================================================================

    from core import kp_import
    stats = kp_import.run(db_path=..., apply=True)

### 签名

    run(conn=None, *, db_path=None, artifacts_dir=None, doc_id=None,
        apply=False, max_sources_per_kp=None) -> dict

| 参数 | 类型 / 默认 | 说明 |
|---|---|---|
| `conn` | `sqlite3.Connection \| None`，默认 None | 复用调用方连接。**None = 自己开/关连接**（dry-run 用 sqlite `mode=ro` 只读连接，绝不写 WAL；apply 用 `core.db.connect` 并跑一次 `db.init_db` 补 schema）。传了 conn 时**本函数不关闭它**，但内部会 commit（与 `study.upsert_kp` 一致），调用方不要在外面再套未提交的大事务。 |
| `db_path` | `Path \| str \| None`，None→`config.DB_PATH` | 目标库。**测试/演示必须传副本库**；函数自身有 `KB_TESTING=1` 时连真实库会被 `core.db.connect` 的护栏拒绝。 |
| `artifacts_dir` | `Path \| str \| None`，None→`config.LOG_DIR/knowledge` | 产物目录，只收 `<doc_id>-末级知识点总结.json`（`.bak-*` 自动忽略）。 |
| `doc_id` | `int \| str \| Iterable[int] \| None` | 只导指定课件。None/空 = 全部；str 支持 `"41,42"`；非整数抛 `KpImportError`。 |
| `apply` | `bool`，默认 **False** | False = dry-run（dry-run 零写入，连库文件 mtime 都不变）；True = 真写库。 |
| `max_sources_per_kp` | `int \| None`，None→24 | 单条知识点最多挂几条出处；<=0 视为默认。 |

### 返回 dict（与 CLI `--json` 逐字段一致）

`mode`(dry_run/apply) / `db` / `artifacts_dir` / `scanned_files` /
`parsed_files` / `failed_files[{file,error}]` / `docs_matched` /
`docs_missing_in_db[]` / `kps_found` / `kps_invalid` / `kps_created` /
`kps_would_create` / `kps_existing` / `kps_name_conflict` /
`kps_without_sources` / `kp_sources_added` / `kp_sources_would_add` /
`name_conflicts_sample[]` / `renamed` / `manifest_updated` /
`record_failures[{key,name,error}]` / `elapsed_ms` / `manifest_size`。

口径：dry-run 看 `kps_would_create` / `kp_sources_would_add`；
apply 看 `kps_created` / `kp_sources_added`（真实写入数）。

### 幂等语义

- 稳定键 = `<doc_id>:<产物知识点 id>`，记录在 `meta` 表的导入清单
  (`kp_import.artifacts.v1`)；同一批产物重复 `apply` → `kps_created=0`、
  `kps_existing=条目数`、`kp_sources_added` 只补新增出处。
- 清单丢失时退回「名称 + 摘要完全一致」认领，仍不重复建卡。
- 绝不覆盖用户机器建的同名知识点：冲突记 `kps_name_conflict` 并跳过。

### 错误处理

- **前置条件**（库文件/产物目录不存在、缺 documents/chunks 表）→
  抛 `KpImportError`（CLI 捕获后退出码 2）。调用方按普通异常处理即可。
- **单份产物坏 / 单条知识点写失败** → 不抛，收进返回值的
  `failed_files` / `record_failures`，其余继续（exit 0 语义）。
- 数据库损坏等 `sqlite3.Error` 原样上抛。

=======================================================================
"""
from __future__ import annotations

import json
import re
import sqlite3
import time
from pathlib import Path

from . import config, db, study

LKP_SUFFIX = "-末级知识点总结.json"
# 导入清单落在 meta 表；key 带版本，将来换格式可并存迁移
MANIFEST_KEY = "kp_import.artifacts.v1"

# source_ref 的页号解析：真实库 724 块实测只有三种形态——
# `p.12` / `p.12-16` / `p.12 图`。
_PAGE_REF_RE = re.compile(r"p\s*\.?\s*(\d+)(?:\s*[-–~—至]\s*(\d+))?", re.I)
_PAGE_ANY_RE = re.compile(r"(?:slide|para)\s*(\d+)", re.I)

# 一条知识点最多挂多少出处。显式材料块真实产物 max=21；页兜底时按 ord 取前 N 块，
# 避免一条知识点吞掉整页所有块。
DEFAULT_MAX_SOURCES = 24

# 摘要上限：产物 summary 150~400 字 + 要点 3~6 条，正常 <800 字；
# 留 1200 余量，防异常产物把知识库塞进长文。
SUMMARY_MAX_CHARS = 1200

# 名称避让最多加深几级前缀（产物路径最深 5 级，12 足够且防死循环）
MAX_NAME_DEPTH = 12

__all__ = ["run", "KpImportError"]


class KpImportError(Exception):
    """前置条件不满足（库/产物目录不存在、缺表、参数不合法）。

    这是本模块**唯一**的可预期异常：CLI 捕获后映射为退出码 2；
    dashboard 接线时可直接 `except kp_import.KpImportError as e: show(str(e))`。
    单份产物坏 / 单条知识点失败**不抛**——收进返回值的 failed_files /
    record_failures（「部分失败也 exit 0」的语义）。
    """


# ---------------------------------------------------------------- 产物解析
def _parse_page_range(ref) -> tuple[int, int] | None:
    """`p.12` / `p.12-16` / `p.12 图` → (12,12) / (12,16)；解析不了给 None。"""
    s = str(ref or "")
    m = _PAGE_REF_RE.search(s)
    if m:
        a = int(m.group(1))
        b = int(m.group(2) or a)
        if b < a:
            a, b = b, a
        return (a, b)
    m = _PAGE_ANY_RE.search(s)
    if m:
        a = int(m.group(1))
        return (a, a)
    return None


def _as_int_list(value) -> list[int]:
    """pages 之类字段宽容解析：只收能变成正整数的元素。"""
    if not isinstance(value, (list, tuple)):
        value = [value] if value not in (None, "") else []
    out: list[int] = []
    for v in value:
        try:
            n = int(v)
        except (TypeError, ValueError):
            continue
        if n > 0 and n not in out:
            out.append(n)
    return out


def _str_list(value) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    out: list[str] = []
    for v in value:
        s = str(v or "").strip()
        if s and s not in out:
            out.append(s)
    return out


def _explicit_chunk_ids(kp: dict) -> list[int]:
    """产物里**显式**记录的材料块 id（v1 `blocks` / v2 `sources` / `material_block_ids`）。

    三者取并集：它们都是「管线真的读过的块」，比按页兜底精确。
    """
    got: list[int] = []

    def add(v):
        try:
            n = int(v)
        except (TypeError, ValueError):
            return
        if n > 0 and n not in got:
            got.append(n)

    for src in (kp.get("sources") or []):
        if isinstance(src, dict):
            for cid in (src.get("chunk_ids") or []):
                add(cid)
    for cid in (kp.get("material_block_ids") or []):
        add(cid)
    for b in (kp.get("blocks") or []):
        if isinstance(b, dict):
            add(b.get("chunk_id"))
    return got


def _normalize_record(kp: dict) -> dict | None:
    """把产物里的单条知识点规范成内部记录；缺名称返回 None。"""
    if not isinstance(kp, dict):
        return None
    name = str(kp.get("name") or kp.get("node_name") or "").strip()
    if not name:
        return None
    path = _str_list(kp.get("node_path")) or _str_list(kp.get("path")) or [name]
    uid = str(kp.get("id") or kp.get("node_id") or "").strip()
    if not uid:
        # 没有 id 的旧格式：用完整路径当稳定键（同路径重复出现的概率极低）
        uid = "path:" + "/".join(path)
    pages = (_as_int_list(kp.get("pages"))
             or _as_int_list(kp.get("content_pages"))
             or _as_int_list(kp.get("self_pages"))
             or _as_int_list(kp.get("page")))
    summary = str(kp.get("summary") or kp.get("understanding") or "").strip()
    points = _str_list(kp.get("points"))
    # 表里只有 summary 一列，详情页直接展示它 → 要点拼在摘要后，不能丢。
    if points:
        tail = "要点：" + "；".join(points)
        summary = (summary + " " + tail).strip() if summary else tail
    if len(summary) > SUMMARY_MAX_CHARS:
        summary = summary[:SUMMARY_MAX_CHARS].rstrip() + "…"
    return {
        "uid": uid,
        "name": name,
        "path": path,
        "pages": pages,
        "summary": summary,
        "explicit_chunk_ids": _explicit_chunk_ids(kp),
    }


def _tree_records(raw: dict) -> list[dict]:
    """极旧产物兼容：没有 `knowledge_points`、只有 `tree` 节点树时按末级节点兜底。"""
    tree = raw.get("tree")
    out: list[dict] = []

    def walk(node, trail):
        if not isinstance(node, dict):
            return
        name = str(node.get("name") or "").strip()
        path = trail + ([name] if name else [])
        children = node.get("children") or []
        if isinstance(children, list) and children:
            for c in children:
                walk(c, path)
            return
        if not name:
            return
        rec = _normalize_record({
            "name": name,
            "path": path,
            "pages": node.get("pages") or node.get("page"),
            "summary": node.get("summary") or node.get("understanding") or "",
            "points": node.get("points") or [],
            "id": node.get("id") or node.get("node_id") or "",
        })
        if rec:
            out.append(rec)

    if isinstance(tree, dict):
        walk(tree, [])
    elif isinstance(tree, list):
        for t in tree:
            walk(t, [])
    return out


def _doc_id_from(path: Path, meta: dict) -> int:
    cands = [meta.get("doc_id")]
    stem = path.name[: -len(LKP_SUFFIX)] if path.name.endswith(LKP_SUFFIX) else ""
    cands.append(stem)
    for cand in cands:
        try:
            did = int(cand)
        except (TypeError, ValueError):
            continue
        if did > 0:
            return did
    return 0


def load_artifact(path: Path) -> dict:
    """读一份产物 → `{doc_id, meta, records, pipeline_version}`。

    坏文件抛 ValueError（调用方记失败继续，绝不让一份产物阻断整批）。
    """
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError as e:
        raise ValueError(f"读取失败: {e}") from e
    except (ValueError, UnicodeDecodeError) as e:
        raise ValueError(f"JSON 解析失败: {e}") from e
    if not isinstance(raw, dict):
        raise ValueError("顶层不是对象（旧格式或坏文件）")
    meta = raw.get("meta") if isinstance(raw.get("meta"), dict) else {}
    doc_id = _doc_id_from(path, meta)
    if doc_id <= 0:
        raise ValueError("拿不到 doc_id（meta.doc_id 与文件名都不是正整数）")

    records: list[dict] = []
    if isinstance(raw.get("knowledge_points"), list):
        for kp in raw["knowledge_points"]:
            rec = _normalize_record(kp)
            if rec:
                records.append(rec)
    elif raw.get("tree"):
        records = _tree_records(raw)
    if not records:
        raise ValueError("没有可导入的知识点条目（缺 knowledge_points / tree）")
    # 同一份产物内重复 id 说明文件异常：保留第一条，重复条数记在首条上
    seen: set[str] = set()
    uniq: list[dict] = []
    dup = 0
    for rec in records:
        if rec["uid"] in seen:
            dup += 1
            continue
        seen.add(rec["uid"])
        uniq.append(rec)
    if dup:
        uniq[0]["duplicate_uid"] = dup
    return {"doc_id": doc_id, "meta": meta, "records": uniq,
            "pipeline_version": str(meta.get("pipeline_version") or "")}


# ---------------------------------------------------------------- 名称
def _parents(rec: dict) -> list[str]:
    name = rec["name"]
    return [p for p in rec["path"][:-1] if p and p != name]


def _candidate_name(rec: dict, depth: int) -> str:
    """depth=0 用节点名；depth>=1 逐级加父级前缀（`父级 · 名称`）。"""
    name = rec["name"]
    parents = _parents(rec)
    if depth <= 0 or not parents:
        return name
    take = min(depth, len(parents))
    return " · ".join(parents[-take:] + [name])


def assign_unique_names(records: list[dict]) -> list[str]:
    """在「本次扫描到的全部待导入记录」内保证名称唯一（不碰库）。

    只按产物内的重名加深前缀（真实产物 3 组跨课件重名 + 1 组同课件重名）；
    与库里已有名字的避让在规划阶段做（那里能看到 DB 现状）。
    极端情况（不同课件完整路径完全相同）追加 `（文档N）` 兜底。
    """
    n = len(records)
    depth = [0] * n
    if n == 0:
        return []
    max_depth = max((len(_parents(r)) for r in records), default=0)
    for _ in range(max_depth + 2):
        counts: dict[str, int] = {}
        for i, rec in enumerate(records):
            nm = _candidate_name(rec, depth[i])
            counts[nm] = counts.get(nm, 0) + 1
        bumped = False
        for i, rec in enumerate(records):
            if counts[_candidate_name(rec, depth[i])] > 1 and depth[i] < max_depth:
                depth[i] += 1
                bumped = True
        if not bumped:
            break
    names = [_candidate_name(rec, depth[i]) for i, rec in enumerate(records)]
    counts: dict[str, int] = {}
    for nm in names:
        counts[nm] = counts.get(nm, 0) + 1
    for i, rec in enumerate(records):
        if counts[names[i]] > 1:
            names[i] = f"{names[i]}（文档{rec['doc_id']}）"
    return names


def _deepen_name(rec: dict, current: str) -> str:
    """给已占用的名称再加一级父级前缀；加不动返回原值。"""
    parents = _parents(rec)
    if not parents:
        return current
    used = current.count(" · ")
    take = min(used + 1, len(parents))
    return " · ".join(parents[-take:] + [rec["name"]])


# ---------------------------------------------------------------- 数据库
def _connect_readonly(path: Path) -> sqlite3.Connection:
    """只读连接：`mode=ro`，不会创建/改写库文件，也不会写 WAL。"""
    uri = "file:%s?mode=ro" % path.as_posix()
    conn = sqlite3.connect(uri, uri=True, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def _table_names(conn: sqlite3.Connection) -> set[str]:
    try:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    except sqlite3.Error:
        return set()
    return {str(r["name"]) for r in rows}


def _load_doc_chunks(conn, doc_ids: list[int]) -> dict[int, list[dict]]:
    """`{doc_id: [{id, ord, page_a, page_b}]}`；解析不了页号的块 page_a=None。"""
    if not doc_ids:
        return {}
    ph = ",".join("?" * len(doc_ids))
    rows = conn.execute(
        f"SELECT id, doc_id, ord, source_ref FROM chunks WHERE doc_id IN ({ph})",
        doc_ids,
    ).fetchall()
    out: dict[int, list[dict]] = {}
    for r in rows:
        pr = _parse_page_range(r["source_ref"])
        out.setdefault(int(r["doc_id"]), []).append({
            "id": int(r["id"]),
            "ord": int(r["ord"] or 0),
            "page_a": pr[0] if pr else None,
            "page_b": pr[1] if pr else None,
        })
    for lst in out.values():
        lst.sort(key=lambda x: (x["ord"], x["id"]))
    return out


def _map_sources(rec: dict, doc_chunks: list[dict], max_sources: int) -> list[int]:
    """一条知识点的出处 chunk id 列表：显式材料块优先，覆盖页范围兜底。"""
    valid = {c["id"] for c in doc_chunks}
    got = [cid for cid in rec["explicit_chunk_ids"] if cid in valid]
    if got:
        return got[:max_sources]
    pages = set(rec["pages"])
    if not pages:
        return []
    hit: list[int] = []
    for c in doc_chunks:
        if c["page_a"] is None:
            continue
        if pages & set(range(c["page_a"], c["page_b"] + 1)):
            hit.append(c["id"])
            if len(hit) >= max_sources:
                break
    return hit


def _read_manifest(conn) -> dict:
    try:
        raw = db.get_meta(conn, MANIFEST_KEY)
    except sqlite3.Error:
        return {}
    if not raw:
        return {}
    try:
        obj = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    items = obj.get("items") if isinstance(obj, dict) else None
    return items if isinstance(items, dict) else {}


def _write_manifest(conn, items: dict) -> None:
    payload = {"version": 1, "kind": "kp_import.artifacts",
               "items": {k: items[k] for k in sorted(items)}}
    db.set_meta(conn, MANIFEST_KEY,
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    conn.commit()


def _kp_row(conn, kp_id):
    try:
        kid = int(kp_id)
    except (TypeError, ValueError):
        return None
    try:
        return conn.execute(
            "SELECT id, name, summary FROM knowledge_points WHERE id=?", (kid,)
        ).fetchone()
    except sqlite3.Error:
        return None


def _new_sources_count(conn, kp_id: int, chunk_ids: list[int]) -> int:
    if not chunk_ids:
        return 0
    ph = ",".join("?" * len(chunk_ids))
    try:
        rows = conn.execute(
            f"SELECT chunk_id FROM kp_sources WHERE kp_id=? AND chunk_id IN ({ph})",
            [int(kp_id)] + [int(c) for c in chunk_ids],
        ).fetchall()
    except sqlite3.Error:
        return len(chunk_ids)
    have = {int(r["chunk_id"]) for r in rows}
    return len([c for c in chunk_ids if c not in have])


# ---------------------------------------------------------------- 主流程
def _scan_artifacts(artifacts_dir: Path) -> dict:
    try:
        files = sorted(p for p in artifacts_dir.iterdir()
                       if p.is_file() and p.name.endswith(LKP_SUFFIX))
    except OSError as e:
        raise KpImportError(f"产物目录读不了: {artifacts_dir}（{e}）") from e
    parsed: list[dict] = []
    failed: list[dict] = []
    for p in files:
        try:
            art = load_artifact(p)
        except ValueError as e:
            failed.append({"file": p.name, "error": str(e)})
            continue
        art["file"] = p.name
        parsed.append(art)
    return {"scanned": len(files), "parsed": parsed, "failed": failed}


def _plan_impl(conn, target: list[dict], manifest: dict, stats: dict) -> None:
    """规划每条知识点：existing / adopt / create / name_conflict。

    只读库、不改库，dry-run 与 apply 共用同一份判断（保证「预览即所得」）。
    """
    used_names: set[str] = set()
    # 库里已有名称 → 只查一次；对应 kp_id 用于认领
    db_names: dict[str, dict] = {}
    for r in conn.execute("SELECT id, name, summary FROM knowledge_points").fetchall():
        db_names[str(r["name"])] = {"id": int(r["id"]), "summary": r["summary"] or ""}
    db_name_set = set(db_names)

    for rec in target:
        rec["_status"] = None
        rec["assigned"] = rec["final_name"]
        rec["_manifest_hit"] = False
        rec["_source_added"] = 0
        entry = manifest.get(rec["key"])
        entry = entry if isinstance(entry, dict) else None

        # ① 清单命中且 kp 还在 → 已存在（不重建；只补新增出处）
        if entry:
            row = _kp_row(conn, entry.get("kp_id"))
            if row is not None:
                rec["_status"] = "existing"
                rec["kp_id"] = int(row["id"])
                rec["_manifest_hit"] = True
                rec["assigned"] = str(row["name"])  # 用户可能改过名，以库里为准
                if str(row["name"]) != str(entry.get("name") or ""):
                    stats["manifest_updated"] += 1
                stats["kps_existing"] += 1
                rec["_source_added"] = _new_sources_count(conn, int(row["id"]), rec["chunk_ids"])
                if rec["chunk_ids"]:
                    stats["kp_sources_added"] += rec["_source_added"]
                else:
                    stats["kps_without_sources"] += 1
                continue

        # ② 与库里已有名字避让：逐级加父级前缀；摘要一致则认领
        name = rec["final_name"]
        conflict = False
        tries = 0
        while name in used_names or name in db_name_set:
            # 同名同内容（清单丢失/别的入口已导过）→ 认领，不建卡
            if name in db_names and db_names[name]["summary"].strip() == rec["summary"].strip():
                break
            deeper = _deepen_name(rec, name)
            if deeper == name or tries >= MAX_NAME_DEPTH:
                conflict = True
                break
            name = deeper
            tries += 1
            stats["renamed"] += 1
        rec["assigned"] = name
        if conflict:
            rec["_status"] = "name_conflict"
            stats["kps_name_conflict"] += 1
            if len(stats["name_conflicts_sample"]) < 10:
                stats["name_conflicts_sample"].append(
                    {"key": rec["key"], "name": name, "doc_id": rec["doc_id"]})
            continue
        used_names.add(name)

        if name in db_names:
            # 内容一致 → 已有同名同内容知识点：认领，不建卡
            rec["_status"] = "adopt"
            rec["kp_id"] = int(db_names[name]["id"])
            rec["_source_added"] = _new_sources_count(
                conn, int(db_names[name]["id"]), rec["chunk_ids"])
            stats["kps_existing"] += 1
            if rec["chunk_ids"]:
                stats["kp_sources_added"] += rec["_source_added"]
            else:
                stats["kps_without_sources"] += 1
            continue

        # ③ 新建
        rec["_status"] = "create"
        stats["kps_would_create"] += 1
        rec["_source_added"] = len(rec["chunk_ids"])
        stats["kp_sources_added"] += rec["_source_added"]
        if not rec["chunk_ids"]:
            stats["kps_without_sources"] += 1


def _normalize_doc_ids(doc_id) -> set[int]:
    """doc_id 参数归一化：int / "41,42" / 可迭代 → {41, 42}；坏值抛 KpImportError。"""
    if doc_id is None:
        return set()
    if isinstance(doc_id, bool):
        raise KpImportError(f"doc_id 不合法: {doc_id!r}")
    if isinstance(doc_id, int):
        return {doc_id} if doc_id > 0 else set()
    if isinstance(doc_id, str):
        parts = [x for x in re.split(r"[,，\s]+", doc_id.strip()) if x]
    else:
        try:
            parts = list(doc_id)
        except TypeError:
            raise KpImportError(f"doc_id 不合法（要 int / '41,42' / 列表）: {doc_id!r}") from None
    out: set[int] = set()
    for x in parts:
        try:
            n = int(x)
        except (TypeError, ValueError):
            raise KpImportError(f"doc_id 需要整数（可逗号分隔）: {x!r}") from None
        if n > 0:
            out.add(n)
    return out


def _normalize_max_sources(value) -> int:
    """max_sources_per_kp 归一化：None/非法/<=0 → DEFAULT_MAX_SOURCES。"""
    try:
        n = int(value) if value is not None else DEFAULT_MAX_SOURCES
    except (TypeError, ValueError):
        n = DEFAULT_MAX_SOURCES
    return n if n > 0 else DEFAULT_MAX_SOURCES


def _resolve_db_path(conn, db_path) -> Path:
    """定位目标库路径用于校验/统计；传了 conn 且没给 db_path 就反查连接。"""
    if db_path is not None:
        return Path(db_path).resolve()
    if conn is not None:
        try:
            for row in conn.execute("PRAGMA database_list").fetchall():
                if str(row[1]) == "main" and row[2]:
                    return Path(str(row[2])).resolve()
        except sqlite3.Error:
            pass
        return Path("")
    return Path(config.DB_PATH).resolve()


def run(conn=None, *, db_path=None, artifacts_dir=None, doc_id=None,
        apply=False, max_sources_per_kp=None) -> dict:
    """导入整理产物 → knowledge_points + kp_sources（默认 dry-run，幂等）。

    这是给 dashboard / 其它模块直接调用的纯函数入口，**不 print、不 exit**；
    CLI 壳在 `tools/import_kp_artifacts.py`。完整参数/返回/错误契约见模块
    顶部 docstring；最常用两行：

        from core import kp_import
        preview = kp_import.run(db_path=copy_db)          # dry-run：零写入
        result = kp_import.run(db_path=copy_db, apply=True)  # 真写
    """
    t0 = time.perf_counter()
    artifacts_dir = Path(artifacts_dir if artifacts_dir is not None
                         else Path(config.LOG_DIR) / "knowledge").resolve()
    want_doc_ids = _normalize_doc_ids(doc_id)
    max_sources = _normalize_max_sources(max_sources_per_kp)
    writable = bool(apply)
    own_conn = conn is None
    db_path = _resolve_db_path(conn, db_path)
    if not artifacts_dir.is_dir():
        raise KpImportError(f"产物目录不存在: {artifacts_dir}")
    if own_conn and not db_path.is_file():
        raise KpImportError(f"数据库不存在: {db_path}（测试请先复制副本）")

    scan = _scan_artifacts(artifacts_dir)
    db_conn = conn
    if own_conn:
        db_conn = db.connect(db_path) if writable else _connect_readonly(db_path)
    try:
        if writable:
            db.init_db(db_conn)  # 空库/新库补齐 schema；真实库本来就齐（幂等）
        tables = _table_names(db_conn)
        if "documents" not in tables or "chunks" not in tables:
            raise KpImportError(f"库里没有 documents/chunks 表，不像是学习库: {db_path}")
        if "knowledge_points" not in tables or "kp_sources" not in tables:
            if not writable:
                raise KpImportError("库里还没有知识点表（空库）；先 apply 一次会建表")
            db.init_db(db_conn)

        docs = {int(r["id"]): dict(r) for r in
                db_conn.execute("SELECT id, title FROM documents").fetchall()}
        doc_chunks = _load_doc_chunks(db_conn, sorted(docs))
        manifest = _read_manifest(db_conn)

        # ---- 组装记录：产物 doc_id 必须在库里（doc 删了产物还在 → 跳过并报告）
        records: list[dict] = []
        doc_missing: set[int] = set()
        invalid = 0
        for art in scan["parsed"]:
            did = art["doc_id"]
            if did not in docs:
                doc_missing.add(did)
                continue
            for rec in art["records"]:
                rec = dict(rec)
                rec["doc_id"] = did
                rec["doc_title"] = str(docs[did].get("title") or "")
                rec["chapter"] = str(art["meta"].get("chapter") or "").strip()
                rec["artifact"] = art["file"]
                rec["key"] = f"{did}:{rec['uid']}"
                rec["chunk_ids"] = _map_sources(rec, doc_chunks.get(did, []),
                                                max_sources)
                records.append(rec)

        names = assign_unique_names(records)
        for rec, nm in zip(records, names):
            rec["final_name"] = nm
        target = [r for r in records if not want_doc_ids or r["doc_id"] in want_doc_ids]

        stats = {
            "mode": "apply" if writable else "dry_run",
            "db": str(db_path),
            "artifacts_dir": str(artifacts_dir),
            "scanned_files": scan["scanned"],
            "parsed_files": len(scan["parsed"]),
            "failed_files": scan["failed"],
            "docs_matched": len({r["doc_id"] for r in records}),
            "docs_missing_in_db": sorted(doc_missing),
            "kps_found": len(target),
            "kps_invalid": invalid,
            "kps_created": 0,
            "kps_would_create": 0,
            "kps_existing": 0,
            "kps_name_conflict": 0,
            "kps_without_sources": 0,
            "kp_sources_added": 0,
            "kp_sources_would_add": 0,
            "name_conflicts_sample": [],
            "renamed": 0,
            "manifest_updated": 0,
            "record_failures": [],
            "elapsed_ms": 0,
        }
        _plan_impl(db_conn, target, manifest, stats)
        stats["kp_sources_would_add"] = stats["kp_sources_added"]
        if writable:
            stats["kp_sources_added"] = 0
            _apply(db_conn, target, stats, manifest)
        stats["elapsed_ms"] = int((time.perf_counter() - t0) * 1000)
        stats["manifest_size"] = len(manifest)
        return stats
    finally:
        if own_conn and db_conn is not None:
            db_conn.close()


def _apply(conn, target: list[dict], stats: dict, manifest: dict) -> None:
    """真写库：逐条 upsert（单条失败不影响其余），最后一次性落清单。"""
    gen_at = time.strftime("%Y-%m-%dT%H:%M:%S")
    for rec in target:
        if rec["_status"] not in ("create", "adopt", "existing"):
            continue
        name = rec["assigned"]
        subject = rec.get("chapter") or ""
        try:
            if rec["_status"] == "create":
                kp_id = study.upsert_kp(conn, name, summary=rec["summary"],
                                        subject=subject, chunk_ids=rec["chunk_ids"])
                stats["kps_created"] += 1
                added = len(rec["chunk_ids"])
            elif rec["_status"] == "adopt":
                kp_id = int(rec["kp_id"])
                added = _new_sources_count(conn, kp_id, rec["chunk_ids"])
                study.upsert_kp(conn, name, summary=rec["summary"],
                               subject=subject, chunk_ids=rec["chunk_ids"])
            else:  # existing：只补新增出处，不动摘要（防覆盖用户编辑）
                kp_id = int(rec["kp_id"])
                added = _new_sources_count(conn, kp_id, rec["chunk_ids"])
                if added:
                    study.upsert_kp(conn, name, summary="",
                                   subject=subject, chunk_ids=rec["chunk_ids"])
            if rec["chunk_ids"]:
                conn.execute(
                    "UPDATE kp_sources SET note=? WHERE kp_id=? AND (note IS NULL OR note='')",
                    (f"import:v1:{rec['key']}", int(kp_id)),
                )
                conn.commit()
            stats["kp_sources_added"] += max(0, added)
            manifest[rec["key"]] = {"kp_id": int(kp_id), "name": name,
                                    "artifact": rec["artifact"], "updated_at": gen_at}
        except (sqlite3.Error, ValueError, TypeError) as e:  # 单条失败不影响其余
            stats["record_failures"].append(
                {"key": rec.get("key"), "name": name, "error": str(e)})
    conn.commit()
    _write_manifest(conn, manifest)
