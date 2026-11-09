"""Local pilot for exploring links between courseware knowledge points.

This is candidate retrieval, not a medical fact extractor. Nothing is sent to a
model service, written to a database, or merged into existing knowledge points.
"""
from __future__ import annotations

import math
import hashlib
import json
import re
import sqlite3
import threading
import unicodedata
from collections import Counter, OrderedDict
from pathlib import Path

import numpy as np

from . import embed

_SUFFIX = "-末级知识点总结.json"
_MAX_CACHE = 2
_LOCK = threading.RLock()
_CACHE: OrderedDict = OrderedDict()
_STOP = frozenset({
    "作用", "影响", "主要", "包括", "可以", "通过", "以及", "其中", "一个", "一种",
    "具有", "有关", "进行", "产生", "由于", "这些", "因为", "然后", "同时",
    "不同", "情况", "使用", "临床", "作用于", "作用的", "的作用", "主要是",
    "相关", "常见", "导致", "形成", "发生", "从而", "引起", "可能", "不能",
    "of", "and", "the", "for", "are", "with", "not", "this", "from", "can",
    "na", "ca", "mg", "cl", "is", "to", "at", "in", "on",
})
_TOKEN_RE = re.compile(r"[a-zα-ω][a-zα-ω0-9\-]{1,23}|[\u3400-\u9fff]{2,}")
_TITLE_SPLIT = re.compile(
    r"(?:概述|总图|机制|机理|功能|分类|水解|消失|合成|贮存|释放|传递|"
    r"临床|药理|作用|对比|概念|结构|受体|以及|与|及|和|的|[\s、,，:：/（）()])+")


def clear_cache() -> None:
    with _LOCK:
        _CACHE.clear()


def _stat(path: Path):
    try:
        st = path.stat()
        return st.st_mtime_ns, st.st_size
    except OSError:
        return None


def _database_signature(db_path: Path):
    """Hash relevant content, not WAL timestamps, which can change on read-only opens."""
    digest = hashlib.sha256()
    conn = sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True, timeout=10)
    try:
        conn.execute("BEGIN")
        for sql in (
            "SELECT id, title, role, status FROM documents ORDER BY id",
            "SELECT c.id, c.doc_id, c.source_ref, c.text FROM chunks c "
            "JOIN documents d ON d.id=c.doc_id WHERE d.role='courseware' ORDER BY c.id",
        ):
            for row in conn.execute(sql):
                for value in row:
                    blob = str(value if value is not None else "").encode("utf-8")
                    digest.update(len(blob).to_bytes(8, "big"))
                    digest.update(blob)
    finally:
        conn.close()
    return digest.hexdigest()


def _manifest(db_path: Path, log_dir: Path):
    folder = log_dir / "knowledge"
    files = sorted(p for p in folder.glob("*" + _SUFFIX)
                   if p.name[:-len(_SUFFIX)].isdigit() and p.is_file())
    return (str(db_path.resolve()), str(folder.resolve()), _database_signature(db_path),
            embed.embed_cache_signature(),
            tuple((p.name, _stat(p)) for p in files))


def _tokens(text: str) -> set[str]:
    text = unicodedata.normalize("NFKC", str(text or "")).casefold()[:3500]
    out = set()
    for match in _TOKEN_RE.finditer(text):
        word = match.group(0)
        if "\u3400" <= word[0] <= "\u9fff":
            for n in range(2, min(6, len(word)) + 1):
                out.update(word[i:i + n] for i in range(len(word) - n + 1))
        elif word not in _STOP:
            out.add(word)
    return out.difference(_STOP)


def _point_text(point: dict) -> str:
    name = str(point.get("name") or "").strip()[:200]
    summary = str(point.get("summary") or "").strip()[:2600]
    return name + "\n" + summary


def _safe_sources(value):
    result = []
    for item in value if isinstance(value, list) else []:
        if not isinstance(item, dict):
            continue
        ids = []
        for raw in item.get("chunk_ids") if isinstance(item.get("chunk_ids"), list) else []:
            try:
                n = int(raw)
            except (TypeError, ValueError):
                continue
            if n > 0 and n not in ids:
                ids.append(n)
        if ids:
            result.append({"page": str(item.get("page") or "")[:32],
                           "chunk_ids": ids[:12], "figure_desc": bool(item.get("figure_desc"))})
    return result[:12]


