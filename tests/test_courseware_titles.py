import json
import sqlite3
import shutil
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from core import ingest, kp_build
from core.document_titles import display_titles
from tools import dashboard


class CoursewareTitlesTest(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(':memory:')
        self.conn.row_factory = sqlite3.Row
        self.conn.execute('CREATE TABLE documents (id INTEGER PRIMARY KEY, path TEXT, title TEXT, kind TEXT, extra TEXT, n_chunks INTEGER, status TEXT)')

    def tearDown(self):
        self.conn.close()

    def add(self, did, path, title, kind='pdf', metadata=None):
        self.conn.execute('INSERT INTO documents VALUES (?, ?, ?, ?, ?, 0, ?)',
                          (did, path, title, kind, json.dumps(metadata or {}, ensure_ascii=False), 'ok'))

    def test_distinct_files_use_own_names_despite_common_embedded_title(self):
        res = ingest.extract.ExtractResult()
        res.meta['title'] = '共同模板标题会覆盖所有文件'
        res.blocks.append(ingest.extract.Block(text='共同模板标题会覆盖所有文件', page=1, para=0, kind='heading', level=1))
        for kind, suffix in [('pdf', '.pdf'), ('pptx', '.pptx')]:
            self.assertEqual(ingest._document_title(Path('第一章' + suffix), res, kind, False), '第一章')
            self.assertEqual(ingest._document_title(Path('第二章' + suffix), res, kind, False), '第二章')
        self.assertEqual(ingest._document_title(Path('教材.pdf'), res, 'pdf', True), ingest._title_of(Path('教材.pdf'), res))

    def test_real_pdf_extraction_with_reused_metadata(self):
        import pymupdf
        with tempfile.TemporaryDirectory() as folder:
            for name in ('course-one.pdf', 'course-two.pdf'):
                path = Path(folder) / name
                doc = pymupdf.open()
                doc.new_page().insert_text((72, 72), 'Shared template heading')
                doc.set_metadata({'title': 'Shared copied template metadata title'})
                doc.save(path)
                doc.close()
                result = ingest.extract.extract(path)
                self.assertEqual(result.meta.get('title'), 'Shared copied template metadata title')
                self.assertEqual(ingest._document_title(path, result, 'pdf', False), path.stem)

    def test_real_ingest_of_split_pdf_retains_original_filename(self):
        import pymupdf
        from core import db, multippt
        class StopAfterTitle(Exception):
            pass
        with tempfile.TemporaryDirectory() as folder:
            conn = sqlite3.connect(':memory:')
            conn.row_factory = sqlite3.Row
            db.init_db(conn)
            try:
                for name in ('first.pdf', 'second.pdf'):
                    original = Path(folder) / name
                    pdf = pymupdf.open()
                    pdf.new_page().insert_text((72, 72), 'Shared slide heading and body')
                    pdf.set_metadata({'title': 'Shared copied template metadata title'})
                    pdf.save(original)
                    pdf.close()
                    captured = {}
                    def split_file(src, dst):
                        shutil.copyfile(src, dst)
                        return {'slides': 1}
                    def capture_title(conn_arg, rel, path, kind, title, digest):
                        captured.update(path=path, title=title)
                        raise StopAfterTitle()
                    with patch.object(multippt, 'needs_split', return_value=True), \
                         patch.object(multippt, 'split', side_effect=split_file), \
                         patch.object(ingest, '_ensure_doc_row', side_effect=capture_title):
                        with self.assertRaises(StopAfterTitle):
                            ingest.ingest_file(conn, original, force=True, with_ocr=False, with_figures=False)
                    self.assertEqual(captured['path'].name, original.stem + '_slides.pdf')
                    self.assertEqual(captured['title'], original.stem)
            finally:
                conn.close()

    def test_legacy_repair_is_display_only_and_protects_manual_or_ambiguous_names(self):
        shared = '重复的模板标题'
        self.add(1, r'raw\第一章.pdf', shared, metadata={'title': shared})
        self.add(2, r'raw\第二章.pptx', shared, 'pptx', {'title': shared})
        self.add(3, r'raw\第三章.pdf', '我的名字', metadata={'title': shared})
        self.add(4, r'raw\第四章.pdf', shared, metadata={'title': shared, 'title_manually_renamed': True})
        with patch.object(dashboard, '_lkp_load_dir', return_value={}), patch.object(dashboard.study, 'decks_for', return_value={}):
            items = {x['doc_id']: x['title'] for x in dashboard._artifact_doc_list(self.conn)}
        self.assertEqual(items, {1: '第一章', 2: '第二章', 3: '我的名字', 4: shared})
        self.assertEqual(self.conn.execute('SELECT title FROM documents WHERE id=1').fetchone()[0], shared)
        self.assertEqual(display_titles(self.conn), {1: '第一章', 2: '第二章'})

    def test_mindmap_title_and_only_matching_root_are_repaired(self):
        shared = '重复模板标题'
        self.add(1, 'raw/first.pdf', shared, metadata={'title': shared})
        self.add(2, 'raw/second.pdf', shared, metadata={'title': shared})
        artifact = {'state': dashboard._ARTIFACT_V2_STATE, 'doc': {}, 'summary': {}, 'meta': {}}
        with patch.object(dashboard, '_artifact_load', return_value=artifact), \
             patch.object(dashboard, '_lkp_index', return_value={}), \
             patch.object(dashboard, '_artifact_tree', return_value=([{'name': shared}], {})):
            response = dashboard.api_mindmap(self.conn, 1)
        self.assertEqual(response['title'], 'first')
        self.assertEqual(response['tree'][0]['name'], 'first')
        with patch.object(dashboard, '_artifact_load', return_value=artifact), \
             patch.object(dashboard, '_lkp_index', return_value={}), \
             patch.object(dashboard, '_artifact_tree', return_value=([{'name': '用户改过的导图标题'}], {})):
            response = dashboard.api_mindmap(self.conn, 1)
        self.assertEqual(response['tree'][0]['name'], '用户改过的导图标题')

    def test_regeneration_uses_repaired_title_without_changing_the_database(self):
        shared = '重复模板标题'
        self.add(1, 'raw/first.pdf', shared, metadata={'title': shared})
        self.add(2, 'raw/second.pdf', shared, metadata={'title': shared})
        self.conn.commit()
        with tempfile.TemporaryDirectory() as folder:
            db_path = Path(folder) / 'study.db'
            target = sqlite3.connect(db_path)
            try:
                self.conn.backup(target)
            finally:
                target.close()
            self.assertEqual(kp_build.doc_title_v2(1, db_path), 'first')
            self.assertEqual(kp_build.doc_title_v2(2, db_path), 'second')
            with closing(sqlite3.connect(db_path)) as conn:
                self.assertEqual(conn.execute('SELECT title FROM documents WHERE id=1').fetchone()[0], shared)


if __name__ == '__main__':
    unittest.main()
