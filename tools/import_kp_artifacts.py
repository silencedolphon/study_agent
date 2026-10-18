#!/usr/bin/env python
# Copyright (c) 2026 潜声海豚 <2227663973@qq.com>. All rights reserved.
# 本源码为著作权人私有作品，适用项目根目录 LICENSE（专有许可）。
"""整理产物 → 知识点表 一键导入（M0-3：零 AI 成本、幂等）。

## 这个脚本解决什么问题

`core/kp_build.py` 的「整理知识点」**只写产物文件**
`logs/knowledge/<doc_id>-末级知识点总结.json`，**从不写数据库**。
于是真实站点上「整理完看不见、复习队列永远空」：
`knowledge_points` 表 = 0，而产物里躺着 250+ 条现成的知识点。

本脚本把产物**离线**解析进 `knowledge_points` + `kp_sources`：
不调模型、不联网、不花一分钱，只 import 现有的 `core.db` / `core.study`，
不改任何产品代码。

## 幂等怎么保证（同一份产物导 100 次也只建一次卡）

1. **稳定键** = `<doc_id>:<产物内知识点 id>`（如 `41:KP-N03-1`）。
   产物 `id` 只在单份课件内唯一（真实 9 份产物里 `KP-N03-1` 出现 5 次），
   所以**必须**带 doc_id —— 这就是「doc_id + 末级节点稳定 id」的落点。
2. **名称唯一**：`knowledge_points.name` 是全局 UNIQUE，而不同课件会出现同名
   末级节点（真实产物 3 组，如「临床应用」×3、「课程导入」×2，同一课件内也有
   1 组重名）。本脚本先按产物内完整路径逐级加父级前缀（`父级 · 名称`，与旧版
   `display` 字段同一文案习惯）直到在本次待导入集合里唯一；若仍与库里**非本脚本
   导入**的同名知识点冲突，**跳过并计数**，绝不覆盖用户数据。
3. **导入清单**落 `meta` 表（key=`kp_import.artifacts.v1`，JSON）。二次运行先按
   清单 kp_id 命中 → 已存在；清单丢失时退回按「名称 + 摘要完全一致」认领，
   因此即使清单被清掉也不会重复建卡。
4. 出处 `kp_sources` 主键 `(kp_id, chunk_id)`，重复插入天然不重复。

## 知识点内容怎么落（真实库表结构没有 extra 列）

`knowledge_points` 只有 name/summary/subject/... 五列，没有 `extra`。所以：

- `name`：末级节点名（必要时加父级前缀，见上）；
- `summary`：产物 `summary`（一段理解）+ `要点：…`（`points` 逐条用「；」连接）。
  看板 `api_kp_detail` 直接把 summary 展示在详情里，要点不会丢；
- `subject`：产物 `meta.chapter`（课件章节名）——根库没有学科概念，用它做分组；
- 出处：优先产物**显式** `sources[].chunk_ids` / `material_block_ids`（管线真读过
  的材料块，最精确）；显式没有才按「知识点覆盖页 ∩ 该课件 chunks 的 source_ref
  页范围」兜底（真实库三种形态：`p.12` / `p.12-16` / `p.12 图`）；
- provenance：`kp_sources.note` 写 `import:v1:<doc_id>:<知识点 id>`，查库可回链。

## 用法

```
# 默认 dry-run：只预览，不写库（sqlite 只读连接，连 WAL 都不动）
venv\\Scripts\\python.exe -X utf8 tools\\import_kp_artifacts.py

# 真写库（默认 config.DB_PATH；测试一律用副本）
venv\\Scripts\\python.exe -X utf8 tools\\import_kp_artifacts.py --apply

# 指定副本库 / 产物目录 / 只导一份 / 机器可读输出
venv\\Scripts\\python.exe -X utf8 tools\\import_kp_artifacts.py --apply ^
    --db _work\\m0\\import\\real-copy.db --artifacts-dir logs\\knowledge --doc-id 41 --json
```

退出码：0 = 跑完（单份产物坏只记 `failed_files` 并继续；单条知识点失败也继续）；
2 = 前置不满足（库不存在 / 产物目录不存在 / 缺表）。
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import time
from pathlib import Path

# tools/ 下脚本统一从项目根 import core（与 tools/study.py 同一条路）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core import config, db, study  # noqa: E402

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


class Fatal(Exception):
    """前置条件不满足（退出码 2），与「单份产物坏但继续」区分开。"""


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
        raise Fatal(f"产物目录读不了: {artifacts_dir}（{e}）") from e
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


def run(args) -> dict:
    t0 = time.perf_counter()
    artifacts_dir = Path(args.artifacts_dir).resolve()
    db_path = Path(args.db).resolve()
    if not artifacts_dir.is_dir():
        raise Fatal(f"产物目录不存在: {artifacts_dir}")
    if not db_path.is_file():
        raise Fatal(f"数据库不存在: {db_path}（测试请先复制副本）")

    scan = _scan_artifacts(artifacts_dir)
    want_doc_ids = set(args.doc_id or [])
    writable = bool(args.apply)
    conn = db.connect(db_path) if writable else _connect_readonly(db_path)
    try:
        if writable:
            db.init_db(conn)  # 空库/新库补齐 schema；真实库本来就齐（幂等）
        tables = _table_names(conn)
        if "documents" not in tables or "chunks" not in tables:
            raise Fatal(f"库里没有 documents/chunks 表，不像是学习库: {db_path}")
        if "knowledge_points" not in tables or "kp_sources" not in tables:
            if not writable:
                raise Fatal("库里还没有知识点表（空库）；先 --apply 一次会建表")
            db.init_db(conn)

        docs = {int(r["id"]): dict(r) for r in
                conn.execute("SELECT id, title FROM documents").fetchall()}
        doc_chunks = _load_doc_chunks(conn, sorted(docs))
        manifest = _read_manifest(conn)

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
                                                args.max_sources_per_kp)
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
        _plan_impl(conn, target, manifest, stats)
        stats["kp_sources_would_add"] = stats["kp_sources_added"]
        if writable:
            stats["kp_sources_added"] = 0
            _apply(conn, target, stats, manifest)
        stats["elapsed_ms"] = int((time.perf_counter() - t0) * 1000)
        stats["manifest_size"] = len(manifest)
        return stats
    finally:
        conn.close()


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


def _human(stats: dict) -> str:
    is_apply = stats["mode"] == "apply"
    mode = "apply（真写库）" if is_apply else "dry-run（预览，不写库）"
    created = stats["kps_created"] if is_apply else stats["kps_would_create"]
    verb = "已创建" if is_apply else "将创建"
    src_done = stats["kp_sources_added"] if is_apply else stats["kp_sources_would_add"]
    lines = [
        f"整理产物 → 知识点表导入 · {mode}",
        f"  数据库  : {stats['db']}",
        f"  产物目录: {stats['artifacts_dir']}",
        f"  扫描 {stats['scanned_files']} 个产物文件，解析成功 {stats['parsed_files']}，失败 {len(stats['failed_files'])}",
        f"  命中库内文档 {stats['docs_matched']} 份；待导入知识点 {stats['kps_found']} 条",
        f"  {verb} {created}；已存在跳过 {stats['kps_existing']}；名称冲突跳过 {stats['kps_name_conflict']}；无效 {stats['kps_invalid']}",
        f"  出处 kp_sources {'已写入' if is_apply else '将写入'} {src_done} 条；无出处知识点 {stats['kps_without_sources']} 条",
        f"  名称避让 {stats['renamed']} 条；耗时 {stats['elapsed_ms']} ms",
    ]
    if stats["docs_missing_in_db"]:
        lines.append(f"  产物对应文档已不在库（跳过）: {stats['docs_missing_in_db']}")
    for f in stats["failed_files"]:
        lines.append(f"  ✗ {f['file']}: {f['error']}")
    if stats["name_conflicts_sample"]:
        lines.append("  名称冲突样例: " + "、".join(
            f"{x['name']}(doc{x['doc_id']})" for x in stats["name_conflicts_sample"]))
    for f in stats["record_failures"][:10]:
        lines.append(f"  ✗ {f['key']}: {f['error']}")
    if not is_apply:
        lines.append("（预览不写库；确认无误后加 --apply）")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="把 logs/knowledge 整理产物导入 knowledge_points + kp_sources（零 AI、幂等）")
    ap.add_argument("--apply", action="store_true",
                    help="真写库；不带此参数 = dry-run 只预览（默认）")
    ap.add_argument("--dry-run", action="store_true", help="显式指定预览（默认行为）")
    ap.add_argument("--db", default=str(config.DB_PATH),
                    help=f"目标 SQLite 库（默认 {config.DB_PATH}；测试请用副本）")
    ap.add_argument("--artifacts-dir", default=str(Path(config.LOG_DIR) / "knowledge"),
                    help="整理产物目录（默认 logs/knowledge）")
    ap.add_argument("--doc-id", default="",
                    help="只导入指定 doc_id（可逗号分隔多个，如 41,42）")
    ap.add_argument("--max-sources-per-kp", type=int, default=DEFAULT_MAX_SOURCES,
                    help=f"单条知识点最多挂几条出处（默认 {DEFAULT_MAX_SOURCES}）")
    ap.add_argument("--json", action="store_true", help="输出机器可读 JSON 摘要")
    args = ap.parse_args(argv)

    try:
        args.doc_id = sorted({int(x) for x in
                              re.split(r"[,，\s]+", str(args.doc_id or "")) if x.strip()})
    except ValueError:
        print("--doc-id 需要整数（可逗号分隔）", file=sys.stderr)
        return 2
    if args.apply and args.dry_run:
        print("--apply 与 --dry-run 不能同时使用", file=sys.stderr)
        return 2

    try:
        stats = run(args)
    except Fatal as e:
        print(f"错误: {e}", file=sys.stderr)
        return 2
    except sqlite3.Error as e:
        print(f"数据库错误: {e}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(stats, ensure_ascii=False, indent=2))
    else:
        print(_human(stats))
    return 0


if __name__ == "__main__":
    sys.exit(main())
