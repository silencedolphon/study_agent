"""CLI 覆盖：检查/统计/文档/检索/切块/复习/知识点/错题/笔记/入库。

全部命令跑在 `_work/verify/data/` 下的**数据库副本**上：
  · cli.db   ← 复制自 main.db（3 文档 / 1135 切块）
  · ingest.db← 复制自 empty.db（空 schema 库），只用于 ingest 用例
"""

import json
import sqlite3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import env  # noqa: E402

CLI_DB = None
INGEST_DB = None
INGEST_SRC = None


def setUpModule():
    global CLI_DB, INGEST_DB, INGEST_SRC
    env.prepare(clean_data=True)
    CLI_DB = env.DATA_DIR / "cli.db"
    INGEST_DB = env.DATA_DIR / "ingest.db"
    env.copy_sqlite(env.DATA_DIR / "main.db", CLI_DB)
    # 空库：0 字节文件，由 study.py init 时建 schema（确定性，不依赖真实库现状）
    INGEST_DB.write_bytes(b"")
    INGEST_SRC = env.DATA_DIR / "ingest_src" / "小样.md"


def _cli(args, db=None, timeout=300):
    r = env.run_study(args, db_path=db or CLI_DB, timeout=timeout)
    return r


def _json(r):
    return json.loads(r["stdout"])


def _ok_json(test, args, db=None):
    r = _cli(args, db=db)
    test.assertEqual(r["rc"], 0, f"`{' '.join(args)}` 失败 rc={r['rc']}: {r['stderr'][:500]}")
    try:
        return _json(r)
    except ValueError:
        test.fail(f"`{' '.join(args)}` 输出不是合法 JSON: {r['stdout'][:300]}")


