"""Portable content locations and source identities for document share packages."""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections import defaultdict
from pathlib import Path

from . import db

ENTRY = "content.json"
SOURCE_FOLDERS = {"课件", "教材", "考试资料"}


def source_folder(doc):
    path = doc.get("path") or doc.get("source_path") or ""
    folder = db.raw_folder(path)
    if folder in SOURCE_FOLDERS:
        return folder
    if doc.get("corpus") == "aux" or doc.get("role") == "reference":
        return "考试资料"
    return "教材" if doc.get("role") == "textbook" else "课件"

QUESTION = "question"
CHAPTER_LINK = "courseware_chapter"
LINKED_TERM = "linked_term"
LOCATIONS = {
    QUESTION: {"module": "资料", "tab": "题库"},
    CHAPTER_LINK: {"module": "知识点", "slot": "教材章节关联"},
    "term": {"module": "知识点", "slot": "名词解释"},
    LINKED_TERM: {"module": "知识点", "slot": "名词解释"},
    "textbook_term": {"module": "知识点", "slot": "教材名词"},
    "exam_focus": {"module": "知识点", "slot": "考试重点"},
    "mindmap": {"module": "知识点", "slot": "思维导图"},
    "courseware_point": {"module": "知识点", "slot": "思维导图"},
    "textbook_point": {"module": "知识点", "slot": "教材总结"},
    "preview": {"module": "教材预习", "slot": "章节预习"},
    "sections": {"module": "教材预习", "slot": "章节目录"},
}

ARTIFACTS = {
    "-末级知识点总结.json": (("mindmap", None), ("courseware_point", "knowledge_points"), ("term", "terms")),
    "-名词解释.json": (("term", "terms"),),
    "-预习总结.json": (("preview", None),),
    "-预习总结-第{chapter}章.json": (("preview", None),),
    "-章节细目.json": (("sections", None),),
    "-教材知识点-第{chapter}章.json": (("textbook_point", "points"), ("exam_focus", "exam_focus"),
                                               ("textbook_term", "terms")),
}
_CHAPTER_FILE = re.compile(r"^-(教材知识点|预习总结)-第([1-9]\d*)章\.json$")
_HOLDING_SCHEMA = """CREATE TABLE IF NOT EXISTS share_unclassified (
    doc_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    source_hash TEXT NOT NULL, source_id TEXT NOT NULL, record TEXT NOT NULL, reason TEXT NOT NULL,
    PRIMARY KEY(doc_id,source_hash,source_id)
)"""


_CHAPTER_LINKS = """CREATE TABLE IF NOT EXISTS share_chapter_links (
    doc_id INTEGER PRIMARY KEY REFERENCES documents(id) ON DELETE CASCADE,
    source_hash TEXT NOT NULL, target TEXT NOT NULL
)"""


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def board_id(root):
    file = Path(root) / "index" / ".share-board-id"
    file.parent.mkdir(parents=True, exist_ok=True)
    try:
        with file.open("x", encoding="ascii") as handle:
            handle.write(uuid.uuid4().hex)
    except FileExistsError:
        pass
    return uuid.UUID(hex=file.read_text(encoding="ascii").strip()).hex


def source_tag(doc):
    return {"key": "content:" + str(doc["content_hash"]), "title": doc["title"]}


def _chapter_info(suffix):
    found = _CHAPTER_FILE.fullmatch(suffix)
    return (suffix, 0) if not found else (f"-{found[1]}-第{{chapter}}章.json", int(found[2]))


def artifact_paths(log_dir, doc_id):
    directory = Path(log_dir) / "knowledge"
    prefix = str(doc_id)
    for path in sorted(directory.glob(prefix + "-*.json")):
        if _chapter_info(path.name[len(prefix):])[0] in ARTIFACTS and path.is_file():
            yield path


