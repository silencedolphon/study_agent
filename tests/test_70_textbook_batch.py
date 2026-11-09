import time
import unittest
from unittest import mock

from core import textbook_batch as batch


class TestTextbookBatch(unittest.TestCase):
    def setUp(self):
        with batch._lock:
            batch._state.clear()
            batch._state.update(active=False, status='', items=[])
            batch._plans.clear()

    def wait(self):
        for _ in range(500):
            s = batch.snapshot()
            if not s['active']:
                return s
            time.sleep(.01)
        self.fail('batch did not finish')

    def test_queue_continues_after_failure_and_rejects_duplicate(self):
        launched = []
        def estimate(mode, doc, chapter):
            return {'ok': True, 'signature': str(chapter), 'calls': 1}
        def launch(mode, doc, chapter, est):
            launched.append(chapter)
            if chapter == 2:
                return {'started': False, 'error': '模型不可用'}
            return {'started': True}
        p = batch.plan('textbook', 9, [1, 2, 3], estimate)
        r = batch.start(p['token'], estimate, launch,
                        lambda _: {'active': False, 'status': 'done'}, lambda: False)
        self.assertTrue(r['started'])
        s = self.wait()
        self.assertEqual(launched, [1, 2, 3])
        self.assertEqual([x['status'] for x in s['items']], ['done', 'failed', 'done'])
        self.assertIn('模型不可用', s['items'][1]['error'])
        self.assertFalse(batch.start(p['token'], estimate, launch, lambda _: {}, lambda: False)['ok'])

    def test_changed_estimate_does_not_invoke_model(self):
        est = mock.Mock(return_value={'ok': True, 'signature': 'old', 'calls': 1})
        p = batch.plan('preview', 9, [1], est)
        est.return_value = {'ok': True, 'signature': 'new', 'calls': 2}
        launch = mock.Mock()
        batch.start(p['token'], est, launch, lambda _: {}, lambda: False)
        s = self.wait()
        launch.assert_not_called()
        self.assertIn('变化', s['items'][0]['error'])

    def test_other_pipeline_busy_rejects_batch(self):
        est = lambda *_: {'ok': True, 'calls': 0}
        p = batch.plan('textbook', 9, [1], est)
        self.assertFalse(batch.start(p['token'], est, None, None, lambda: True)['ok'])


if __name__ == '__main__':
    unittest.main()
