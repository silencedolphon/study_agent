"""Isolated smoke coverage for the knowledge-point vector index and share import."""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import config, db, kp_vector, share


class FakeEmbedder:
    dim = config.EMBED_DIM

    def embed_documents(self, texts, batch_size=16, progress=False):
        rows = []
        for text in texts:
            vec = np.zeros(self.dim, dtype=np.float32)
            for token in str(text).lower().split():
                pos = int.from_bytes(hashlib.sha256(token.encode("utf-8")).digest()[:4], "little") % self.dim
                vec[pos] += 1.0
            if not np.any(vec):
                vec[0] = 1.0
            rows.append(vec)
        return np.vstack(rows)

    def embed_query(self, text):
        return self.embed_documents([text])[0]


class KnowledgePointVectorStoreTest(unittest.TestCase):
    def test_share_import_indexes_summaries_and_sync_is_idempotent(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            source, dest = base / "source", base / "dest"
            src_db = source / "index" / "study.db"
            src_logs = source / "logs"
            conn = db.connect(src_db)
            db.init_db(conn)
            conn.execute(
                "INSERT INTO documents(id,path,abs_path,kind,title,content_hash,n_chunks,status,role) "
                "VALUES(1,'raw/lecture.pdf','missing.pdf','pdf','Calcium lecture',?,1,'ok','courseware')",
                ("a" * 64,),
            )
            conn.execute(
                "INSERT INTO chunks(id,doc_id,ord,text,heading_path,source_ref,content_hash) "
                "VALUES(1,1,0,'Calcium channel blocker lecture','Pharmacology','p.1',?)",
                ("b" * 64,),
            )
            conn.execute(
                "INSERT INTO embeddings(chunk_id,model,dim,vec) VALUES(1,?,?,?)",
                (config.EMBED_MODEL_REPO, config.EMBED_DIM,
                 np.eye(1, config.EMBED_DIM, 0, dtype=np.float32).tobytes()),
            )
            conn.commit()
            conn.close()

            know_dir = src_logs / "knowledge"
            know_dir.mkdir(parents=True)
            artifact = {
                "meta": {"doc_id": 1, "pipeline_version": "C-0058-v4"},
                "knowledge_points": [{
                    "id": "N01", "name": "Calcium channel blockers",
                    "summary": "Calcium channel blockers reduce calcium influx and relax vascular smooth muscle.",
                    "points": ["They reduce peripheral resistance."], "pages": [1],
                }],
            }
            (know_dir / f"1{kp_vector.KP_SUFFIX}").write_text(
                json.dumps(artifact, ensure_ascii=False), encoding="utf-8")
            package = base / "lecture.sharepack"
            share.export_doc(1, package, db_path=src_db, log_dir=src_logs,
                             raw_dir=source / "raw", root=source)

            fake = FakeEmbedder()
            with patch("core.embed.get_embedder", return_value=fake):
                result = share.import_package(
                    package, db_path=dest / "index" / "study.db", log_dir=dest / "logs",
                    raw_dir=dest / "raw", root=dest,
                )
            self.assertEqual(result["status"], "imported")
            self.assertEqual(result["knowledge_vector"]["status"], "ok")
            self.assertEqual(result["knowledge_vector"]["indexed"], 1)
            self.assertEqual(result["knowledge_vector"]["embedded"], 1)

            target_db = dest / "index" / "study.db"
            conn = db.connect(target_db)
            row = conn.execute(
                "SELECT doc_id,kp_uid,name,summary,model,dim,vec FROM knowledge_point_embeddings"
            ).fetchone()
            conn.close()
            self.assertEqual(int(row["doc_id"]), result["doc_id"])
            self.assertEqual(row["kp_uid"], "N01")
            self.assertEqual(row["model"], config.EMBED_MODEL_REPO)
            self.assertEqual(int(row["dim"]), config.EMBED_DIM)
            self.assertEqual(len(row["vec"]), config.EMBED_DIM * 4)

            imported_artifact = dest / "logs" / "knowledge" / f"{result['doc_id']}{kp_vector.KP_SUFFIX}"
            with patch("core.embed.get_embedder", return_value=fake):
                again = kp_vector.sync_artifact(result["doc_id"], imported_artifact,
                                                db_path=target_db)
                hits = kp_vector.search("calcium channel blockers", db_path=target_db,
                                        embedder=fake)
            self.assertEqual(again["embedded"], 0)
            self.assertEqual(len(hits), 1)
            self.assertEqual(hits[0]["name"], "Calcium channel blockers")
            self.assertGreater(hits[0]["score"], 0.7)

            updated_artifact = json.loads(imported_artifact.read_text(encoding="utf-8"))
            updated_artifact["knowledge_points"] = []
            imported_artifact.write_text(json.dumps(updated_artifact), encoding="utf-8")
            removed = kp_vector.sync_artifact(result["doc_id"], imported_artifact,
                                              db_path=target_db, embedder=fake)
            self.assertEqual(removed["removed"], 1)
            conn = db.connect(target_db)
            remaining = conn.execute(
                "SELECT COUNT(*) FROM knowledge_point_embeddings WHERE doc_id=?",
                (result["doc_id"],),
            ).fetchone()[0]
            conn.close()
            self.assertEqual(int(remaining), 0)


if __name__ == "__main__":
    unittest.main()