def _identity(kind, obj, index=0):
    if not isinstance(obj, dict):
        return digest(obj)
    if kind == QUESTION:
        normalize = lambda value: "".join(str(value or "").casefold().split())
        return digest([obj.get("scope"), obj.get("type"), obj.get("number"), normalize(obj.get("stem")),
                       [normalize(x) for x in obj.get("options", [])]])
    if kind in {"term", "textbook_term"}:
        return str(obj.get("term") or obj.get("zh") or "").strip().casefold() or digest(obj)
    return str(obj.get("id") or obj.get("kp_id") or obj.get("title") or obj.get("name")
               or obj.get("label") or obj.get("focus") or digest(obj))


def _record(doc, kind, key, title, **values):
    source = source_tag(doc)
    return {"id": digest([source["key"], kind, key]), "kind": kind, "title": str(title or kind),
            "source": source, "location": dict(LOCATIONS[kind]), **values}


def courseware_point_id(doc, item):
    return _record(doc, "courseware_point", ["-末级知识点总结.json", "knowledge_points",
                                            _identity("courseware_point", item)], "")["id"]


def question_id(doc, item):
    return item.get("share_source_id") or _record(doc, QUESTION, _identity(QUESTION, item), "")["id"]


def document_extra(doc):
    try:
        extra = json.loads(doc.get("extra") or "{}")
    except (TypeError, ValueError):
        return {}
    return extra if isinstance(extra, dict) else {}


def source_invalid(doc, obj, path=None):
    stamped = obj.get("source_tag") if isinstance(obj, dict) else None
    if isinstance(stamped, dict) and stamped.get("key"):
        return stamped["key"] != source_tag(doc)["key"]
    changed = document_extra(doc).get("source_revision_changed_at")
    return bool(isinstance(changed, (int, float)) and path and path.is_file()
                and path.stat().st_mtime < changed)


def original_file(doc, root, raw_dir):
    extra = document_extra(doc)
    stored = extra.get("original_source") if isinstance(extra, dict) else None
    if isinstance(stored, str):
        candidate = (Path(root) / stored).resolve()
        if candidate.is_relative_to(Path(raw_dir).resolve()) and candidate.is_file():
            return candidate
    indexed = Path(doc.get("abs_path") or Path(root) / str(doc.get("path") or ""))
    candidate = indexed if indexed.is_file() else Path(root) / str(doc.get("path") or "")
    # Older ingests did not record which was the actual upload. Recover only known pipeline variants.
    if candidate.stem.endswith("_slides"):
        unsplit = candidate.with_stem(candidate.stem.removesuffix("_slides"))
        if unsplit.is_file():
            return unsplit
    if candidate.suffix.lower() in {".pptx", ".docx", ".xlsx"}:
        legacy = candidate.with_suffix(candidate.suffix[:-1])
        if legacy.is_file() and "已自动转换" in str(doc.get("warnings") or ""):
            return legacy
    return candidate if candidate.is_file() else None


def source_for_db(doc_id, db_path):
    from . import config
    from contextlib import closing

    path = Path(db_path or config.DB_PATH)
    if not path.is_file():
        return None
    with closing(db.connect(path)) as conn:
        doc = conn.execute("SELECT title,content_hash FROM documents WHERE id=?", (doc_id,)).fetchone()
        return source_tag(doc) if doc else None


def by_source(conn, tag):
    key = tag.get("key") if isinstance(tag, dict) else None
    if not isinstance(key, str) or not key.startswith("content:"):
        return None
    matches = conn.execute("SELECT id,title,content_hash FROM documents WHERE content_hash=? ORDER BY id LIMIT 2",
                           (key.removeprefix("content:"),)).fetchall()
    return matches[0] if len(matches) == 1 else None


def term_source(conn, owner, term, artifact):
    stamped = term.get("source_tag")
    if isinstance(stamped, dict) and stamped.get("key"):
        return stamped
    if term.get("source") == "textbook_chapter":
        meta = artifact.get("meta") or {}
        derived = (meta.get("source_meta") or {}).get("textbook_chapter", meta)
        key = derived.get("textbook_doc_id")
        book = conn.execute("SELECT title,content_hash FROM documents WHERE id=?", (key,)).fetchone()
        return source_tag(book) if book else {"title": derived.get("textbook_title") or "原教材"}
    if term.get("source") == "external_material":
        return {"title": "外部资料"}
    return source_tag(owner)