def _load_records(conn, log_dir: Path, readable_versions):
    docs = {}
    for row in conn.execute(
        "SELECT id, title, role, status FROM documents WHERE role='courseware'"
    ):
        docs[int(row["id"])] = {
            "title": str(row["title"] or ""),
            "status": str(row["status"] or "ok"),
        }
    records = []
    folder = log_dir / "knowledge"
    for path in sorted(folder.glob("*" + _SUFFIX)):
        stem = path.name[:-len(_SUFFIX)]
        if not stem.isdigit() or int(stem) not in docs:
            continue
        did = int(stem)
        try:
            art = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(art, dict):
            continue
        meta = art.get("meta") if isinstance(art.get("meta"), dict) else {}
        if readable_versions and str(meta.get("pipeline_version") or "") not in readable_versions:
            continue
        structure = art.get("structure") if isinstance(art.get("structure"), dict) else {}
        if not isinstance(structure.get("root"), dict):
            continue
        seen = set()
        for point in art.get("knowledge_points") if isinstance(art.get("knowledge_points"), list) else []:
            if not isinstance(point, dict):
                continue
            kid = str(point.get("id") or "").strip()
            summary = str(point.get("summary") or "").strip()
            if not kid or not summary or kid in seen:
                continue
            seen.add(kid)
            path_names = point.get("node_path") if isinstance(point.get("node_path"), list) else []
            pages = []
            for page in point.get("pages") if isinstance(point.get("pages"), list) else []:
                try:
                    n = int(page)
                except (TypeError, ValueError):
                    continue
                if n > 0 and n not in pages:
                    pages.append(n)
            text = _point_text(point)
            records.append({
                "doc_id": did, "kp_id": kid,
                "doc_title": docs[did]["title"],
                "doc_status": docs[did]["status"],
                "name": str(point.get("name") or kid).strip()[:200],
                "summary": summary[:2600],
                "node_path": [str(x)[:200] for x in path_names[:20]],
                "pages": pages[:100],
                "sources": _safe_sources(point.get("sources")),
                "material_kinds": {
                    int(item["chunk_id"]): str(item.get("kind") or "")
                    for item in (point.get("material") or [])
                    if isinstance(item, dict) and str(item.get("chunk_id") or "").isdigit()
                },
                "insufficient": bool(point.get("insufficient")),
                "text": text, "tokens": _tokens(text),
            })
    return records


def _get_index(conn, db_path: Path, log_dir: Path, readable_versions, allow_build: bool):
    sig = (_manifest(db_path, log_dir), tuple(sorted(readable_versions or ())))
    with _LOCK:
        if sig in _CACHE:
            _CACHE.move_to_end(sig)
            return _CACHE[sig], True
        if not allow_build:
            raise ValueError("正在处理其他长任务，请等任务结束后再首次建立知识联系索引")
        records = _load_records(conn, log_dir, readable_versions)
        if not records:
            raise ValueError("当前学科还没有可用的课件知识点，请先整理课件")
        raw = embed.embed_documents_cached([point["text"] for point in records])
        vectors = np.asarray(raw, dtype=np.float32)
        if vectors.ndim != 2 or vectors.shape[0] != len(records):
            raise ValueError("本地知识点索引结果异常，请稍后重试")
        if not np.isfinite(vectors).all():
            raise ValueError("本地知识点向量无效，请检查模型")
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        vectors = vectors / np.where(norms > 0, norms, 1.0)
        df = Counter(t for point in records for t in point["tokens"])
        result = {"records": records, "vectors": vectors,
                  "lookup": {(p["doc_id"], p["kp_id"]): i for i, p in enumerate(records)},
                  "df": df, "signature": sig}
        if _manifest(db_path, log_dir) != sig[0]:
            raise ValueError("资料在建立索引时发生变化，请再试一次")
        _CACHE[sig] = result
        while len(_CACHE) > _MAX_CACHE:
            _CACHE.popitem(last=False)
        return result, False


def _informative_terms(index, left: int, right: int):
    records, df = index["records"], index["df"]
    n = len(records)
    common = records[left]["tokens"] & records[right]["tokens"]
    terms = [term for term in common if 2 <= df.get(term, 0) <= max(2, int(n * 0.45))]
    terms.sort(key=lambda t: (-(len(t) * math.log((n + 1) / (df[t] + 1))), -len(t), t))
    chosen = []
    for term in terms:
        if any(term in kept or kept in term for kept in chosen):
            continue
        chosen.append(term)
        if len(chosen) >= 6:
            break
    return chosen