class TestCli(unittest.TestCase):
    def test_check(self):
        """study.py check：依赖/模型/数据库全绿，且数据库指向隔离副本。"""
        d = _ok_json(self, ["--json", "check"])
        self.assertEqual(Path(d["root"]).resolve(), env.SANDBOX_DIR.resolve())
        self.assertTrue(d["python"].startswith("3.13"), d["python"])
        failed = [c for c in d["checks"] if not c.get("ok")]
        self.assertEqual(failed, [], f"自检失败项：{failed}")
        names = {c["name"] for c in d["checks"]}
        self.assertIn("嵌入推理", names)
        db_check = next(c for c in d["checks"] if c["name"] == "数据库")
        self.assertIn(str(CLI_DB), db_check["detail"])
        self.assertIn("FTS5=开", db_check["detail"])

    def test_stats(self):
        """study.py stats：与 API 一致的文档/知识点统计。"""
        d = _ok_json(self, ["--json", "stats"])
        self.assertEqual(d["documents"]["total"], 3)
        self.assertGreaterEqual(d["documents"]["chunks"], 1000)

    def test_docs(self):
        """study.py docs：列出 3 份已入库文档。"""
        d = _ok_json(self, ["--json", "docs"])
        self.assertEqual(d["count"], 3)
        self.assertEqual({x["doc_id"] for x in d["docs"]}, {2, 3, 4})

    def test_docs_outline(self):
        """study.py docs --outline 2：返回标题段大纲。"""
        d = _ok_json(self, ["--json", "docs", "--outline", "2"])
        self.assertEqual(d["doc_id"], 2)
        self.assertGreaterEqual(len(d["outline"]), 1)
        for key in ("heading", "n_chunks", "first_chunk"):
            self.assertIn(key, d["outline"][0])

    def test_search(self):
        """study.py search：混合检索返回命中与出处。"""
        d = _ok_json(self, ["--json", "search", "药物代谢", "-k", "3"])
        self.assertGreaterEqual(d["count"], 1)
        self.assertIn("chunk_id", d["hits"][0])
        self.assertIn("path", d["hits"][0])

    def test_show(self):
        """study.py show：有效切块返回上下文；无效切块 rc!=0 且报错清晰。"""
        conn = sqlite3.connect(str(CLI_DB))
        try:
            cid = conn.execute("SELECT id FROM chunks ORDER BY id LIMIT 1").fetchone()[0]
        finally:
            conn.close()
        d = _ok_json(self, ["--json", "show", str(cid)])
        self.assertEqual(d["center"], int(cid))
        self.assertGreaterEqual(len(d["blocks"]), 1)
        self.assertIn("text", d["blocks"][0])
        r = _cli(["--json", "show", "999999999"])
        self.assertNotEqual(r["rc"], 0, "不存在的切块应返回非 0")
        self.assertIn("切块", r["stderr"])

    def test_review(self):
        """study.py review：新点入队规则（CLI 只列到期）+ 评分写入 SM-2。"""
        d = _ok_json(self, ["--json", "kp", "add", "CLI复习知识点", "--summary", "自动验收"])
        kp_id = int(d["kp_id"])
        # 新点（未复习、无到期日）不在 CLI 的「今日到期」队列里
        q = _ok_json(self, ["--json", "review", "--limit", "20"])
        self.assertNotIn(kp_id, {x["id"] for x in q["queue"]})
        # 评分
        st = _ok_json(self, ["--json", "review", str(kp_id), "--grade", "4"])
        self.assertEqual(st["kp_id"], kp_id)
        self.assertEqual(st["reps"], 1)
        self.assertGreaterEqual(st["interval_days"], 1.0)
        self.assertIn("ease", st)
        # 缺 --grade 是用法错误（rc=2）
        bad = _cli(["--json", "review", str(kp_id)])
        self.assertEqual(bad["rc"], 2)

    def test_kp(self):
        """study.py kp add/list：知识点写入并出现在列表。"""
        d = _ok_json(self, ["--json", "kp", "add", "CLI知识点A", "--subject", "验收"])
        kp_id = int(d["kp_id"])
        listing = _ok_json(self, ["--json", "kp", "list"])
        self.assertGreaterEqual(listing["count"], 1)
        row = next((k for k in listing["kps"] if k["id"] == kp_id), None)
        self.assertIsNotNone(row)
        self.assertEqual(row["name"], "CLI知识点A")

    def test_mistake(self):
        """study.py mistake add/stats：错题登记与按错因统计。"""
        d = _ok_json(self, ["--json", "mistake", "add", "--question", "验收问题",
                            "--my", "错误答案", "--right", "正确答案",
                            "--reason", "概念混淆"])
        self.assertGreater(int(d["mistake_id"]), 0)
        st = _ok_json(self, ["--json", "mistake", "stats"])
        self.assertGreaterEqual(st["total"], 1)
        self.assertGreaterEqual(st["open"], 1)
        self.assertIn("概念混淆", {x["reason"] for x in st["by_reason"]})

    def test_note(self):
        """study.py note save/list/read：笔记回写与读回。"""
        _ok_json(self, ["--json", "note", "save", "--title", "CLI验收笔记",
                        "--body", "正文内容", "--refs", "教材 p.12"])
        listing = _ok_json(self, ["--json", "note", "list"])
        self.assertIn("CLI验收笔记.md", {n["file"] for n in listing["notes"]})
        d = _ok_json(self, ["--json", "note", "read", "CLI验收笔记"])
        self.assertIn("正文内容", d["body"] or d.get("text", ""))
        self.assertGreaterEqual(d["matches"], 1)

    def test_ingest(self):
        """study.py ingest：小 md → 文档/切块/向量/FTS 全链路落临时库。"""
        r = _cli(["--json", "ingest", str(INGEST_SRC)], db=INGEST_DB)
        self.assertEqual(r["rc"], 0, r["stderr"][:500])
        d = _json(r)
        self.assertGreaterEqual(d["added"], 1)
        self.assertGreaterEqual(d["chunks"], 1)
        self.assertEqual(d["failed"], 0)
        conn = sqlite3.connect(str(INGEST_DB))
        try:
            counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                      for t in ("documents", "chunks", "embeddings", "chunks_fts")}
        finally:
            conn.close()
        for table, n in counts.items():
            self.assertGreaterEqual(n, 1, f"{table} 未写入")
        docs = _ok_json(self, ["--json", "docs"], db=INGEST_DB)
        self.assertEqual(docs["count"], 1)
        self.assertEqual(docs["docs"][0]["title"], "小样测试")

    def test_ingest_idempotent(self):
        """study.py ingest 幂等：同一文件第二次入库 skipped、不重复写切块。"""
        first = _cli(["--json", "ingest", str(INGEST_SRC)], db=INGEST_DB)
        self.assertEqual(first["rc"], 0)
        second = _cli(["--json", "ingest", str(INGEST_SRC)], db=INGEST_DB)
        self.assertEqual(second["rc"], 0, second["stderr"][:300])
        d = _json(second)
        self.assertEqual(d["added"], 0, f"重复入库不应新增：{d}")
        self.assertGreaterEqual(d["skipped"], 1, f"重复入库应跳过：{d}")
        conn = sqlite3.connect(str(INGEST_DB))
        try:
            n_docs = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
            n_chunks = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(n_docs, 1)
        self.assertEqual(n_chunks, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
