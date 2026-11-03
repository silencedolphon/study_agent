"""Repair contradictory visual no-info flags from already-paid local cache.

The default is read-only planning. Applying requires a new SQLite backup and
uses one checked transaction; no vision or language-model requests are made.
"""
from __future__ import annotations

from contextlib import closing
import hashlib
import json
import sqlite3
import time
from pathlib import Path

from . import config, db, figures, ingest


class VisualRepairError(RuntimeError):
    """A precondition failed; no repair transaction was committed."""


def _connect(path: Path, *, readonly: bool):
    path = Path(path).resolve()
    if not path.is_file():
        raise VisualRepairError("数据库不存在: %s" % path)
    if readonly:
        conn = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=30)
    else:
        conn = sqlite3.connect(str(path), timeout=30, isolation_level=None)
        conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    return conn


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def plan_repairs(conn: sqlite3.Connection, doc_ids) -> list[dict]:
    """Return internal plans for exact, currently misclassified page chunks."""
    plans = []
    for did in sorted({int(x) for x in doc_ids}):
        doc = conn.execute("SELECT id,role,content_hash FROM documents WHERE id=?", (did,)).fetchone()
        if doc is None:
            raise VisualRepairError("文档不存在: %d" % did)
        if str(doc["role"] or "courseware") != "courseware":
            raise VisualRepairError("只支持课件整页切块，拒绝修改文档 %d" % did)
        rows = conn.execute(
            "SELECT page,description,no_info FROM figure_desc "
            "WHERE doc_id=? AND no_info=1 ORDER BY page", (did,)).fetchall()
        for row in rows:
            raw = str(row["description"] or "").strip()
            if figures.NO_INFO_MARKER not in raw or figures.is_no_info(raw):
                continue
            clean = figures.clean_contradictory_no_info(raw)
            page = int(row["page"])
            chunks = conn.execute(
                "SELECT c.id,c.text,c.heading_path,e.model,e.dim,e.vec "
                "FROM chunks c LEFT JOIN embeddings e ON e.chunk_id=c.id "
                "WHERE c.doc_id=? AND c.source_ref=? ORDER BY c.id",
                (did, "p.%d" % page)).fetchall()
            if len(chunks) != 1:
                raise VisualRepairError("doc#%d p.%d 有 %d 个页面块，无法安全修复" %
                                        (did, page, len(chunks)))
            ch = chunks[0]
            old = str(ch["text"] or "")
            if ingest.PAGE_CHUNK_IMAGE_HEAD in old and clean not in old:
                raise VisualRepairError("doc#%d p.%d 已有其他图文段，拒绝自动追加" % (did, page))
            if ch["model"] != config.EMBED_MODEL_REPO or int(ch["dim"] or 0) != int(config.EMBED_DIM):
                raise VisualRepairError("doc#%d p.%d 的现有向量模型与当前模型不匹配" % (did, page))
            if not ch["vec"] or len(ch["vec"]) != int(config.EMBED_DIM) * 4:
                raise VisualRepairError("doc#%d p.%d 缺少完整向量，拒绝自动修复" % (did, page))
            updated = old if clean in old else (old + "\n\n" + ingest.PAGE_CHUNK_IMAGE_HEAD + "\n" + clean).strip()
            heading = str(ch["heading_path"] or "")
            plans.append({"doc_id": did, "page": page, "chunk_id": int(ch["id"]),
                          "document_hash": str(doc["content_hash"] or ""),
                          "old_text": old, "new_text": updated, "description": raw,
                          "heading": heading, "old_vector_hash": hashlib.sha256(ch["vec"]).hexdigest(),
                          "added_chars": len(updated) - len(old)})
    return plans


def public_plan(plans: list[dict]) -> list[dict]:
    return [{"doc_id": p["doc_id"], "page": p["page"], "chunk_id": p["chunk_id"],
             "added_chars": p["added_chars"], "old_text_sha256": _digest(p["old_text"]),
             "new_text_sha256": _digest(p["new_text"])} for p in plans]


def backup_database(path: Path, backup_path: Path) -> None:
    source_path, target_path = Path(path).resolve(), Path(backup_path).resolve()
    if source_path == target_path:
        raise VisualRepairError("备份路径不能是原数据库")
    target_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with target_path.open("xb"):
            pass
    except FileExistsError:
        raise VisualRepairError("备份文件已存在，拒绝覆盖: %s" % target_path) from None
    try:
        with closing(_connect(source_path, readonly=True)) as src, \
             closing(sqlite3.connect(str(target_path), timeout=30)) as dst:
            src.backup(dst)
            dst.commit()
        with closing(_connect(target_path, readonly=True)) as check:
            if check.execute("PRAGMA quick_check").fetchall()[0][0] != "ok":
                raise VisualRepairError("数据库备份未通过 quick_check")
    except BaseException:
        # The file was created exclusively by this invocation.
        try:
            target_path.unlink()
        except OSError:
            pass
        raise


