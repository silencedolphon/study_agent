"""Manual add/edit/delete operations for courseware and textbook term artifacts."""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path

from . import (config, deck_chapter, english_terms, kp_build, preview,
               terms_store, textbook_kp)


def _document(conn, doc_id: int, role: str) -> dict:
    row = conn.execute("SELECT id,title,role FROM documents WHERE id=?", (int(doc_id),)).fetchone()
    if not row:
        raise ValueError("资料不存在，请刷新后重试")
    if str(row["role"] or "") != role:
        raise ValueError("请选择" + ("课件" if role == "courseware" else "教材"))
    return {"id": int(row["id"]), "title": str(row["title"] or "")}


def _clean(payload: dict) -> dict:
    term = str(payload.get("term") or "").strip()
    en = str(payload.get("en") or "").strip()
    definition = str(payload.get("definition") or "").strip()
    if not term:
        raise ValueError("名词不能为空")
    if len(term) > 200:
        raise ValueError("名词不能超过 200 个字符")
    if len(en) > 300:
        raise ValueError("英文不能超过 300 个字符")
    if not definition:
        raise ValueError("释义不能为空")
    if len(definition) > 12000:
        raise ValueError("释义不能超过 12000 个字符")
    return {"term": term, "en": english_terms.segment_english(en), "definition": definition}


def _key(value) -> str:
    return re.sub(r"\s+", "", str(value or "")).casefold()


def _manual_row(schema: str, clean: dict, base: dict | None = None) -> dict:
    row = dict(base or {})
    old_name = str(row.get("term") or "")
    row.update({"term": clean["term"], "en": clean["en"], "source": "manual"})
    row["def" if schema == "courseware" else "definition"] = clean["definition"]
    if schema == "courseware":
        if old_name and _key(old_name) != _key(clean["term"]):
            # A renamed term may no longer refer to its previous KP/page match.
            row.update({"first_page": 0, "first_kp_id": "", "first_kp_name": "",
                        "kp_ids": [], "relevance_score": None,
                        "relevance_kp_id": "", "relevance_kp_name": ""})
        row.setdefault("first_page", 0)
        row.setdefault("first_kp_id", "")
        row.setdefault("first_kp_name", "")
        row.setdefault("kp_ids", [])
        row.setdefault("n_occurrences", 1)
    else:
        row.setdefault("source_ids", [])
        row.setdefault("source_ranges", [])
    return row


def _sync_counterpart(path: Path, schema: str, action: str,
                      old_term: str, clean: dict | None) -> None:
    """Mirror a manual operation by term identity, collapsing duplicate matches."""
    old_key = _key(old_term)
    new_key = _key((clean or {}).get("term"))

    def transform(data: dict) -> dict:
        terms = list(data.get("terms") or [])
        if action == "delete":
            terms = [row for row in terms
                     if not isinstance(row, dict) or _key(row.get("term")) != old_key]
        else:
            match_keys = {new_key}
            if action == "update" and old_key:
                match_keys.add(old_key)
            indices = [i for i, row in enumerate(terms)
                       if isinstance(row, dict) and _key(row.get("term")) in match_keys]
            base = dict(terms[indices[0]]) if indices else None
            row = _manual_row(schema, clean, base)
            insert_at = min(indices) if indices else len(terms)
            terms = [item for i, item in enumerate(terms) if i not in set(indices)]
            terms.insert(min(insert_at, len(terms)), row)
        meta = dict(data.get("meta") or {})
        if schema == "courseware":
            meta["terms_override"] = True
            meta["manual_edits"] = True
        return {**data, "meta": meta, "terms": terms}

    terms_store.mutate(path, transform)