def portable_term_refs(conn, term, root):
    if not isinstance(term, dict) or term.get("source") != "textbook_chapter":
        return None
    if isinstance(term.get("portable_term_refs"), list):
        return term["portable_term_refs"]
    book = by_source(conn, root)
    refs = []
    for ident in term.get("source_ids") or []:
        if type(ident) is not int or not book:
            continue
        chunk = conn.execute("SELECT ord,source_ref,text FROM chunks WHERE doc_id=? AND id=?",
                             (book["id"], ident)).fetchone()
        if chunk:
            refs.append({"ord":chunk["ord"], "ref":chunk["source_ref"], "text_hash":db.sha256_text(chunk["text"])})
    return refs


def _portable_chapter(conn, value):
    parts = str(value or "").split(":")
    if len(parts) != 2 or not all(p.isascii() and p.isdigit() for p in parts):
        return None
    row = conn.execute("SELECT title,content_hash FROM documents WHERE id=?", (int(parts[0]),)).fetchone()
    return {**source_tag(row), "chapter": int(parts[1])} if row else None


def _local_chapter(conn, target, doc):
    if not isinstance(target, dict) or type(target.get("chapter")) is not int or target["chapter"] < 1:
        return ""
    if target.get("key") == source_tag(doc)["key"]:
        return f"{doc['id']}:{target['chapter']}"
    key = str(target.get("key") or "")
    if not key.startswith("content:"):
        return ""
    matches = conn.execute("SELECT id FROM documents WHERE content_hash=? ORDER BY id LIMIT 2",
                           (key.removeprefix("content:"),)).fetchall()
    return f"{matches[0][0]}:{target['chapter']}" if len(matches) == 1 else ""


def has_table(conn, name):
    return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone())


def existing_chapter(conn, doc, log_dir):
    from . import deck_chapter

    if has_table(conn, "share_chapter_links"):
        row = conn.execute("SELECT target FROM share_chapter_links WHERE doc_id=? AND source_hash=?",
                           (doc["id"], doc["content_hash"])).fetchone()
        if row:
            return json.loads(row[0])
    with deck_chapter._LOCK:
        entry = deck_chapter._read(deck_chapter._path(log_dir)).get(str(doc["id"]))
    if isinstance(entry, dict) and entry.get("deck_path") == doc["path"]:
        target = _portable_chapter(conn, f"{entry.get('book_id')}:{entry.get('chapter')}")
        if target:
            return target
    return None


def import_chapter(conn, doc, record, log_dir, action):
    target = record.get("chapter")
    if (not isinstance(target, dict) or type(target.get("chapter")) is not int or target["chapter"] < 1
            or not isinstance(target.get("key"), str) or not target["key"].startswith("content:")):
        hold(conn, doc, record, "教材章节标识不完整")
        return record, "unclassified", "教材章节标识不完整"
    prior = existing_chapter(conn, doc, log_dir)
    if prior and action == "merge":
        return record, "skipped", "保留本地已有的教材章节关联"
    conn.execute(_CHAPTER_LINKS)
    conn.execute("INSERT OR REPLACE INTO share_chapter_links VALUES(?,?,?)",
                 (doc["id"], doc["content_hash"], canonical(target)))
    clear_hold(conn, doc, record)
    message = "" if _local_chapter(conn, target, doc) else "等待导入关联的教材"
    return record, "success", message