def repair_database(db_path, doc_ids, *, apply: bool = False, backup_path=None,
                    embedder=None) -> dict:
    """Plan or atomically apply a local visual-cache repair; never call a remote model."""
    path = Path(db_path).resolve()
    with closing(_connect(path, readonly=True)) as conn:
        plans = plan_repairs(conn, doc_ids)
    result = {"ok": True, "applied": False, "pages": public_plan(plans),
              "count": len(plans), "model_calls": 0}
    if not apply or not plans:
        return result
    if not backup_path:
        raise VisualRepairError("实际修复必须提供一个新的 SQLite 备份路径")

    # Compute embeddings outside the write transaction; abort on any mismatch.
    from .embed import get_embedder, to_blob
    import numpy as np
    engine = embedder or get_embedder()
    texts = [(p["heading"] + "\n" + p["new_text"] if p["heading"] else p["new_text"])
             for p in plans]
    vecs = engine.embed_documents(texts)
    if tuple(vecs.shape) != (len(plans), int(config.EMBED_DIM)) or not np.isfinite(vecs).all():
        raise VisualRepairError("本地嵌入结果维度或数值无效；未写数据库")
    backup_database(path, Path(backup_path))

    conn = _connect(path, readonly=False)
    changed_docs: dict[int, list[int]] = {}
    try:
        conn.execute("BEGIN IMMEDIATE")
        for p in plans:
            current = conn.execute(
                "SELECT d.content_hash,c.text,c.heading_path,e.model,e.dim,e.vec,f.description,f.no_info "
                "FROM documents d JOIN chunks c ON c.doc_id=d.id "
                "JOIN embeddings e ON e.chunk_id=c.id "
                "JOIN figure_desc f ON f.doc_id=d.id AND f.page=? "
                "WHERE d.id=? AND c.id=? AND c.source_ref=?",
                (p["page"], p["doc_id"], p["chunk_id"], "p.%d" % p["page"])).fetchone()
            if (current is None or str(current["content_hash"] or "") != p["document_hash"]
                    or str(current["text"] or "") != p["old_text"]
                    or str(current["heading_path"] or "") != p["heading"]
                    or str(current["description"] or "").strip() != p["description"]
                    or int(current["no_info"] or 0) != 1
                    or current["model"] != config.EMBED_MODEL_REPO
                    or int(current["dim"] or 0) != int(config.EMBED_DIM)
                    or hashlib.sha256(current["vec"] or b"").hexdigest() != p["old_vector_hash"]):
                raise VisualRepairError("doc#%d p.%d 在准备期间发生改变；整批未写入" %
                                        (p["doc_id"], p["page"]))
        for p, vec in zip(plans, vecs):
            t = p["new_text"]
            conn.execute("UPDATE chunks SET text=?,text_norm=?,char_len=?,token_est=?,content_hash=? "
                         "WHERE id=? AND doc_id=?",
                         (t, db.normalize_for_match(t), len(t), int(len(t) * ingest.TOKEN_PER_CHAR),
                          db.sha256_text(t), p["chunk_id"], p["doc_id"]))
            conn.execute("UPDATE embeddings SET vec=? WHERE chunk_id=?", (to_blob(vec), p["chunk_id"]))
            conn.execute("UPDATE figure_desc SET no_info=0 WHERE doc_id=? AND page=?",
                         (p["doc_id"], p["page"]))
            changed_docs.setdefault(p["doc_id"], []).append(p["page"])
        for did, pages in changed_docs.items():
            n_chars = conn.execute("SELECT COALESCE(SUM(LENGTH(text)),0) FROM chunks WHERE doc_id=?",
                                   (did,)).fetchone()[0]
            conn.execute("UPDATE documents SET n_chars=?,updated_at=? WHERE id=?",
                         (n_chars, time.time(), did))
            key = "visual_repair:%d" % did
            previous = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
            try:
                old_data = json.loads(previous[0]) if previous else {}
            except (TypeError, ValueError):
                old_data = {}
            previous_pages = old_data.get("pages", []) if isinstance(old_data, dict) else []
            marker = {"version": 1, "pages": sorted(set(previous_pages) | set(pages)),
                      "updated_at": time.time()}
            db.set_meta(conn, key, json.dumps(marker, ensure_ascii=False))
        revision = db.bump_data_revision(conn)
        conn.execute("COMMIT")
    except BaseException:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise
    finally:
        conn.close()
    result.update({"applied": True, "backup": str(Path(backup_path).resolve()),
                   "data_revision": revision, "documents": sorted(changed_docs)})
    return result
