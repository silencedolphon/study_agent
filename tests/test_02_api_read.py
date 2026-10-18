"""只读 API 覆盖：文档/检索/知识点/复习/图谱/预导/分享/学科/设置。

被测实例使用真实库的**副本**（main.db 来自 subjects/生药药动，含 3 文档/1135 切块）。
所有断言只看「接口是否按约定返回数据」，不在只读模块里做任何写操作。
"""

import sqlite3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import env  # noqa: E402

_SRV = None
_MAIN_DB = None
_CHUNK_ID = None


def setUpModule():
    global _SRV, _MAIN_DB, _CHUNK_ID
    env.prepare(clean_data=True)
    _MAIN_DB = env.DATA_DIR / "main.db"
    conn = sqlite3.connect(str(_MAIN_DB))
    try:
        row = conn.execute(
            "SELECT id FROM chunks WHERE doc_id=2 ORDER BY id LIMIT 1").fetchone()
        _CHUNK_ID = int(row[0]) if row else 0
    finally:
        conn.close()
    _SRV = env.Server(tag="read")
    _SRV.start()


def tearDownModule():
    if _SRV is not None:
        _SRV.stop()


class TestReadApi(unittest.TestCase):
    def _ok(self, path, **params):
        st, obj = _SRV.get_json(path, timeout=180, **params)
        self.assertEqual(st, 200, f"{path} 返回 {st}: {obj}")
        self.assertIsInstance(obj, dict)
        return obj

    def test_stats(self):
        """学习总览统计：文档/切块/知识点计数。"""
        d = self._ok("/api/stats")
        self.assertEqual(d["documents"]["total"], 3)
        self.assertGreaterEqual(d["documents"]["chunks"], 1000)
        self.assertIn("by_kind", d["documents"])
        self.assertEqual(d["knowledge"]["total"], 0, "副本库应无知识点（真实库现状）")
        self.assertIn("weakest", d)

    def test_docs(self):
        """文档列表：3 份真实入库文档。"""
        d = self._ok("/api/docs")
        docs = d["documents"]
        self.assertEqual(len(docs), 3)
        self.assertEqual({x["id"] for x in docs}, {2, 3, 4})
        for x in docs:
            self.assertTrue(x["title"])
            self.assertGreater(x["n_chunks"], 0)

    def test_doc_detail(self):
        """文档详情：正例有 outline，反例 error。"""
        d = self._ok("/api/doc", id=2)
        self.assertEqual(d["document"]["id"], 2)
        self.assertIn("药物排泄", d["document"]["title"])
        self.assertIsInstance(d["outline"], list)
        bad = self._ok("/api/doc", id=0)
        self.assertIn("error", bad)

    def test_chunk(self):
        """切块及相邻上下文。"""
        self.assertGreater(_CHUNK_ID, 0, "副本库里找不到 doc_id=2 的切块")
        d = self._ok("/api/chunk", id=_CHUNK_ID, before=1, after=1)
        self.assertEqual(d["target"]["chunk_id"], _CHUNK_ID)
        self.assertGreaterEqual(len(d["context"]), 1)
        bad = self._ok("/api/chunk", id=0)
        self.assertIn("error", bad)

    def test_search(self):
        """混合检索：非空查询有命中，空查询安全返回 0 条。"""
        d = self._ok("/api/search", q="药物代谢", k=3)
        self.assertGreaterEqual(d["n"], 1, "检索无命中")
        hit = d["hits"][0]
        for key in ("chunk_id", "doc_id", "title", "ref", "why", "confidence"):
            self.assertIn(key, hit)
        self.assertIn(hit["doc_id"], (2, 3, 4))
        empty = self._ok("/api/search", q="", k=3)
        self.assertEqual(empty["n"], 0)

    def test_kps(self):
        """知识点列表：空库返回空列表（结构正确）。"""
        d = self._ok("/api/kps")
        self.assertEqual(d["knowledge_points"], [])

    def test_kp_detail(self):
        """知识点详情：不存在的 id 返回 error 而非 500。"""
        d = self._ok("/api/kp", id=0)
        self.assertIn("error", d)

    def test_queue(self):
        """复习队列：空库返回空队列。"""
        d = self._ok("/api/queue", limit=5)
        self.assertEqual(d["queue"], [])

    def test_deck_calendar(self):
        """课件复习月历：返回当月结构与 today。"""
        d = self._ok("/api/deck-calendar", ym="2026-10")
        self.assertEqual(d["ym"], "2026-10")
        self.assertIsInstance(d["days"], dict)
        self.assertIn("today", d["stats"])
        self.assertEqual(d["stats"]["plans"], 0)

    def test_mistakes(self):
        """错题本：空库结构与统计。"""
        d = self._ok("/api/mistakes")
        self.assertEqual(d["mistakes"], [])
        self.assertEqual(d["stats"]["total"], 0)

    def test_graph(self):
        """知识点依赖图谱：空库返回空 nodes/edges。"""
        d = self._ok("/api/graph")
        self.assertEqual(d["nodes"], [])
        self.assertEqual(d["edges"], [])

    def test_mindmap(self):
        """思维导图：单文档返回 artifact 状态与树结构。"""
        d = self._ok("/api/mindmap", doc_id=2)
        self.assertEqual(d["doc_id"], 2)
        self.assertIn("tree", d)
        self.assertIn("artifact_state", d)
        # doc_id=0 返回文档选择列表（不是 500）
        listing = self._ok("/api/mindmap", doc_id=0)
        self.assertIn("documents", listing)

    def test_terms(self):
        """名词解释：单文档返回产物状态与说明。"""
        d = self._ok("/api/terms", doc_id=2)
        self.assertEqual(d["doc_id"], 2)
        self.assertEqual(d["n_kps"], 0)
        self.assertIn("terms_state", d)

    def test_page_kps(self):
        """按页知识点定位：无页码出处时给出明确 note。"""
        d = self._ok("/api/page-kps", doc_id=2, page=0, source_ref="")
        self.assertEqual(d["doc_id"], 2)
        self.assertIsInstance(d["pages"], list)
        self.assertIn("ref_kind", d)

    def test_kp_build_options(self):
        """知识点整理候选：返回候选列表与成本字段（结构契约）。"""
        d = self._ok("/api/kp-build/options")
        self.assertEqual(d["n"], len(d["documents"]))
        self.assertIn("progress", d)
        batch = self._ok("/api/kp-build/batch/options")
        for key in ("documents", "n", "stages", "progress", "max_workers_default"):
            self.assertIn(key, batch)

    def test_raw(self):
        """原始资料清单：看到 isolated raw/ 下的 fixture 与孤儿文档。"""
        d = self._ok("/api/raw")
        self.assertIn(str(env.VERIFY_DIR), d["raw_dir"])
        names = {f["name"] for f in d["files"]}
        self.assertIn("验收样例.md", names, f"raw 清单缺 fixture：{names}")
        orphans = {o.get("id") for o in d["orphans"]}
        self.assertTrue({2, 3, 4} & orphans, "真实库文档应出现在 raw 孤儿列表（只读展示）")

    def test_firstrun(self):
        """首次运行体检：docs=3、非空库、路径在隔离目录。"""
        d = self._ok("/api/firstrun")
        self.assertEqual(d["docs"], 3)
        self.assertFalse(d["empty"])
        self.assertIsInstance(d["credential"], dict)

    def test_notes_read(self):
        """笔记列表/详情/按来源反查（空库结构）。"""
        d = self._ok("/api/notes")
        self.assertEqual(d["notes"], [])
        missing = self._ok("/api/note", name="不存在的笔记.md")
        self.assertIn("error", missing)
        by = self._ok("/api/notes/by_kp", kp_id="", doc_id=0)
        self.assertEqual(by["notes"], [])
        self.assertEqual(by["kp_id"], "")

    def test_subjects(self):
        """学科列表：default + 沙箱 fixture 学科，路径可核对。"""
        d = self._ok("/api/subjects")
        self.assertEqual(d["current"], "default")
        slugs = {s["slug"] for s in d["subjects"]}
        self.assertIn("default", slugs)
        self.assertIn("验收-药物化学", slugs)
        self.assertTrue(d["subjects"][0]["db_exists"])
        for key in ("DB_PATH", "RAW_DIR", "NOTES_DIR", "LOG_DIR"):
            self.assertIn(key, d["paths"], f"/api/subjects 缺路径字段 {key}")
            self.assertIn(str(env.VERIFY_DIR), d["paths"][key],
                          f"学科路径未隔离：{key}={d['paths'][key]}")

    def test_settings_read(self):
        """设置读取：四条管线的字段与提示词槽位。"""
        d = self._ok("/api/settings")
        pipelines = {p["id"] for p in d["pipelines"]}
        self.assertTrue({"chat", "kp", "ingest", "deck"} <= pipelines,
                        f"设置窗口管线缺失：{pipelines}")
        self.assertIsInstance(d["credential"], dict)
        self.assertEqual(len(d["prompts"]), 3)
        self.assertEqual(len(d["builtin_prompts"]), 3)

    def test_settings_prompt(self):
        """设置窗口提示词：内置提示词可读；未知 builtin 返回 error。"""
        d = self._ok("/api/settings/prompt", builtin="chat_system")
        self.assertTrue(d.get("ok"))
        self.assertGreater(d.get("chars", 0), 100)
        self.assertIn("text", d)
        bad = self._ok("/api/settings/prompt", builtin="no_such")
        self.assertIn("error", bad)

    def test_progress_snapshots(self):
        """四个长任务进度快照接口：空闲态结构正确。"""
        for path, key in (
            ("/api/ingest/progress", "active"),
            ("/api/kp-build/progress", "active"),
            ("/api/ingest/batch/progress", "running"),
            ("/api/kp-build/batch/progress", "active"),
        ):
            d = self._ok(path)
            self.assertIn(key, d, f"{path} 缺 {key}")
            self.assertFalse(d[key], f"{path} 空闲态应 {key}=false")

    def test_share(self):
        """分享包：列表目录在沙箱 share/，进度空闲。"""
        d = self._ok("/api/share/packages")
        self.assertTrue(d["ok"])
        self.assertEqual(Path(d["dir"]).resolve(), (env.SANDBOX_DIR / "share").resolve())
        self.assertEqual(d["packages"], [])
        p = self._ok("/api/share/progress")
        self.assertFalse(p["active"])

    def test_chat_status(self):
        """问答管线状态：只读汇报模型/密钥是否就绪（不联网）。"""
        d = self._ok("/api/chat/status")
        self.assertIn("ok", d)
        self.assertIn("model", d)
        self.assertIn("key", d)
        self.assertIn("base", d)


if __name__ == "__main__":
    unittest.main(verbosity=2)
