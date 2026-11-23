"""Offline migration across two boards, including conflicts and forward-compatible placement."""
import hashlib
import json
import tempfile
import unittest
import zipfile
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from core import db, deck_chapter, question_bank, share, share_content, textbook_kp


class PortableShare(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.source = self.board("source")
        self.dest = self.board("dest")
        with closing(db.connect(self.source["db_path"])) as conn:
            self.book = self.document(conn, self.source, "教材", "原教材", "a", 7, 25)
            self.deck = self.document(conn, self.source, "课件", "原讲义", "b", 8, 77)
            conn.executescript(question_bank._SCHEMA)
            self.question(conn, "旧答案")
            conn.commit()
        with closing(db.connect(self.dest["db_path"])) as conn:
            self.document(conn, self.dest, "课件", "另一个课件", "c", 109, 200)
            conn.commit()
        self.knowledge = self.source["log_dir"] / "knowledge"
        self.knowledge.mkdir(parents=True)
        plan = textbook_kp._plan(self.book, 2, db_path=self.source["db_path"])
        self.write(self.knowledge / "7-教材知识点-第2章.json", {
            "doc_id": self.book, "status": "done", "chapter": 2,
            "signature": plan["signature"], "points": [{"id": "one", "title": "书中知识", "source_ids": [25]}],
            "terms": [{"term":"重点名词", "source_ids": [25]}], "exam_focus": ["教材考点"],
        })
        self.write(self.knowledge / "8-末级知识点总结.json", {
            "meta":{"doc_id": self.deck},
            "structure":{"root":{"id":"root","name":"源导图","children":[]}},
            "knowledge_points":[
                {"id":"k1","name":"选中的知识点","sources":[{"chunk_id":77}]},
                {"id":"k2","name":"另一个知识点","sources":[{"chunk_id":77}]},
            ],
        })
        self.write(self.knowledge / "courseware-chapter-links.json", {"8": {
            "book_id": 7, "chapter": 2, "book_path":"raw/教材/原教材.txt", "deck_path":"raw/课件/原讲义.txt",
        }})
        mock = patch("core.kp_vector.sync_artifact", return_value={"status":"ok"})
        mock.start()
        self.addCleanup(mock.stop)

    def board(self, name):
        root = self.root / name
        root.mkdir()
        raw = root / "raw"
        db_path = root / "study.db"
        with closing(db.connect(db_path)) as conn:
            db.init_db(conn)
        return dict(root=root, db_path=db_path, raw_dir=raw, log_dir=root / "logs")

    def document(self, conn, board, folder, title, fingerprint, doc_id, chunk_id):
        path = board["raw_dir"] / folder / (title + ".txt")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("第二章 材料", encoding="utf8")
        conn.execute(
            "INSERT INTO documents(id,path,abs_path,kind,title,content_hash,n_chunks,status,role) "
            "VALUES(?,?,?,?,?,?,1,'ok',?)",
            (doc_id, path.relative_to(board["root"]).as_posix(), str(path), "text", title, fingerprint*64,
             "textbook" if folder == "教材" else "courseware"))
        conn.execute(
            "INSERT INTO chunks(id,doc_id,ord,text,heading_path,source_ref) VALUES(?,?,0,?,?,'p.3')",
            (chunk_id, doc_id, "题干和答案", title + " > 第2章 材料"))
        return doc_id

    def question(self, conn, answer):
        item = {
            "stem":"这是什么题？", "scope":"chapter-2", "type":"qa", "number":"1",
            "answer":answer, "options":[], "refs":[{"chunk_id":77,"ref":"p.3"}],
            "answer_refs":[{"chunk_id":77,"ref":"p.3"}],
        }
        conn.execute("INSERT OR REPLACE INTO question_bank_items VALUES('source-side-only',8,77,?,'7:2',0,?)",
                     ("b"*64,json.dumps(item,ensure_ascii=False)))

    def write(self, path, value):
        path.write_text(json.dumps(value,ensure_ascii=False), encoding="utf8")

    def export(self, doc, name, children=None, original=True):
        package = self.root / (name + ".zip")
        report = share.export_doc(doc,package,with_original=original,children=children,**self.source)
        return package, report

    def test_fresh_conflicts_late_chapter_and_references(self):
        package, exported = self.export(self.deck, "deck")
        inspect = share.inspect_package(package)
        self.assertEqual(inspect["manifest"]["format_version"], 2)
        self.assertEqual(exported["manifest"]["board_id"], inspect["manifest"]["board_id"])
        self.assertEqual(sum(child["kind"] == "courseware_point" for child in inspect["children"]), 2)
        first = share.import_package(package, **self.dest)
        self.assertEqual(first["content_report"]["success"], 5)
        dest_id = first["doc_id"]
        self.assertNotEqual(dest_id, self.deck)
        self.assertTrue((self.dest["raw_dir"] / "课件" / "原讲义.txt").exists())
        with closing(db.connect(self.dest["db_path"])) as conn:
            self.assertEqual(question_bank.view(conn, "", 0)["items"][0]["answer"], "旧答案")
            self.assertIn("等待", deck_chapter.get(conn, dest_id, log_dir=self.dest["log_dir"],
                                                   db_path=self.dest["db_path"])["warning"])
        book, report = self.export(self.book, "book")
        self.assertEqual(report["manifest"]["board_id"], inspect["manifest"]["board_id"])
        book_id = share.import_package(book, **self.dest)["doc_id"]
        with closing(db.connect(self.dest["db_path"])) as conn:
            item = question_bank.view(conn, f"{book_id}:2", 0)["items"][0]
            self.assertNotEqual(item["id"], "source-side-only")
            self.assertNotEqual(item["answer_refs"][0]["chunk_id"], 77)
            self.assertEqual(item["answer_refs"], item["refs"])
            result = deck_chapter.get(conn, dest_id, log_dir=self.dest["log_dir"], db_path=self.dest["db_path"])
            self.assertEqual(result["exam_focus"], ["教材考点"])
            self.assertFalse(result["summary_stale"])
        with closing(db.connect(self.source["db_path"])) as conn:
            self.question(conn, "新版答案")
            conn.commit()
        changed, _ = self.export(self.deck, "changed")
        self.assertEqual(share.import_package(changed, **self.dest)["status"],"skipped")
        merged = share.import_package(changed, conflict_action="merge", **self.dest)
        self.assertEqual(merged["counts"]["documents"], 0)
        with closing(db.connect(self.dest["db_path"])) as conn:
            self.assertEqual(question_bank.view(conn, f"{book_id}:2", 0)["items"][0]["answer"], "旧答案")
        share.import_package(changed, conflict_action="overwrite", **self.dest)
        with closing(db.connect(self.dest["db_path"])) as conn:
            items = question_bank.view(conn, f"{book_id}:2", 0)["items"]
            self.assertEqual(len(items), 1)
            self.assertEqual(items[0]["answer"], "新版答案")

    def test_child_only_and_multistage_glossary_reappear_when_parent_arrives(self):
        with closing(db.connect(self.source["db_path"])) as conn:
            conn.execute("INSERT INTO chunks(id,doc_id,ord,text,heading_path,source_ref) "
                         "VALUES(78,8,1,?,'原讲义 > 第2章 材料','p.4')", ("未选中的资料摘录",))
            conn.execute("UPDATE documents SET n_chunks=2 WHERE id=8")
            conn.commit()
        self.write(self.knowledge / "8-名词解释.json", {
            "meta":{"doc_id":8, "textbook_doc_id":7},
            "terms":[{"term":"从教材筛到课件的名词", "source":"textbook_chapter",
                       "def":"定义", "source_ids":[25]}],
        })
        book, report = self.export(self.book, "book-linked")
        link = next(child for child in report["children"] if child["kind"] == share_content.LINKED_TERM)
        self.assertEqual(len(link["roots"]), 2)
        book_result = share.import_package(book, **self.dest)
        self.assertEqual(book_result["content_report"]["unclassified"], 1)
        _, deck_report = self.export(self.deck, "deck-full")
        point_id = next(child["id"] for child in deck_report["children"] if child["title"] == "选中的知识点")
        partial = self.root / "minimized.zip"
        limited = share.export_doc(self.deck, partial, with_original=False, children=[point_id],
                                   minimize_context=True, **self.source)
        self.assertEqual(limited["counts"]["chunks"], 1)
        with zipfile.ZipFile(partial) as package:
            self.assertNotIn("未选中的资料摘录", package.read("chunks.jsonl").decode("utf8"))
        imported = share.import_package(partial, **self.dest)
        deck_id = imported["doc_id"]
        glossary = self.dest["log_dir"] / "knowledge" / f"{deck_id}-名词解释.json"
        term = json.loads(glossary.read_text(encoding="utf8"))["terms"][0]
        self.assertEqual(term["term"], "从教材筛到课件的名词")
        with closing(db.connect(self.dest["db_path"])) as conn:
            self.assertFalse(conn.execute("SELECT 1 FROM share_unclassified").fetchone())
        full, _ = self.export(self.deck, "deck-full")
        result = share.import_package(full, conflict_action="merge", **self.dest)
        self.assertEqual(result["doc_id"], deck_id)
        self.assertEqual(result["counts"]["chunks"], 1)
        with closing(db.connect(self.dest["db_path"])) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM chunks WHERE doc_id=?", (deck_id,)).fetchone()[0], 2)
        self.assertEqual(len(json.loads(glossary.read_text(encoding="utf8"))["terms"]), 1)

    def test_individual_point_unknown_location_and_v1(self):
        _, exported = self.export(self.deck, "deck")
        item = next(child for child in exported["children"] if child["title"] == "选中的知识点")
        partial, _ = self.export(self.deck, "partial", [item["id"]], original=False)
        placed = share.import_package(partial, **self.dest)
        self.assertEqual(placed["content_report"]["success"], 1)
        dest_id = placed["doc_id"]
        output = self.dest["log_dir"] / "knowledge" / f"{dest_id}-末级知识点总结.json"
        point = json.loads(output.read_text(encoding="utf8"))
        self.assertEqual([row["id"] for row in point["knowledge_points"]], ["k1"])
        unknown = self.root / "unknown.zip"
        with zipfile.ZipFile(partial) as package, zipfile.ZipFile(unknown,"w") as out:
            data = json.loads(package.read("content.json"))
            data["children"][0]["location"] = {"module":"未来模块"}
            serialized = json.dumps(data,ensure_ascii=False).encode()
            manifest = json.loads(package.read("manifest.json"))
            entry = next(x for x in manifest["files"] if x["name"]=="content.json")
            entry.update(sha256=hashlib.sha256(serialized).hexdigest(),bytes=len(serialized))
            for name in package.namelist():
                out.writestr(name, serialized if name == "content.json" else
                             json.dumps(manifest) if name == "manifest.json" else package.read(name))
        holding = self.board("holding")
        held = share.import_package(unknown, **holding)
        self.assertEqual(held["content_report"]["unclassified"], 1)
        self.assertEqual(held["counts"]["knowledge"], 0)
        with closing(db.connect(holding["db_path"])) as conn:
            doc = dict(conn.execute("SELECT * FROM documents").fetchone())
            records, _ = share_content.inventory(conn, doc, holding["log_dir"])
            self.assertTrue(records[0]["unclassified"])
            self.assertEqual(records[0]["held_payload"]["name"], "选中的知识点")
        book, _ = self.export(self.book, "book")
        old = self.root / "v1.zip"
        with zipfile.ZipFile(book) as package, zipfile.ZipFile(old,"w") as out:
            manifest = json.loads(package.read("manifest.json"))
            manifest["format_version"] = 1
            manifest["files"] = [x for x in manifest["files"] if x["name"]!="content.json"]
            for name in package.namelist():
                if name != "content.json":
                    out.writestr(name, json.dumps(manifest) if name=="manifest.json" else package.read(name))
        self.assertEqual(share.import_package(old, **self.dest)["counts"]["knowledge"], 1)
