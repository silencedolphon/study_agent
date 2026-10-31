"""S16 第一步验收：kp_owners 抽出 claim/release，行为逐字不变、旧入口可访问。"""
from __future__ import annotations

import sys
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import env  # noqa: E402

sys.path.insert(0, str(env.PROJECT))
from core import kp_build, kp_owners  # noqa: E402


class TestOwnershipExtraction(unittest.TestCase):
    def setUp(self):
        self.claimed = {}
        self.lock = threading.RLock()

    def test_same_functions_accessible_from_both_modules(self):
        self.assertIs(kp_build.claim_pages, kp_owners.claim_pages)
        self.assertIs(kp_build.release_pages, kp_owners.release_pages)

    def test_first_come_first_served(self):
        a = kp_owners.claim_pages(self.claimed, self.lock, "A", [1, 2])
        b = kp_owners.claim_pages(self.claimed, self.lock, "B", [2, 3])
        self.assertEqual(a, [1, 2])
        self.assertEqual(b, [3])
        self.assertEqual(self.claimed, {1: "A", 2: "A", 3: "B"})

    def test_release_only_own(self):
        kp_owners.claim_pages(self.claimed, self.lock, "A", [1, 2])
        kp_owners.release_pages(self.claimed, self.lock, "B", [1])
        self.assertEqual(self.claimed.get(1), "A")
        kp_owners.release_pages(self.claimed, self.lock, "A", [1])
        self.assertNotIn(1, self.claimed)

    def test_idempotent_and_invalid_pages(self):
        self.assertEqual(kp_owners.claim_pages(self.claimed, self.lock, "A", [5]), [5])
        self.assertEqual(kp_owners.claim_pages(self.claimed, self.lock, "A", [5]), [5])
        self.assertEqual(kp_owners.claim_pages(self.claimed, self.lock, "A",
                                               ["x", None, 6]), [6])

    def test_null_lock_supported(self):
        self.assertEqual(kp_owners.claim_pages(self.claimed, None, "A", [7]), [7])
        kp_owners.release_pages(self.claimed, None, "A", [7])
        self.assertNotIn(7, self.claimed)

    def test_eight_threads_single_winner(self):
        results = []
        barrier = threading.Barrier(8)
        lock = threading.Lock()

        def worker(i):
            barrier.wait(timeout=5)
            got = kp_owners.claim_pages(self.claimed, self.lock, "K%d" % i, [9])
            with lock:
                results.append(got)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        self.assertEqual(len([r for r in results if r]), 1)

    def test_kp_build_behavior_unchanged(self):
        a = kp_build.claim_pages(self.claimed, self.lock, "A", [10])
        b = kp_build.claim_pages(self.claimed, self.lock, "B", [10])
        self.assertEqual((a, b), ([10], []))
        kp_build.release_pages(self.claimed, self.lock, "A", [10])
        self.assertEqual(kp_build.claim_pages(self.claimed, self.lock, "B", [10]), [10])

    def test_only_small_helpers_moved(self):
        self.assertFalse(hasattr(kp_owners, "build_v2"))
        self.assertFalse(hasattr(kp_owners, "gen_kp_v2"))
        self.assertFalse(hasattr(kp_owners, "plan_kp_tasks_v2"))
        self.assertTrue(hasattr(kp_build, "build_v2"))
        self.assertTrue(hasattr(kp_build, "gen_kp_v2"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
