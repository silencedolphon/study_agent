"""P2-C / S11：课件知识点页面归属回归（零模型；纯函数 + 覆盖统计）。"""
from __future__ import annotations

import sys
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import env  # noqa: E402

sys.path.insert(0, str(env.PROJECT))
from core import kp_build  # noqa: E402


class TestClaimRelease(unittest.TestCase):
    def setUp(self):
        self.claimed = {}
        self.lock = threading.RLock()

    def test_first_come_first_served(self):
        got_a = kp_build.claim_pages(self.claimed, self.lock, "A", [1, 2])
        got_b = kp_build.claim_pages(self.claimed, self.lock, "B", [2, 3])
        self.assertEqual(got_a, [1, 2])
        self.assertEqual(got_b, [3], "被 A 先占的页 B 不能抢")
        self.assertEqual(self.claimed, {1: "A", 2: "A", 3: "B"})

    def test_release_only_own_and_reusable(self):
        kp_build.claim_pages(self.claimed, self.lock, "A", [1, 2])
        kp_build.release_pages(self.claimed, self.lock, "B", [1])   # 别人释放无效
        self.assertEqual(self.claimed.get(1), "A")
        kp_build.release_pages(self.claimed, self.lock, "A", [1])
        self.assertNotIn(1, self.claimed)
        self.assertEqual(kp_build.claim_pages(self.claimed, self.lock, "B", [1]), [1])

    def test_idempotent_same_owner_and_invalid_pages(self):
        self.assertEqual(kp_build.claim_pages(self.claimed, self.lock, "A", [5]), [5])
        self.assertEqual(kp_build.claim_pages(self.claimed, self.lock, "A", [5]), [5])
        self.assertEqual(kp_build.claim_pages(self.claimed, self.lock, "A",
                                              ["x", None, 6]), [6])
        kp_build.release_pages(self.claimed, self.lock, "A", [5, "x"])
        self.assertNotIn(5, self.claimed)

    def test_eight_threads_only_one_owner(self):
        results = []
        barrier = threading.Barrier(8)
        lock = threading.Lock()

        def worker(i):
            barrier.wait(timeout=5)
            got = kp_build.claim_pages(self.claimed, self.lock, "K%d" % i, [7])
            with lock:
                results.append(got)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        winners = [r for r in results if r]
        self.assertEqual(len(winners), 1, "同一页只能有一个 owner")
        self.assertEqual(self.claimed.get(7), self.claimed.get(7))

    def test_resume_overlap_and_legacy(self):
        prev = {"meta": {"pipeline_version": kp_build.PIPELINE_VERSION_V1},
                "knowledge_points": [{"id": "a", "summary": "s", "pages": [1, 2]},
                                     {"id": "b", "summary": "t", "pages": [2, 3]}]}
        self.assertEqual(kp_build.resume_set(prev), set(), "有重叠的旧产物一条都不续")
        ok = {"meta": {"pipeline_version": kp_build.PIPELINE_VERSION_V1},
              "knowledge_points": [{"id": "a", "summary": "s", "pages": [1, 2]},
                                   {"id": "b", "summary": "t", "pages": [3]}]}
        self.assertEqual(kp_build.resume_set(ok), {1, 2, 3})
        old = {"meta": {"pipeline_version": "old-v1"},
               "knowledge_points": [{"summary": "s", "pages": [1]}]}
        self.assertEqual(kp_build.resume_set(old), set(), "旧版产物不续跑")

    def test_resume_prefill_cannot_be_stolen(self):
        self.claimed[5] = "old-kp"
        self.assertEqual(kp_build.claim_pages(self.claimed, self.lock, "new-kp", [5]), [])
        self.assertEqual(self.claimed[5], "old-kp")


class TestCoverage(unittest.TestCase):
    def _cov(self, recs):
        est = {"pages_all": list(range(1, 5)), "max_page": 4,
               "pages_with_blocks": [1, 2, 3, 4]}
        norm = {"conflicts": [], "filled": [], "fallback": False,
                "page_owner": {1: "t1", 2: "t1", 3: "t2", 4: "t2"}}
        tasks = [{"kp_id": "t1", "pages": [1, 2]},
                 {"kp_id": "t2", "pages": [3, 4]}]
        return kp_build._v2_coverage(est, recs, norm, tasks)

    def test_final_overlap_empty_when_unique(self):
        recs = [{"id": "a", "pages": [1, 2]}, {"id": "b", "pages": [3, 4]}]
        cov = self._cov(recs)
        self.assertEqual(cov["overlap_pages"], [])
        self.assertEqual(cov["uncovered"], [])
        self.assertEqual(cov["n_covered"], 4)

    def test_overlap_detected_and_uncovered_reason(self):
        recs = [{"id": "a", "pages": [1, 2]}, {"id": "b", "pages": [2, 3]}]
        cov = self._cov(recs)
        self.assertEqual(cov["overlap_pages"], [2])
        self.assertIn(4, cov["uncovered"])
        self.assertIn("尚未生成", cov["uncovered_reasons"]["4"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