def manage(conn: sqlite3.Connection, payload: dict) -> dict:
    """Apply a manual operation and mirror it to the currently linked term list."""
    target = str(payload.get("target") or "").strip()
    action = str(payload.get("action") or "").strip().lower()
    if target not in ("courseware", "textbook"):
        raise ValueError("未知名词目标")
    if action not in ("add", "update", "delete"):
        raise ValueError("未知名词操作")

    textbook = None
    chapter = 0
    deck = None
    if target == "courseware":
        doc_id = int(payload.get("doc_id") or 0)
        deck = _document(conn, doc_id, "courseware")
        artifact_path = Path(kp_build.terms_out_file(deck["id"]))
        relation = deck_chapter.get(conn, deck["id"])
        binding = relation.get("binding") if isinstance(relation, dict) else None
        counterpart_path = (Path(textbook_kp._paths(int(binding["book_id"]),
                            int(binding["chapter"]))[0]) if isinstance(binding, dict) else None)
        counterpart_schema = "textbook"
    else:
        textbook = _document(conn, int(payload.get("book_id") or 0), "textbook")
        chapter = int(payload.get("chapter") or 0)
        if chapter <= 0:
            raise ValueError("请选择教材章节")
        listing = preview.list_chapters(textbook["id"], db_path=config.DB_PATH)
        if not listing.get("ok") or not any(
                int(item.get("no") or 0) == chapter for item in listing.get("chapters") or []):
            raise ValueError("教材章节不存在，请刷新后重试")
        artifact_path = Path(textbook_kp._paths(textbook["id"], chapter)[0])
        counterpart_schema = "courseware"
        deck_id = int(payload.get("doc_id") or 0)
        counterpart_path = None
        if deck_id:
            deck = _document(conn, deck_id, "courseware")
            relation = deck_chapter.get(conn, deck_id)
            binding = relation.get("binding") if isinstance(relation, dict) else None
            if (not isinstance(binding, dict)
                    or int(binding.get("book_id") or 0) != textbook["id"]
                    or int(binding.get("chapter") or 0) != chapter):
                raise ValueError("当前课件与教材章节的对应关系已变化，请刷新知识点窗口")
            counterpart_path = Path(kp_build.terms_out_file(deck_id))

    index = None
    if action != "add":
        try:
            index = int(payload.get("index"))
        except (TypeError, ValueError):
            raise ValueError("名词条目已变化，请刷新后重试") from None
        if index < 0:
            raise ValueError("名词条目已变化，请刷新后重试")
    expected = str(payload.get("expected_term") or "")
    clean = _clean(payload) if action in ("add", "update") else None
    previous_name = {"value": ""}

    def transform(data: dict) -> dict:
        terms = list(data.get("terms") or [])
        if action == "add":
            if any(isinstance(row, dict) and _key(row.get("term")) == _key(clean["term"])
                   for row in terms):
                raise ValueError("这个名词已经存在；请使用修改按钮编辑")
            terms.append(_manual_row(target, clean))
        else:
            if index >= len(terms) or not isinstance(terms[index], dict):
                raise ValueError("名词条目已变化，请刷新后重试")
            current = dict(terms[index])
            if expected and str(current.get("term") or "") != expected:
                raise ValueError("名词条目已变化，请刷新后重试")
            previous_name["value"] = str(current.get("term") or "")
            if action == "delete":
                deleted_key = _key(previous_name["value"])
                terms = [row for row in terms
                         if not isinstance(row, dict) or _key(row.get("term")) != deleted_key]
            else:
                new_key = _key(clean["term"])
                if new_key != _key(current.get("term")) and any(
                        i != index and isinstance(row, dict) and _key(row.get("term")) == new_key
                        for i, row in enumerate(terms)):
                    raise ValueError("修改后的名词已存在，请先整理重复名词")
                updated_row = _manual_row(target, clean, current)
                old_key = _key(current.get("term"))
                terms = [updated_row if i == index else row
                         for i, row in enumerate(terms)
                         if i == index or not (isinstance(row, dict)
                                               and _key(row.get("term")) in {old_key, new_key})]
        meta = dict(data.get("meta") or {})
        if target == "courseware":
            # Prevent deleted/overridden terms from reappearing through the legacy KP fallback.
            meta["terms_override"] = True
            meta["manual_edits"] = True
        return {**data, "meta": meta, "terms": terms}

    current_existed = artifact_path.is_file()
    before_current = terms_store.read(artifact_path)
    terms_store.mutate(artifact_path, transform)
    synced = False
    if counterpart_path is not None:
        try:
            old_term = previous_name["value"] if action != "add" else clean["term"]
            _sync_counterpart(counterpart_path, counterpart_schema, action, old_term, clean)
            synced = True
        except Exception:
            # Keep the two views consistent if the counterpart write fails.
            if current_existed:
                terms_store.write(artifact_path, before_current)
            else:
                try:
                    artifact_path.unlink(missing_ok=True)
                except OSError:
                    pass
            raise

    return {"ok": True, "target": target, "action": action,
            "doc_id": deck["id"] if deck else 0,
            "book_id": textbook["id"] if textbook else 0,
            "chapter": chapter, "synced": synced,
            "backup": "", "out_path": str(artifact_path)}