def _lexical_score(index, left: int, right: int):
    terms = _informative_terms(index, left, right)
    if not terms:
        return 0.0
    n = len(index["records"])
    df = index["df"]
    return sum(min(len(t), 6) * math.log((n + 1) / (df[t] + 1)) for t in terms)


def _display_terms(index, left: int, right: int):
    """Show exact title terms and abbreviations, not arbitrary character fragments."""
    a, b = index["records"][left], index["records"][right]
    left_text = unicodedata.normalize("NFKC", a["text"]).casefold()
    right_text = unicodedata.normalize("NFKC", b["text"]).casefold()
    common = []
    for title in (a["name"], b["name"]):
        for value in _TITLE_SPLIT.split(unicodedata.normalize("NFKC", title)):
            term = value.strip()
            key = term.casefold()
            if (len(key) >= 3 and key not in _STOP and key in left_text and key in right_text
                    and not any(key == old.casefold() for old in common)):
                common.append(term)
    left_latin = {m.group().casefold(): m.group() for m in re.finditer(r"[A-Za-zα-ωΑ-Ω][A-Za-zα-ωΑ-Ω0-9-]{1,23}", a["text"])}
    right_latin = {m.group().casefold() for m in re.finditer(r"[A-Za-zα-ωΑ-Ω][A-Za-zα-ωΑ-Ω0-9-]{1,23}", b["text"])}
    for key in sorted(left_latin.keys() & right_latin):
        if key not in _STOP and not any(key == old.casefold() for old in common):
            common.append(left_latin[key])
    return common[:4]


def _rank(index, query_i: int, candidates: list[int]):
    if not candidates:
        return []
    sims = index["vectors"] @ index["vectors"][query_i]
    # Low similarity is dropped even when generic terms overlap. These are
    # heuristic retrieval thresholds, not calibrated confidence values.
    candidates = [i for i in candidates if float(sims[i]) >= 0.45]
    sem = sorted(candidates, key=lambda i: (-float(sims[i]), i))
    lex_scores = {i: _lexical_score(index, query_i, i) for i in candidates}
    lex = sorted((i for i in candidates if lex_scores[i] > 0),
                 key=lambda i: (-lex_scores[i], i))
    fused = {i: 1.0 / (60 + rank) for rank, i in enumerate(sem, 1)}
    for rank, i in enumerate(lex, 1):
        fused[i] += 0.65 / (60 + rank)
    return sorted(candidates, key=lambda i: (-fused[i], -float(sims[i]), i))


def _same_parent(left, right):
    a, b = left["node_path"], right["node_path"]
    return len(a) >= 3 and len(b) >= 3 and a[:-1] == b[:-1]


def _evidence(conn, records):
    requests = []
    ids = set()
    for point in records:
        for source in point["sources"]:
            for cid in source["chunk_ids"]:
                ids.add(cid)
    rows = {}
    # SQLite builds may have a 999-parameter limit.
    chunks = sorted(ids)
    for start in range(0, len(chunks), 400):
        part = chunks[start:start + 400]
        if part:
            sql = ("SELECT id, doc_id, source_ref, text FROM chunks WHERE id IN ("
                   + ",".join("?" for _ in part) + ")")
            rows.update({int(row["id"]): row for row in conn.execute(sql, part)})
    for point in records:
        found = []
        seen = set()
        for source in point["sources"]:
            for cid in source["chunk_ids"]:
                row = rows.get(cid)
                if row is None or int(row["doc_id"]) != point["doc_id"] or cid in seen:
                    continue
                ref = str(row["source_ref"] or "")
                text = re.sub(r"\s+", " ", str(row["text"] or "")).strip()
                if not text:
                    continue
                seen.add(cid)
                kind = point["material_kinds"].get(cid, "")
                is_image = (kind == "图片理解" or (not kind and
                            (source["figure_desc"] or "图" in ref)))
                found.append({"chunk_id": cid, "page": ref,
                              "excerpt": text[:220] + ("…" if len(text) > 220 else ""),
                              "image_description": is_image,
                              "sparse": len(text) < 30})
                if len(found) >= 2:
                    break
            if len(found) >= 2:
                break
        requests.append(found)
    return requests


