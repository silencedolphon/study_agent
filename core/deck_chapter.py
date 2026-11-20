"""手动保存课件与教材章节的对应关系；读取既有学习产物，不调用模型。"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from . import config, english_terms, kp_build, preview, terms_store, textbook_kp

_LOCK = threading.RLock()


def _attach_term_source_ranges(conn, book_id, terms):
    """Attach textbook page ranges from each term's source chunk IDs."""
    wanted = set()
    for term in terms:
        if not isinstance(term, dict):
            continue
        for source_id in (term.get("source_ids") or []):
            try:
                wanted.add(int(source_id))
            except (TypeError, ValueError):
                continue
    refs = {}
    if wanted:
        ids = sorted(wanted)
        for offset in range(0, len(ids), 400):
            batch = ids[offset:offset + 400]
            marks = ",".join("?" for _ in batch)
            rows = conn.execute(
                "SELECT id, source_ref FROM chunks WHERE doc_id=? AND id IN (%s)" % marks,
                (int(book_id), *batch),
            ).fetchall()
            for row in rows:
                refs[int(row["id"])] = str(row["source_ref"] or "")

    for term in terms:
        if not isinstance(term, dict):
            continue
        term["en"] = english_terms.segment_english(term.get("en") or "")
        spans = []
        for source_id in (term.get("source_ids") or []):
            try:
                ref = refs.get(int(source_id), "")
            except (TypeError, ValueError):
                continue
            span = textbook_kp._ref_span(ref)
            if span:
                spans.append(span)
        merged = []
        for start, end in sorted(set(spans)):
            if merged and start <= merged[-1][1] + 1:
                merged[-1] = (merged[-1][0], max(merged[-1][1], end))
            else:
                merged.append((start, end))
        term["source_ranges"] = [[start, end] for start, end in merged]
    return terms


def _path(log_dir=None):
    return Path(log_dir or config.LOG_DIR) / "knowledge" / "courseware-chapter-links.json"


def chapter_has_courseware_terms(conn, book_id, chapter, *, log_dir=None) -> dict:
    """Return whether any current deck linked to a chapter already has glossary rows.

    This is a read-only gate for the optional textbook-term extraction branch. A stale
    link (deleted/replaced deck or textbook, or changed path) is ignored; malformed term
    artifacts still raise so an unreadable glossary is never mistaken for an empty one.
    """
    book_id, chapter = int(book_id), int(chapter)
    with _LOCK:
        links = _read(_path(log_dir))
    for raw_id, entry in links.items():
        if not isinstance(entry, dict):
            continue
        try:
            if int(entry.get("book_id") or 0) != book_id or int(entry.get("chapter") or 0) != chapter:
                continue
            deck_id = int(raw_id)
            deck = _doc(conn, deck_id)
            book = _doc(conn, book_id)
        except (TypeError, ValueError):
            continue
        if (deck.get("role") != "courseware"
                or str(deck.get("path") or "") != str(entry.get("deck_path") or "")):
            continue
        if (book.get("role") != "textbook"
                or str(book.get("path") or "") != str(entry.get("book_path") or "")):
            continue
        term_path = (Path(log_dir or config.LOG_DIR) / "knowledge"
                     / (str(deck_id) + kp_build.TERMS_SUFFIX))
        artifact = terms_store.read(term_path)
        if any(str(row.get("term") or "").strip() for row in artifact.get("terms", [])):
            return {"exists": True, "doc_id": deck_id,
                    "title": str(deck.get("title") or "")}
        if artifact["meta"].get("terms_override"):
            continue
        legacy_path = (Path(log_dir or config.LOG_DIR) / "knowledge"
                       / (str(deck_id) + kp_build.KP_SUFFIX))
        legacy = kp_build._load_prev(legacy_path)
        legacy_terms = legacy.get("terms") if isinstance(legacy.get("terms"), list) else []
        if any(isinstance(row, dict) and str(row.get("term") or "").strip()
               for row in legacy_terms):
            return {"exists": True, "doc_id": deck_id,
                    "title": str(deck.get("title") or "")}
    return {"exists": False, "doc_id": 0, "title": ""}


def _read(path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except (ValueError, OSError) as exc:
        raise ValueError("章节对应记录无法读取，已保留原文件：" + str(exc)) from exc


def _doc(conn, doc_id):
    row = conn.execute("SELECT id, title, path, role FROM documents WHERE id=?", (int(doc_id),)).fetchone()
    if not row:
        raise ValueError("资料不存在，请刷新后重试")
    return {"id": int(row[0]), "title": str(row[1] or ""), "path": str(row[2] or ""),
            "role": str(row[3] or "courseware")}


def save(conn, doc_id, book_id, chapter, *, log_dir=None, db_path=None):
    deck = _doc(conn, doc_id)
    if deck["role"] != "courseware":
        raise ValueError("请选择课件")
    book_id, chapter = int(book_id or 0), int(chapter or 0)
    entry = None
    if book_id:
        book = _doc(conn, book_id)
        if book["role"] != "textbook":
            raise ValueError("请选择教材")
        result = preview.list_chapters(book_id, db_path=db_path)
        ch = next((x for x in result.get("chapters", []) if int(x.get("no") or 0) == chapter), None)
        if not result.get("ok") or not ch:
            raise ValueError("教材章节不存在，请刷新后重新选择")
        entry = {"deck_path": deck["path"], "book_id": book_id, "book_path": book["path"],
                 "chapter": chapter}
    path = _path(log_dir)
    with _LOCK:
        links = _read(path)
        if entry:
            links[str(deck["id"])] = entry
        else:
            links.pop(str(deck["id"]), None)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        try:
            tmp.write_text(json.dumps(links, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, path)
        finally:
            if tmp.exists():
                tmp.unlink()
    return {"ok": True}


def get(conn, doc_id, *, log_dir=None, db_path=None):
    deck = _doc(conn, doc_id)
    if deck["role"] != "courseware":
        raise ValueError("请选择课件")
    with _LOCK:
        entry = _read(_path(log_dir)).get(str(deck["id"]))
    empty = {"ok": True, "binding": None, "exam_focus": [], "terms": []}
    if not isinstance(entry, dict) or entry.get("deck_path") != deck["path"]:
        return empty
    try:
        book = _doc(conn, entry["book_id"])
        if book["role"] != "textbook" or book["path"] != entry.get("book_path"):
            return {**empty, "warning": "对应教材已变化，请重新选择"}
        chapter = int(entry["chapter"])
        listing = preview.list_chapters(book["id"], db_path=db_path)
        ch = next((x for x in listing.get("chapters", []) if int(x.get("no") or 0) == chapter), None)
        if not ch:
            return {**empty, "warning": "对应章节已变化，请重新选择"}
    except (KeyError, TypeError, ValueError):
        return {**empty, "warning": "对应教材已不可用，请重新选择"}
    result = {**empty, "binding": {"book_id": book["id"], "book_title": book["title"],
                                  "chapter": chapter, "chapter_name": str(ch.get("name") or "")}}
    # 考试重点已归入教材知识点总结；预习产物不再为此栏提供数据。
    try:
        artifact = textbook_kp.read_summary(book["id"], chapter, log_dir=log_dir, db_path=db_path)
        result["exam_focus"] = artifact.get("exam_focus") or []
        result["terms"] = _attach_term_source_ranges(
            conn, book["id"], artifact.get("terms") or [])
        result["summary_available"] = bool(artifact.get("exists"))
        result["summary_stale"] = bool(artifact.get("stale"))
    except (ValueError, OSError):
        result["summary_available"] = False
        result["exam_focus"] = []
    return result