def dependent_terms(conn, doc, log_dir):
    root = source_tag(doc)
    for owner in conn.execute("SELECT * FROM documents WHERE role='courseware' ORDER BY id"):
        owner = dict(owner)
        path = Path(log_dir) / "knowledge" / f"{owner['id']}-名词解释.json"
        if not path.is_file():
            continue
        try:
            artifact = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(artifact, dict) or source_invalid(owner, artifact, path):
            continue
        for term in artifact.get("terms") or []:
            if not isinstance(term, dict) or term.get("source") != "textbook_chapter":
                continue
            tag = term_source(conn, owner, term, artifact)
            if tag.get("key") != root["key"]:
                continue
            owner_tag = source_tag(owner)
            payload = {k: v for k, v in term.items() if k != "source_ids"}
            payload["portable_term_refs"] = portable_term_refs(conn, term, tag)
            payload["source_tag"] = root
            yield _record(doc, LINKED_TERM, [owner_tag["key"], _identity("term", term)],
                          term.get("term"), payload=payload, target=owner_tag, roots=[root, owner_tag])


def pending_for_target(conn, doc):
    if not has_table(conn, "share_unclassified"):
        return []
    return [(dict(row), json.loads(row["record"])) for row in conn.execute(
        "SELECT h.record,d.* FROM share_unclassified h JOIN documents d ON d.id=h.doc_id "
        "AND d.content_hash=h.source_hash WHERE json_extract(h.record,'$.kind')=? "
        "AND json_extract(h.record,'$.target.key')=?",
        (LINKED_TERM, source_tag(doc)["key"]))]


def inventory(conn, doc, log_dir, invalid=None):
    records, artifacts = [], {}
    if doc.get("role") == "courseware":
        chapter = existing_chapter(conn, doc, log_dir)
        if chapter:
            records.append(_record(doc, CHAPTER_LINK, "chapter", "对应教材章节 · "
                                   + chapter.get("title", "") + f" · 第 {chapter['chapter']} 章",
                                   chapter=chapter))

    if has_table(conn, "question_bank_items"):
        for row in conn.execute("SELECT id,chapter,payload FROM question_bank_items WHERE doc_id=? AND source_hash=? ORDER BY rowid",
                                (doc["id"], doc["content_hash"])):
            item = json.loads(row["payload"])
            chapter = _portable_chapter(conn, row["chapter"]) or item.get("portable_chapter")
            payload = {k: v for k, v in item.items() if k not in {"id", "doc_id", "chapter", "portable_chapter", "source_tag", "share_source_id"}}
            record = _record(doc, QUESTION, _identity(QUESTION, item), item.get("stem"),
                             payload=payload, chapter=chapter)
            record["id"] = question_id(doc, item)
            records.append(record)
    for path in artifact_paths(log_dir, doc["id"]):
        obj = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(obj, dict):
            raise ValueError(f"无法读取子文件 {path.name}")
        if source_invalid(doc, obj, path):
            if invalid is not None:
                invalid.append(path.name)
            continue
        suffix = path.name[len(str(doc["id"])):]
        pattern, chapter = _chapter_info(suffix)
        artifacts[suffix] = obj
        for kind, field in ARTIFACTS[pattern]:
            if not field:
                n = len((obj.get("structure") or {}).get("nodes") or []) if kind == "mindmap" else 0
                title = LOCATIONS[kind]["slot"] + (f" · 第 {chapter} 章" if chapter else "")
                records.append(_record(doc, kind, suffix, title, artifact=suffix, count=n or 1))
                continue
            for index, value in enumerate(obj.get(field) or []):
                identity = _identity(kind, value, index)
                title = (value.get("term") or value.get("title") or value.get("name") or
                         value.get("label") or value.get("focus")) if isinstance(value, dict) else value
                record = _record(doc, kind, [suffix, field, identity], title,
                                 artifact=suffix, field=field, index=index)
                if kind == "courseware_point":
                    record["id"] = courseware_point_id(doc, value)
                if kind == "term" and isinstance(value, dict):
                    root = term_source(conn, doc, value, obj)
                    record["roots"] = [root] + ([source_tag(doc)] if root.get("key") != source_tag(doc)["key"] else [])
                    refs = portable_term_refs(conn, value, root)
                    if refs is not None:
                        record["related_refs"] = refs
                records.append(record)
    if doc.get("role") == "textbook":
        records.extend(dependent_terms(conn, doc, log_dir))
    if has_table(conn, "share_unclassified"):
        records.extend({**json.loads(r[0]), "unclassified": True, "reason": r[1]} for r in conn.execute(
            "SELECT record,reason FROM share_unclassified WHERE doc_id=? AND source_hash=? ORDER BY rowid",
            (doc["id"], doc["content_hash"])))
    distinct = {}
    for item in records:
        if item["id"] not in distinct:
            distinct[item["id"]] = item
        elif item.get("payload", {}).get("answer"):
            distinct[item["id"]] = item
    return list(distinct.values()), artifacts


