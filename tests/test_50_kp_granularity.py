"""S14 验收：只读 plan_small_node_merge（同父相邻小节点合并模拟，不接真实 build）。"""
from __future__ import annotations

import copy
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import env  # noqa: E402

sys.path.insert(0, str(env.PROJECT))
from core import kp_granularity as gran  # noqa: E402


def _task(kp_id, node_id, pages, chars, parent=("课件", "第1章")):
    return {"kp_id": kp_id, "node_id": node_id, "node_name": node_id,
            "node_path": list(parent) + [node_id], "level": 3,
            "pages": list(pages), "material_chars_planned": chars}


class TestGranularityPlan(unittest.TestCase):
    def setUp(self):
        self.tasks = [
            _task("KP-a", "a", [1], 500),
            _task("KP-b", "b", [2], 600),
            _task("KP-big", "big", [3, 4, 5], 5000),
            _task("KP-c", "c", [6], 700),
        ]
        self.snapshot = copy.deepcopy(self.tasks)

    def tearDown(self):
        self.assertEqual(self.tasks, self.snapshot, "plan 不得修改输入 tasks")

    def test_default_off_no_groups(self):
        with mock.patch.dict(os.environ, {"KP_MERGE_SMALL_NODES": "0"}):
            res = gran.plan_small_node_merge(self.tasks)
        self.assertFalse(res["enabled"])
        self.assertEqual(res["groups"], [])
        self.assertFalse(res["applied"])
        self.assertFalse(gran.merge_experiment_enabled())

    def test_enabled_plans_adjacent_small_siblings(self):
        with mock.patch.dict(os.environ, {"KP_MERGE_SMALL_NODES": "1"}):
            res = gran.plan_small_node_merge(self.tasks)
        self.assertTrue(res["enabled"])
        self.assertEqual(res["n_groups"], 1)
        self.assertEqual(res["groups"][0]["kp_ids"], ["KP-a", "KP-b"])
        self.assertEqual(res["merged_nodes"], 2)
        self.assertFalse(res["applied"], "本轮不接入真实 build")
        self.assertIn("not wired", res["reason"])

    def test_explicit_enabled_override(self):
        with mock.patch.dict(os.environ, {"KP_MERGE_SMALL_NODES": "0"}):
            res = gran.plan_small_node_merge(self.tasks, enabled=True)
        self.assertTrue(res["enabled"])
        self.assertEqual(res["n_groups"], 1)

    def test_input_is_not_mutated_and_unchanged_flag(self):
        with mock.patch.dict(os.environ, {"KP_MERGE_SMALL_NODES": "1"}):
            res = gran.plan_small_node_merge(self.tasks)
        self.assertTrue(res["input_unchanged"])
        self.assertEqual(len(self.tasks), 4)

    def test_different_parent_not_merged(self):
        tasks = [_task("KP-a", "a", [1], 500, ("课件", "第1章")),
                 _task("KP-b", "b", [2], 600, ("课件", "第2章"))]
        with mock.patch.dict(os.environ, {"KP_MERGE_SMALL_NODES": "1"}):
            res = gran.plan_small_node_merge(tasks)
        self.assertEqual(res["groups"], [])

    def test_non_adjacent_not_merged(self):
        tasks = [_task("KP-a", "a", [1], 500),
                 _task("KP-big", "big", [2, 3, 4], 9000),
                 _task("KP-c", "c", [5], 500)]
        with mock.patch.dict(os.environ, {"KP_MERGE_SMALL_NODES": "1"}):
            res = gran.plan_small_node_merge(tasks)
        self.assertEqual(res["groups"], [], "被大节点隔开的小节点不相邻")

    def test_over_pages_threshold_not_small(self):
        tasks = [_task("KP-a", "a", [1, 2, 3], 500),
                 _task("KP-b", "b", [4, 5, 6], 500)]
        with mock.patch.dict(os.environ, {"KP_MERGE_SMALL_NODES": "1"}):
            res = gran.plan_small_node_merge(tasks, max_pages=2)
        self.assertEqual(res["groups"], [], "页数超过 max_pages 的节点不算小节点")

    def test_over_chars_threshold_not_small(self):
        tasks = [_task("KP-a", "a", [1], 4000),
                 _task("KP-b", "b", [2], 4000)]
        with mock.patch.dict(os.environ, {"KP_MERGE_SMALL_NODES": "1"}):
            res = gran.plan_small_node_merge(tasks, max_chars=3000)
        self.assertEqual(res["groups"], [], "字符超过 max_chars 的节点不算小节点")

    def test_hard_limits_and_page_coverage(self):
        tasks = [_task("KP-a", "a", [1, 2], 500),
                 _task("KP-b", "b", [3, 4], 500),
                 _task("KP-c", "c", [5, 6], 500),
                 _task("KP-d", "d", [7, 8], 500)]
        with mock.patch.dict(os.environ, {"KP_MERGE_SMALL_NODES": "1"}):
            res = gran.plan_small_node_merge(tasks, hard_max_pages=6)
        self.assertGreaterEqual(res["n_groups"], 1)
        for g in res["groups"]:
            self.assertLessEqual(g["n_pages"], 6)
            self.assertTrue(g["page_coverage_unchanged"])
            self.assertEqual(g["pages_before"], g["pages_after"])
            self.assertEqual(g["saved_calls"], g["before_nodes"] - 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