def _public_point(point, evidence):
    result = {
        "doc_id": point["doc_id"], "kp_id": point["kp_id"],
        "doc_title": point["doc_title"], "name": point["name"],
        "summary": point["summary"][:380], "pages": point["pages"],
        "insufficient": point["insufficient"],
        "doc_partial": point["doc_status"] != "ok",
        "evidence": evidence,
        "evidence_note": ("出处尚不足以核对，请查看原课件" if not evidence
                          else "仅核对来源归属，具体联系仍需结合原文判断"),
    }
    encoded = json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    result["version"] = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    return result


def find_links(doc_id: int, kp_id: str, *, db_path, log_dir,
               readable_versions=None, allow_build=True, limit=6):
    """Return local candidates and structural bridges within one subject."""
    did, kid = int(doc_id or 0), str(kp_id or "").strip()
    if did <= 0 or not kid or len(kid) > 200:
        raise ValueError("请选择课件中的知识点")
    db_path, log_dir = Path(db_path), Path(log_dir)
    conn = sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("BEGIN")
        index, reused = _get_index(conn, db_path, log_dir, readable_versions, allow_build)
        current = index["lookup"].get((did, kid))
        if current is None:
            raise ValueError("没有找到这条已完成的课件知识点，请刷新后重试")
        records = index["records"]
        other = [i for i, p in enumerate(records) if p["doc_id"] != did]
        selected, per_doc = [], Counter()
        for i in _rank(index, current, other):
            if per_doc[records[i]["doc_id"]] >= 2:
                continue
            selected.append(i)
            per_doc[records[i]["doc_id"]] += 1
            if len(selected) >= max(1, min(int(limit), 8)):
                break

        # Same-parent nodes are *structural* bridges, never causal assertions.
        siblings = [i for i, p in enumerate(records)
                    if i != current and p["doc_id"] == did and _same_parent(records[current], p)]
        siblings = _rank(index, current, siblings)[:5]
        bridge_pool = []
        used = set(selected)
        bridge_per_doc = Counter()
        for sibling in siblings:
            for rank, target in enumerate(_rank(index, sibling, other)[:12], 1):
                if target in used:
                    continue
                if not _informative_terms(index, sibling, target):
                    continue
                via = float(index["vectors"][sibling] @ index["vectors"][target])
                direct = float(index["vectors"][current] @ index["vectors"][target])
                gain = via - direct
                if gain < 0.025:
                    continue
                score = (0.20 * float(index["vectors"][current] @ index["vectors"][sibling])
                         + 0.55 * via + 0.50 * gain - rank * 0.002)
                bridge_pool.append((score, sibling, target))
        bridge_pairs, per_bridge = [], Counter()
        for _, sibling, target in sorted(bridge_pool, reverse=True):
            if (target in used or per_bridge[sibling] >= 2
                    or bridge_per_doc[records[target]["doc_id"]] >= 2):
                continue
            bridge_pairs.append((sibling, target))
            used.add(target)
            per_bridge[sibling] += 1
            bridge_per_doc[records[target]["doc_id"]] += 1
            if len(bridge_pairs) >= 3:
                break

        unique = list(dict.fromkeys([current] + selected +
                      [x for pair in bridge_pairs for x in pair]))
        evidence_lists = _evidence(conn, [records[i] for i in unique])
        evidence = dict(zip(unique, evidence_lists))
        direct = []
        for i in selected:
            item = _public_point(records[i], evidence[i])
            item["shared_terms"] = _display_terms(index, current, i)
            direct.append(item)
        bridges = []
        for sibling, target in bridge_pairs:
            item = _public_point(records[target], evidence[target])
            item["bridge"] = _public_point(records[sibling], evidence[sibling])
            item["shared_terms"] = _display_terms(index, sibling, target)
            bridges.append(item)
        if _manifest(db_path, log_dir) != index["signature"][0]:
            raise ValueError("资料在查询期间发生变化，请再试一次")
        return {"ok": True, "kind": "local_pilot", "indexed_points": len(records),
                "reused": reused, "source": _public_point(records[current], evidence[current]),
                "direct": direct, "bridges": bridges}
    finally:
        conn.close()

