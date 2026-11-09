"""教材分享包离线往返：不访问用户资料或模型。"""
import json
import tempfile
import unittest
from pathlib import Path
from core import config, db, share, search


class TextbookShareRoundtrip(unittest.TestCase):
    def test_textbook_chapters_and_chunk_references_round_trip(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source, dest = root / 'source', root / 'dest'
            source.mkdir(); dest.mkdir()
            conn = db.connect(source / 'study.db'); db.init_db(conn)
            conn.execute("INSERT INTO documents(id,path,abs_path,kind,title,content_hash,n_chunks,status,role,extra) "
                         "VALUES(1,'raw/book.pdf','missing.pdf','pdf','教材',?,2,'ok','textbook',?)",
                         ('a' * 64, json.dumps({'chunk_scheme': 'textbook-v1'})))
            conn.execute("INSERT INTO chunks(id,doc_id,ord,text,heading_path,source_ref,content_hash) "
                         "VALUES(1,1,0,'教材内容','教材 > 第1章','p.1',?)", ('b' * 64,))
            conn.execute("INSERT INTO chunks(id,doc_id,ord,text,heading_path,source_ref,content_hash) "
                         "VALUES(2,1,1,'第二块','教材 > 第1章','p.2',?)", ('c' * 64,))
            conn.execute("INSERT INTO embeddings(chunk_id,model,dim,vec) VALUES(1,?,?,?)",
                         (config.EMBED_MODEL_REPO, config.EMBED_DIM, b'\0' * (4 * config.EMBED_DIM)))
            conn.commit(); conn.close()
            knowledge = source / 'logs' / 'knowledge'; knowledge.mkdir(parents=True)
            artifacts = {
                '1-章节细目.json': {'meta': {'doc_id': 1}, 'chapters': [{'sections': []}]},
                '1-预习总结-第1章.json': {'meta': {'doc_id': 1}, 'sections': [{'chunk_ids': [1, 2]}]},
                '1-教材知识点-第1章.json': {'doc_id': 1, 'chapter': 1,
                    'points': [{'title': '受体', 'source_ids': [1, 2]}],
                    'terms': [{'term': '受体', 'source_ids': [2]}]},
            }
            for name, data in artifacts.items():
                (knowledge / name).write_text(json.dumps(data, ensure_ascii=False), encoding='utf8')
            package = root / 'book.zip'
            exported = share.export_doc(1, package, db_path=source/'study.db', log_dir=source/'logs',
                                        raw_dir=source/'raw', root=source)
            self.assertEqual(len(exported['manifest']['knowledge']), 3)
            conn = db.connect(dest/'study.db'); db.init_db(conn)
            conn.execute("INSERT INTO documents(id,path,abs_path,kind,title,content_hash) VALUES(1,'other','other','pdf','other',?)", ('d' * 64,))
            conn.execute("INSERT INTO chunks(id,doc_id,ord,text,content_hash) VALUES(1,1,0,'other',?)", ('e' * 64,))
            conn.commit(); conn.close()
            kw = dict(db_path=dest/'study.db', log_dir=dest/'logs', raw_dir=dest/'raw', root=dest)
            result = share.import_package(package, **kw)
            self.assertEqual(result['status'], 'imported')
            self.assertEqual(result['doc_id'], 2)
            self.assertEqual(result['counts']['chunks'], 2)
            self.assertEqual(result['counts']['embeddings'], 1)
            conn = db.connect(dest/'study.db')
            row = conn.execute('SELECT role,extra FROM documents WHERE id=2').fetchone()
            chunks = conn.execute('SELECT id,heading_path FROM chunks WHERE doc_id=2 ORDER BY ord').fetchall()
            conn.close()
            self.assertEqual(row['role'], 'textbook')
            self.assertEqual(json.loads(row['extra'])['chunk_scheme'], 'textbook-v1')
            self.assertIn('第1章', chunks[0]['heading_path'])
            ids = [r['id'] for r in chunks]
            out = dest/'logs'/'knowledge'
            book = json.loads((out/'2-教材知识点-第1章.json').read_text(encoding='utf8'))
            preview = json.loads((out/'2-预习总结-第1章.json').read_text(encoding='utf8'))
            self.assertEqual(book['doc_id'], 2)
            self.assertEqual(book['points'][0]['source_ids'], ids)
            self.assertEqual(book['terms'][0]['source_ids'], ids[1:])
            self.assertEqual(preview['sections'][0]['chunk_ids'], ids)
            conn = db.connect(dest/'study.db')
            hits = search.search(conn, '第二块', mode='keyword', corpus='all')
            conn.close()
            self.assertIn(ids[1], [hit.chunk_id for hit in hits])
            self.assertTrue(any('缺少向量' in warning for warning in result['warnings']))
            self.assertEqual(share.import_package(package, **kw)['status'], 'skipped')
            self.assertEqual(json.loads((out/'2-教材知识点-第1章.json').read_text(encoding='utf8')), book)


if __name__ == '__main__':
    unittest.main()