def public(records):
    return [{k: row[k] for k in ("id", "kind", "title", "source", "location", "artifact", "count", "unclassified", "reason", "roots") if k in row}
            for row in records]


def select(records, artifacts, selected):
    if selected is None:
        return records, artifacts
    if not isinstance(selected, list) or not all(isinstance(item, str) for item in selected):
        raise ValueError("子文件选择无效")
    ids = set(selected)
    if ids - {item["id"] for item in records}:
        raise ValueError("子文件清单已变化，请重新打开导出预览")
    if len(ids) == len(records):
        return records, artifacts
    result = [dict(item) for item in records if item["id"] in ids]
    indexes = defaultdict(list)
    filtered = {}
    for item in result:
        suffix = item.get("artifact")
        if suffix not in artifacts or item.get("unclassified"):
            continue
        indexes[suffix].append(item)
    for suffix, entries in indexes.items():
        pattern, _ = _chapter_info(suffix)
        source = artifacts[suffix]
        fields = {key for _, key in ARTIFACTS[pattern] if key}
        if not fields:
            filtered[suffix] = source
            continue
        obj = {**source, **{field: [] for field in fields}}
        filtered[suffix] = obj
        for row in entries:
            field = row.get("field")
            if field:
                index = row["index"]
                row["index"] = len(obj[field])
                obj[field].append(source[field][index])
        if any(len(obj[field]) != len(source.get(field) or []) for field in fields):
            obj["share_partial"] = True
            obj.pop("parts", None)
            obj.pop("segments", None)
            obj.pop("merge_decisions", None)
    return result, filtered


def validate(container, doc):
    source = source_tag(doc)
    supplied = container.get("source") if isinstance(container, dict) else None
    if not isinstance(supplied, dict) or supplied.get("key") != source["key"]:
        raise ValueError("分享包来源标签与源文件不一致")
    records = container.get("children")
    if not isinstance(records, list):
        raise ValueError("分享包缺少子文件清单")
    seen = set()
    for row in records:
        if (not isinstance(row, dict) or not isinstance(row.get("id"), str) or not row["id"]
                or row["id"] in seen or not isinstance(row.get("kind"), str) or not isinstance(row.get("title"), str)
                or not isinstance(row.get("location"), dict) or not isinstance(row.get("source"), dict)
                or row["source"].get("key") != source["key"]):
            raise ValueError("分享包的子文件、来源标签或位置标签不合法")
        seen.add(row["id"])
    return records


def placement(record):
    expected = LOCATIONS.get(record["kind"])
    return expected if expected == record["location"] else None


def hold(conn, doc, record, reason):
    conn.execute(_HOLDING_SCHEMA)
    conn.execute("INSERT OR REPLACE INTO share_unclassified VALUES(?,?,?,?,?)",
                 (doc["id"], doc["content_hash"], record["id"], canonical(record), reason))


def clear_hold(conn, doc, record):
    if has_table(conn, "share_unclassified"):
        conn.execute("DELETE FROM share_unclassified WHERE doc_id=? AND source_hash=? AND source_id=?",
                     (doc["id"], doc["content_hash"], record["id"]))


