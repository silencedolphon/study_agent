import random
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from core import config, review


class ReviewExclusions(unittest.TestCase):
    def test_exclusion_cannot_change_session_during_submit(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with patch.object(config, 'LOG_DIR', root/'a'), patch.object(config, 'DB_PATH', root/'a.db'), \
                 patch.object(review, '_kp_artifact', return_value=(root/'kp', [{'id':'x','name':'X'}, {'id':'y','name':'Y'}], {})), \
                 patch.object(review, '_doc_title_for', return_value='test'):
                start = review.start_selftest(1)
                entered, release, done = threading.Event(), threading.Event(), threading.Event()
                original = review._atomic_write_json
                outcome = {}
                def slow_write(data, path):
                    entered.set()
                    self.assertTrue(release.wait(5))
                    original(data, path)
                def submit():
                    outcome['submitted'] = review.submit_selftest(1, start['session_id'], {})
                def remove():
                    outcome['excluded'] = review.update_selftest_exclusion(1, start['session_id'], start['items'][0]['kp_id'])
                    done.set()
                with patch.object(review, '_atomic_write_json', side_effect=slow_write):
                    first = threading.Thread(target=submit)
                    second = threading.Thread(target=remove)
                    first.start()
                    try:
                        self.assertTrue(entered.wait(5))
                        second.start()
                        self.assertFalse(done.wait(0.1))
                    finally:
                        release.set()
                        first.join(5)
                        if second.ident:
                            second.join(5)
                self.assertTrue(outcome['submitted']['ok'])
                self.assertFalse(outcome['excluded']['ok'])

    def test_persist_replace_restore_and_scope(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            items = [{'id': str(i), 'name': 'point'+str(i), 'summary': 'text'} for i in range(4)]
            with patch.object(config, 'LOG_DIR', root/'a'), patch.object(config, 'DB_PATH', root/'a.db'), \
                 patch.object(review, '_kp_artifact', return_value=(root/'kp.json', items, {})), \
                 patch.object(review, '_doc_title_for', return_value='test'):
                start = review.start_selftest(1, rng=random.Random(1))
                sid = start['session_id']
                keep = start['items'][1]['kp_id']
                removed = start['items'][0]['kp_id']
                new = review.update_selftest_exclusion(1, sid, removed)
                self.assertEqual(new['n_sample'], 2)
                self.assertIn(keep, [x['kp_id'] for x in new['items']])
                self.assertNotIn(removed, [x['kp_id'] for x in new['items']])
                self.assertNotIn(removed, [x['kp_id'] for x in review.start_selftest(1)['items']])
                with patch.object(config, 'DB_PATH', root/'b.db'):
                    self.assertFalse(review.update_selftest_exclusion(1, sid, keep)['ok'])
                    self.assertFalse(review.submit_selftest(1, sid, {})['ok'])
                res = review.submit_selftest(1, sid, {keep: True})
                self.assertEqual(res['n_sample'], 2)
                self.assertEqual(res['n_mastered'], 1)
                self.assertNotIn(removed, [x['kp_id'] for x in res['results']])

                # 即使确认完会话后配置被切换，提交也只写会话所属学科路径。
                race = review.start_selftest(1)
                real_write = review._atomic_write_json
                def switch_during_write(data, path):
                    with patch.object(config, 'LOG_DIR', root/'b'):
                        real_write(data, path)
                with patch.object(review, '_atomic_write_json', side_effect=switch_during_write):
                    self.assertTrue(review.submit_selftest(1, race['session_id'], {})['ok'])
                self.assertTrue((root/'a'/'review'/'1-自测.json').exists())
                self.assertFalse((root/'b'/'review'/'1-自测.json').exists())

    def test_all_excluded_can_be_restored(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with patch.object(config, 'LOG_DIR', root/'a'), patch.object(config, 'DB_PATH', root/'a.db'), \
                 patch.object(review, '_kp_artifact', return_value=(root/'kp.json', [{'id':'only','name':'one'}], {})), \
                 patch.object(review, '_doc_title_for', return_value='test'):
                start = review.start_selftest(1)
                result = review.update_selftest_exclusion(1, start['session_id'], 'only')
                self.assertEqual(result['n_sample'], 0)
                self.assertFalse(review.submit_selftest(1, start['session_id'], {})['ok'])
                again = review.start_selftest(1)
                self.assertEqual(again['items'], [])
                restored = review.update_selftest_exclusion(1, again['session_id'], restore=True)
                self.assertEqual([x['kp_id'] for x in restored['items']], ['only'])

