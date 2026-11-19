# Copyright (c) 2026 潜声海豚 <2227663973@qq.com>. All rights reserved.
# File-ID: SA-LAB-KP-VECTOR-01
"""Knowledge-point-only vector index.

This mirrors the ingestion vector representation (SQLite + float32 BLOB + the
shared local ONNX embedder) while keeping knowledge summaries out of the chunk
embedding table. The index is derived from ``*-末级知识点总结.json`` artifacts;
rebuilding it never calls a remote model.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
from pathlib import Path

import numpy as np

from . import config, db, embed


KP_SUFFIX = "-末级知识点总结.json"


def _as_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        return "\n".join(x for x in (_as_text(v) for v in value) if x)
    if isinstance(value, dict):
        # Keep meaningful markdown/formula strings while tolerating structured
        # point objects produced by older artifact versions.
        for key in ("text", "content", "point", "name", "title"):
            val = _as_text(value.get(key))
            if val:
                return val
        return ""
    return str(value).strip()


def _record_uid(item: dict, name: str, pages: list) -> str:
    uid = str(item.get("id") or item.get("uid") or "").strip()
    if uid:
        return uid[:240]
    seed = json.dumps([name, pages], ensure_ascii=False, separators=(",", ":"))
    return "kp-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:32]


def _artifact_records(raw: dict, path: Path) -> tuple[int, list[dict], int]:
    match = re.match(r"^(\d+)" + re.escape(KP_SUFFIX) + r"$", path.name)
    meta = raw.get("meta") if isinstance(raw.get("meta"), dict) else {}
    raw_doc_id = match.group(1) if match else (meta.get("doc_id") or raw.get("doc_id"))
    try:
        doc_id = int(raw_doc_id)
    except (TypeError, ValueError):
        raise ValueError(f"无法从文件名或产物 meta 确认 doc_id：{path.name}") from None
    if doc_id <= 0:
        raise ValueError(f"产物 doc_id 不合法：{raw_doc_id!r}")
    points = raw.get("knowledge_points")
    if not isinstance(points, list):
        raise ValueError("知识点总结产物缺少 knowledge_points 列表")

    records: list[dict] = []
    skipped = 0
    used_uids: set[str] = set()
    for item in points:
        if not isinstance(item, dict):
            skipped += 1
            continue
        name = _as_text(item.get("name") or item.get("title"))
        summary = _as_text(item.get("summary") or item.get("content"))
        if not name or not summary:
            skipped += 1
            continue
        detail = _as_text(item.get("points") or item.get("key_points"))
        node_path = _as_text(item.get("node_path") or item.get("path"))
        search_text = "\n".join(x for x in (name, summary, detail, node_path) if x)
        pages = item.get("pages") if isinstance(item.get("pages"), list) else []
        pages = [int(p) for p in pages if str(p).isdigit()]
        uid = _record_uid(item, name, pages)
        digest = hashlib.sha256(search_text.encode("utf-8")).hexdigest()
        if uid in used_uids:
            uid = f"{uid}-{hashlib.sha256((digest + json.dumps(pages)).encode('utf-8')).hexdigest()[:12]}"
        while uid in used_uids:
            uid += "-x"
        used_uids.add(uid)
        records.append({
            "kp_uid": uid,
            "name": name,
            "summary": summary,
            "search_text": search_text,
            "pages": json.dumps(pages, ensure_ascii=False, separators=(",", ":")),
            "content_hash": digest,
            "source_file": path.name,
        })
    return doc_id, records, skipped


def sync_artifact(doc_id: int, artifact_path: str | Path, *,
                  db_path: str | Path | None = None, embedder=None) -> dict:
    """Idempotently index one completed courseware KP artifact.

    Existing vectors with an unchanged summary/model are reused. New/changed
    vectors are computed before opening the write transaction so ONNX work does
    not hold SQLite's writer lock. A failed embedding leaves the old index intact.
    """
    path = Path(artifact_path)
    if not path.is_file():
        return {"status": "missing_artifact", "doc_id": int(doc_id), "indexed": 0}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"无法读取知识点产物 {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError("知识点总结产物顶层必须是 JSON object")
    artifact_doc_id, records, skipped = _artifact_records(raw, path)
    if int(doc_id) != artifact_doc_id:
        raise ValueError(f"doc_id 不一致：参数 {doc_id}，产物文件 {artifact_doc_id}")

    target_db = Path(db_path or config.DB_PATH)
    conn = db.connect(target_db)
    try:
        db.init_db(conn)
        if not conn.execute("SELECT 1 FROM documents WHERE id=?", (int(doc_id),)).fetchone():
            return {"status": "missing_document", "doc_id": int(doc_id),
                    "indexed": 0, "skipped": skipped}

        model = str(config.EMBED_MODEL_REPO)
        existing_rows = conn.execute(
            "SELECT kp_uid, content_hash, model, dim FROM knowledge_point_embeddings "
            "WHERE doc_id=?", (int(doc_id),)
        ).fetchall()
        existing = {str(r["kp_uid"]): r for r in existing_rows}
        emb = embedder or embed.get_embedder()
        dim = int(getattr(emb, "dim", config.EMBED_DIM))
        pending = [r for r in records if not (
            (old := existing.get(r["kp_uid"])) is not None
            and str(old["content_hash"]) == r["content_hash"]
            and str(old["model"]) == model and int(old["dim"]) == dim
        )]

        vectors: dict[str, np.ndarray] = {}
        if pending:
            matrix = np.asarray(embed.embed_documents_cached(
                [r["search_text"] for r in pending], embedder=emb), dtype=np.float32)
            if matrix.ndim != 2 or matrix.shape != (len(pending), dim):
                raise ValueError(f"嵌入器结果形状异常：{matrix.shape}，预期 {(len(pending), dim)}")
            if not np.isfinite(matrix).all():
                raise ValueError("嵌入器返回了 NaN/Inf 向量")
            norms = np.linalg.norm(matrix, axis=1, keepdims=True)
            matrix = matrix / np.where(norms > 0, norms, 1.0)
            vectors = {r["kp_uid"]: matrix[i] for i, r in enumerate(pending)}

        # All model work is done; reconcile the document's index atomically.
        now = time.time()
        conn.execute("BEGIN IMMEDIATE")
        keep = {r["kp_uid"] for r in records}
        stale = sorted(set(existing) - keep)
        conn.executemany(
            "DELETE FROM knowledge_point_embeddings WHERE doc_id=? AND kp_uid=?",
            [(int(doc_id), uid) for uid in stale],
        )

        for r in records:
            vec = vectors.get(r["kp_uid"])
            if vec is None:
                # Unchanged row: update display metadata without rewriting BLOB.
                conn.execute(
                    "UPDATE knowledge_point_embeddings SET name=?, summary=?, search_text=?, "
                    "pages=?, source_file=?, updated_at=? WHERE doc_id=? AND kp_uid=?",
                    (r["name"], r["summary"], r["search_text"], r["pages"],
                     r["source_file"], now, int(doc_id), r["kp_uid"]),
                )
                continue
            conn.execute(
                "INSERT INTO knowledge_point_embeddings(doc_id,kp_uid,name,summary,search_text,"
                "pages,content_hash,model,dim,vec,source_file,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(doc_id,kp_uid) DO UPDATE SET name=excluded.name,"
                "summary=excluded.summary,search_text=excluded.search_text,pages=excluded.pages,"
                "content_hash=excluded.content_hash,model=excluded.model,dim=excluded.dim,"
                "vec=excluded.vec,source_file=excluded.source_file,updated_at=excluded.updated_at",
                (int(doc_id), r["kp_uid"], r["name"], r["summary"], r["search_text"],
                 r["pages"], r["content_hash"], model, dim,
                 embed.to_blob(vec), r["source_file"], now, now),
            )
        conn.commit()
        return {"status": "ok", "doc_id": int(doc_id), "indexed": len(records),
                "embedded": len(pending), "removed": len(stale),
                "skipped": skipped,
                "model": model, "dim": dim}
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def search(query: str, *, top_k: int = 10, doc_id: int | None = None,
           db_path: str | Path | None = None, embedder=None) -> list[dict]:
    """Return nearest stored summaries by cosine similarity (normalized dot product)."""
    query = str(query or "").strip()
    if not query:
        return []
    emb = embedder or embed.get_embedder()
    q = np.asarray(emb.embed_query(query), dtype=np.float32).reshape(-1)
    norm = float(np.linalg.norm(q))
    if norm:
        q = q / norm
    model = str(config.EMBED_MODEL_REPO)
    dim = int(q.shape[0])
    conn = db.connect(db_path or config.DB_PATH)
    try:
        db.init_db(conn)
        sql = ("SELECT doc_id,kp_uid,name,summary,pages,model,dim,vec "
               "FROM knowledge_point_embeddings WHERE model=? AND dim=?")
        args: list = [model, dim]
        if doc_id is not None:
            sql += " AND doc_id=?"
            args.append(int(doc_id))
        hits = []
        for row in conn.execute(sql, args):
            vec = np.frombuffer(row["vec"], dtype=np.float32)
            if vec.size != dim:
                continue
            score = float(np.dot(q, vec))
            hits.append({"doc_id": int(row["doc_id"]), "kp_uid": row["kp_uid"],
                         "name": row["name"], "summary": row["summary"],
                         "pages": json.loads(row["pages"] or "[]"), "score": score})
        hits.sort(key=lambda item: item["score"], reverse=True)
        return hits[:max(1, int(top_k))]
    finally:
        conn.close()


def search_many(queries: list[str], *, top_k: int = 1,
                doc_id: int | None = None,
                db_path: str | Path | None = None,
                embedder=None) -> list[list[dict]]:
    """Batch-search multiple queries against the stored knowledge-point vectors.

    Query embeddings are generated together, then compared to one snapshot of the
    requested vector index. The return list stays aligned with ``queries``; each
    element contains up to ``top_k`` hits ordered by cosine similarity.
    """
    values = [str(query or "").strip() for query in queries]
    results: list[list[dict]] = [[] for _ in values]
    positions = [i for i, query in enumerate(values) if query]
    if not positions:
        return results

    emb = embedder or embed.get_embedder()
    active_queries = [values[i] for i in positions]
    if hasattr(emb, "embed_queries"):
        # Avoid one very large ONNX batch when a textbook chapter has many
        # terms; keep memory use similar to the indexer's document batch size.
        q_matrix = np.vstack([
            np.asarray(emb.embed_queries(active_queries[start:start + 16]), dtype=np.float32)
            for start in range(0, len(active_queries), 16)
        ])
    else:
        q_matrix = np.vstack([emb.embed_query(query) for query in active_queries])
    if q_matrix.ndim != 2 or q_matrix.shape[0] != len(positions):
        raise ValueError("查询嵌入器返回了异常的向量形状")
    q_norms = np.linalg.norm(q_matrix, axis=1, keepdims=True)
    q_matrix = q_matrix / np.where(q_norms > 0, q_norms, 1.0)

    model = str(config.EMBED_MODEL_REPO)
    dim = int(q_matrix.shape[1])
    conn = db.connect(db_path or config.DB_PATH)
    try:
        db.init_db(conn)
        sql = ("SELECT doc_id,kp_uid,name,summary,pages,model,dim,vec "
               "FROM knowledge_point_embeddings WHERE model=? AND dim=?")
        args: list = [model, dim]
        if doc_id is not None:
            sql += " AND doc_id=?"
            args.append(int(doc_id))
        rows = conn.execute(sql, args).fetchall()
        records, vectors = [], []
        for row in rows:
            vec = np.frombuffer(row["vec"], dtype=np.float32)
            if vec.size != dim or not np.isfinite(vec).all():
                continue
            norm = float(np.linalg.norm(vec))
            if norm:
                vec = vec / norm
            records.append(row)
            vectors.append(vec)
        if not records:
            return results

        matrix = np.vstack(vectors)
        scores = q_matrix @ matrix.T
        limit = max(1, int(top_k))
        for q_index, original_index in enumerate(positions):
            row_scores = scores[q_index]
            count = min(limit, len(records))
            # Sorting descending without a full sort when callers only need the
            # best match; stable ordering makes ties deterministic.
            if count < len(records):
                candidates = np.argpartition(-row_scores, count - 1)[:count]
            else:
                candidates = np.arange(len(records))
            order = sorted(candidates.tolist(), key=lambda i: (-float(row_scores[i]), i))
            hits = []
            for index in order:
                row = records[index]
                hits.append({"doc_id": int(row["doc_id"]), "kp_uid": row["kp_uid"],
                             "name": row["name"], "summary": row["summary"],
                             "pages": json.loads(row["pages"] or "[]"),
                             "score": float(row_scores[index])})
            results[original_index] = hits
        return results
    finally:
        conn.close()