def report(outcomes):
    counts = {"success": 0, "skipped": 0, "unclassified": 0}
    locations = defaultdict(int)
    issues = []
    for record, status, reason in outcomes:
        counts[status] += 1
        loc = placement(record)
        if status == "success" and loc:
            locations[f"{loc['module']} / {loc.get('tab') or loc.get('slot')}"] += 1
        if reason:
            issues.append({"id": record["id"], "title": record["title"], "status": status, "reason": reason})
    return {**counts, "locations": dict(locations), "issues": issues}


def import_questions(conn, doc, records, id_map, conflict):
    from . import question_bank

    # execute() (not executescript) keeps this inside the enclosing import transaction.
    for ddl in question_bank._SCHEMA.split(";"):
        if ddl.strip():
            conn.execute(ddl)
    existing = {}
    by_source = {}
    for row in conn.execute("SELECT id,payload FROM question_bank_items WHERE doc_id=? AND source_hash=?",
                            (doc["id"], doc["content_hash"])):
        item = json.loads(row["payload"])
        existing[_identity(QUESTION, item)] = (row["id"], item)
        if item.get("share_source_id"):
            by_source[item["share_source_id"]] = (row["id"], item)
    report = []
    for row in records:
        payload = row.get("payload")
        if (not isinstance(payload, dict) or not isinstance(payload.get("stem"), str) or not payload["stem"].strip()
                or not isinstance(payload.get("options"), list) or not all(isinstance(o, str) for o in payload["options"])
                or not isinstance(payload.get("answer", ""), str)):
            hold(conn, doc, row, "题目格式无法识别")
            report.append((row, "unclassified", "题目格式无法识别"))
            continue
        item = dict(payload)
        for field in ("refs", "answer_refs"):
            refs = item.get(field)
            item[field] = [{"chunk_id": id_map[ref["chunk_id"]], "ref": str(ref.get("ref") or "")}
                           for ref in (refs if isinstance(refs, list) else []) if isinstance(ref, dict)
                           and type(ref.get("chunk_id")) is int and ref["chunk_id"] in id_map]
        if not item["refs"] or payload.get("answer_refs") and not item["answer_refs"]:
            hold(conn, doc, row, "目标库找不到原题或答案的切块")
            report.append((row, "unclassified", "目标库找不到原题或答案的切块"))
            continue
        key = _identity(QUESTION, item)
        hit = by_source.get(row["id"]) or existing.get(key)
        chapter = _local_chapter(conn, row.get("chapter"), doc)
        item.update(source_tag=source_tag(doc), share_source_id=row["id"], portable_chapter=row.get("chapter"))
        item.setdefault("answer", "")
        if hit and conflict == "merge" and (hit[1].get("answer") or not item.get("answer")):
            report.append((row, "skipped", "本地已有同一题和答案"))
            continue
        if hit and conflict == "merge":
            item = {**hit[1], "answer": item["answer"], "answer_refs": item["answer_refs"],
                    "source_tag": item["source_tag"], "share_source_id": item["share_source_id"]}
            chapter = hit[1].get("chapter") or chapter
        ident = hit[0] if hit else question_bank._signature([doc["id"], *question_bank._item_key(item)])[:28]
        if not hit and conn.execute("SELECT 1 FROM question_bank_items WHERE id=?", (ident,)).fetchone():
            ident = digest([doc["id"], row["id"]])[:28]
        conn.execute("INSERT INTO question_bank_items(id,doc_id,chunk_id,source_hash,chapter,payload) VALUES(?,?,?,?,?,?) "
                     "ON CONFLICT(id) DO UPDATE SET chunk_id=excluded.chunk_id,source_hash=excluded.source_hash,"
                     "chapter=CASE WHEN question_bank_items.manual_chapter THEN question_bank_items.chapter ELSE excluded.chapter END,"
                     "payload=excluded.payload", (ident, doc["id"], item["refs"][0]["chunk_id"], doc["content_hash"], chapter, canonical(item)))
        existing[key] = (ident, item)
        clear_hold(conn, doc, row)
        report.append((row, "success", "待归章：目标库尚无对应章节" if row.get("chapter") and not chapter else ""))
    return report
