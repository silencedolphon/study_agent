# -*- coding: utf-8 -*-
"""GPT 独立验收报告修复 B/G 的回归（零真实模型 / 零真实数据 / 临时库）。

B —— 教材 partial 不能覆盖旧完整成果：
  · 只有 status=done 才写最终 JSON；partial 只写 `.pending.json`（可恢复），旧 final 保持可读；
  · done 覆盖旧 final 前版本化备份（不删）；
  · partial=True 的段不算完成：续跑重新调用；完整段不重调；
  · estimate / read_summary / pending / calls_needed 与"是否还需调用"一致；
  · 旧产物（无 parts / 旧版 partial 写在 final）不误用、仍可续跑/重写。

G —— 章节读取一致快照：
  · `core/textbook_kp.py::_plan` 的"元数据 → 正文"两次查询在同一只读事务快照里；
  · `core/preview.py::read_chapter_ch` 的 locate → 正文读取也共享同一快照；
  · 并发重入库（WAL 提交）发生在两次查询之间时，必须读到一致快照，
    不能返回 ok=True 的"半章"。

不真实调用模型、不碰真实 index/logs/notes；产物全部写临时目录。
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import env  # noqa: E402

sys.path.insert(0, str(env.PROJECT))
from core import config, db, textbook_kp  # noqa: E402


def _point(i=1) -> dict:
    return {"title": "点%d" % i, "summary": "解释",
            "key_points": ["k"], "memory_tip": "", "pitfall": "",
            "questions": [{"question": "q", "answer": "a"}],
            "source_ids": [1]}


class _Base(unittest.TestCase):
    """临时 SQLite（真实 schema）+ 临时 LOG_DIR + 零 runtime 快照。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.logs = self.root / "logs"
        self.logs.mkdir()
        self.dbpath = self.root / "t.db"
        self.conn = db.connect(self.dbpath, immediate=True)
        db.init_db(self.conn)
        self.conn.execute(
            "INSERT INTO documents(id, path, abs_path, kind, title, content_hash, "
            "role, n_chunks, n_chars, status) VALUES(50,'t.pdf','t.pdf','pdf','药理学',"
            "'h','textbook',3,240,'ok')")
        for i in range(1, 4):
            self._chunk(i, ("甲乙丙"[i - 1]) * 200, "p.%d" % i)
        self.conn.commit()
        self._patches = [
            mock.patch.dict(os.environ, {"PIPELINE_RUNTIME": "0",
                                          "TEXTBOOK_KP_FAST_PLAN": "1"}),
            mock.patch.object(config, "LOG_DIR", self.logs),
            mock.patch.object(config, "DB_PATH", self.dbpath),
        ]
        for p in self._patches:
            p.start()
            self.addCleanup(p.stop)

    def tearDown(self):
        try:
            self.conn.close()
        except Exception:  # noqa: BLE001
            pass

    def _chunk(self, ord_, text, ref, cid=None):
        self.conn.execute(
            "INSERT INTO chunks(doc_id, ord, text, text_norm, heading_path, source_ref, "
            "char_len, token_est, content_hash, created_at) "
            "VALUES(50,?,?,?,?,?,?,?,?,0)",
            (ord_, text, text, "药理学 > 第1章 总论", ref, len(text), len(text) // 2,
             db.sha256_text(text)))

    def _plan(self, chars=80):
        with mock.patch.object(textbook_kp, "SEGMENT_CHARS", chars):
            return textbook_kp._plan(50, 1, db_path=self.dbpath)

    def _estimate(self, **kw):
        """estimate 内部会重新 _plan：沿用同一 SEGMENT_CHARS，才能与 build 的签名一致。"""
        with mock.patch.object(textbook_kp, "SEGMENT_CHARS", 80):
            return textbook_kp.estimate(50, 1, db_path=self.dbpath,
                                        log_dir=self.logs, **kw)

    def _summary(self):
        with mock.patch.object(textbook_kp, "SEGMENT_CHARS", 80):
            return textbook_kp.read_summary(50, 1, log_dir=self.logs,
                                            db_path=self.dbpath)

    def _paths(self):
        return textbook_kp._paths(50, 1, self.logs)

    @staticmethod
    def _write_final(path: Path, plan: dict, *, sig=None, status="done",
                     points=None, parts=None, extra=None) -> str:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {"version": textbook_kp.VERSION, "status": status,
                "signature": sig or plan["signature"], "doc_id": 50, "chapter": 1,
                "title": plan["title"], "chapter_name": plan["chapter_name"],
                "config_fingerprint": plan["config_fingerprint"],
                "points": points if points is not None else [_point(1)],
                "reviews": {}}
        if parts is not None:
            data["parts"] = parts
        if extra:
            data.update(extra)
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return path.read_text(encoding="utf-8")

    def _fake_call(self, behaviour):
        """behaviour: fn(segment, index) -> part dict。"""
        calls = []

        def call(seg, plan, run_context=None):
            calls.append(seg["hash"])
            return behaviour(seg, len(calls) - 1)
        return calls, call


def _good(partial=False, n_invalid=0, idx=0):
    return {"points": [_point(idx + 1)], "usage": {}, "partial": bool(partial),
            "invalid_points": int(n_invalid), "finish_reason": "stop"}


# ================================================================ B：旧成果保护
class TestPartialKeepsOldFinal(_Base):
    def test_new_partial_does_not_overwrite_old_done(self):
        """已有 done（旧签名）+ 新 partial：旧 final 逐字不变，partial 只进 pending。"""
        final, pending, _ = self._paths()
        old_plan = self._plan(chars=100)          # 旧签名
        old_bytes = self._write_final(
            final, old_plan, sig="old-signature-xxx",
            points=[_point(9)], extra={"chapter_name": "旧章"})
        plan = self._plan(chars=80)               # 新签名（不同）
        self.assertNotEqual(plan["signature"], "old-signature-xxx")
        calls, call = self._fake_call(lambda seg, i: _good(partial=True, n_invalid=1, idx=i))
        with mock.patch.object(textbook_kp, "_call", side_effect=call):
            res = textbook_kp._build(plan, force=False, db_path=self.dbpath,
                                     log_dir=self.logs, max_calls=10)
        self.assertEqual(res["status"], "partial")
        self.assertFalse(res["final_written"])
        self.assertTrue(res["pending_path"])
        self.assertEqual(len(calls), len(plan["segments"]))
        # 旧 done 逐字可读（内容/签名/点都没动）
        self.assertEqual(final.read_text(encoding="utf-8"), old_bytes)
        art = json.loads(final.read_text(encoding="utf-8"))
        self.assertEqual(art["status"], "done")
        self.assertEqual(art["points"], [_point(9)])
        # partial 在 pending，可恢复
        pend = json.loads(pending.read_text(encoding="utf-8"))
        self.assertEqual(pend["status"], "partial")
        self.assertEqual(pend["signature"], plan["signature"])
        self.assertEqual(len(pend["parts"]), len(plan["segments"]))
        # 估算/状态一致：所有段都是 partial → 还需全部重调
        est = self._estimate()
        self.assertEqual(est["calls"], len(plan["segments"]))
        self.assertEqual(est["partial_segments"], len(plan["segments"]))
        self.assertTrue(est["resumable"])
        summary = self._summary()
        self.assertTrue(summary["exists"])
        self.assertEqual(summary["status"], "done", "旧 done 仍可读")
        self.assertTrue(summary["pending"], "有新 partial 可续跑")
        self.assertEqual(summary["calls_needed"], len(plan["segments"]))

    def test_second_build_completes_and_backs_up_old_done(self):
        """partial 续跑：完整段 skip、partial 段重调；done 后旧 final 有备份。"""
        final, pending, _ = self._paths()
        old_plan = self._plan(chars=100)
        old_bytes = self._write_final(final, old_plan, sig="old-done-sig")
        plan = self._plan(chars=80)
        partial_hash = plan["segments"][0]["hash"]

        def first(seg, i):
            return _good(partial=(seg["hash"] == partial_hash), n_invalid=1, idx=i)

        calls1, call1 = self._fake_call(first)
        with mock.patch.object(textbook_kp, "_call", side_effect=call1):
            res1 = textbook_kp._build(plan, force=False, db_path=self.dbpath,
                                      log_dir=self.logs, max_calls=10)
        self.assertEqual(res1["status"], "partial")
        self.assertEqual(len(calls1), len(plan["segments"]))
        self.assertEqual(final.read_text(encoding="utf-8"), old_bytes)
        # 估算：只有 partial 段需要重调
        est = self._estimate()
        self.assertEqual(est["calls"], 1)
        self.assertEqual(est["completed_segments"], len(plan["segments"]) - 1)
        self.assertEqual(est["partial_segments"], 1)
        summary = self._summary()
        self.assertTrue(summary["pending"])
        self.assertEqual(summary["calls_needed"], 1)
        self.assertEqual(summary["partial_segments"], 1)

        calls2, call2 = self._fake_call(lambda seg, i: _good(idx=i))
        with mock.patch.object(textbook_kp, "_call", side_effect=call2):
            res2 = textbook_kp._build(plan, force=False, db_path=self.dbpath,
                                      log_dir=self.logs, max_calls=10)
        self.assertEqual(res2["status"], "done")
        self.assertEqual(calls2, [partial_hash], "只重调 partial 段")
        self.assertTrue(res2["final_written"])
        # 旧 done 备份（版本化、不删）
        self.assertTrue(res2["backup_path"])
        backup = Path(res2["backup_path"])
        self.assertTrue(backup.exists())
        self.assertEqual(backup.read_text(encoding="utf-8"), old_bytes)
        art = json.loads(final.read_text(encoding="utf-8"))
        self.assertEqual(art["status"], "done")
        self.assertFalse(pending.exists(), "done 后 pending 清掉")

    def test_legacy_partial_without_parts_not_misused(self):
        """旧 partial final 没有 parts（历史产物）：不当完成，按需重跑并备份。"""
        final, pending, _ = self._paths()
        plan = self._plan(chars=80)
        old_bytes = self._write_final(final, plan, status="partial",
                                      points=[_point(1)], parts=None)
        est = self._estimate()
        self.assertEqual(est["calls"], len(plan["segments"]),
                         "旧 partial 没有分段缓存，不能当成已完成")
        self.assertFalse(est["resumable"])
        calls, call = self._fake_call(lambda seg, i: _good(idx=i))
        with mock.patch.object(textbook_kp, "_call", side_effect=call):
            res = textbook_kp._build(plan, force=False, db_path=self.dbpath,
                                     log_dir=self.logs, max_calls=10)
        self.assertEqual(len(calls), len(plan["segments"]))
        self.assertEqual(res["status"], "done")
        self.assertTrue(res["backup_path"])
        self.assertEqual(Path(res["backup_path"]).read_text(encoding="utf-8"),
                         old_bytes)

    def test_done_same_signature_reused_no_write(self):
        """同签名 done：直接复用，不调用、不写、不备份。"""
        final, pending, _ = self._paths()
        plan = self._plan(chars=80)
        before = self._write_final(final, plan, status="done", points=[_point(1)])
        mtime = final.stat().st_mtime_ns
        calls, call = self._fake_call(lambda seg, i: _good(idx=i))
        with mock.patch.object(textbook_kp, "_call", side_effect=call):
            res = textbook_kp._build(plan, force=False, db_path=self.dbpath,
                                     log_dir=self.logs, max_calls=10)
        self.assertTrue(res.get("reused"))
        self.assertEqual(calls, [])
        self.assertEqual(final.read_text(encoding="utf-8"), before)
        self.assertEqual(final.stat().st_mtime_ns, mtime)
        self.assertFalse(any(p.name.startswith(final.name + ".bak-")
                             for p in final.parent.glob(final.name + ".bak-*")))

    def test_estimate_and_max_calls_consistent_for_partial(self):
        """估算说还需 N 次时，build 的 max_calls=N-1 必须拒绝且只打了 N-1 次。"""
        final, pending, _ = self._paths()
        plan = self._plan(chars=80)
        n = len(plan["segments"])
        calls, call = self._fake_call(lambda seg, i: _good(partial=True, idx=i))
        with mock.patch.object(textbook_kp, "_call", side_effect=call):
            with self.assertRaises(ValueError):
                textbook_kp._build(plan, force=True, db_path=self.dbpath,
                                   log_dir=self.logs, max_calls=n - 1)
        self.assertEqual(len(calls), n - 1)
        est = self._estimate()
        self.assertEqual(est["calls"], n, "估算与实际需要调用次数一致")

    def test_estimate_force_counts_all_segments(self):
        """force=True（重新整理）忽略已完成段：估算必须按全部段算，不能少报。"""
        plan = self._plan(chars=80)
        n = len(plan["segments"])
        calls, call = self._fake_call(lambda seg, i: _good(partial=(i == 0), idx=i))
        with mock.patch.object(textbook_kp, "_call", side_effect=call):
            textbook_kp._build(plan, force=False, db_path=self.dbpath,
                               log_dir=self.logs, max_calls=10)
        est_resume = self._estimate(force=False)
        self.assertEqual(est_resume["calls"], 1, "续跑只需重调 partial 段")
        est_force = self._estimate(force=True)
        self.assertEqual(est_force["calls"], n, "force 必须重跑全部段")
        # 与 build(force=True) 的实际调用一致
        calls2, call2 = self._fake_call(lambda seg, i: _good(idx=i))
        with mock.patch.object(textbook_kp, "_call", side_effect=call2):
            textbook_kp._build(plan, force=True, db_path=self.dbpath,
                               log_dir=self.logs, max_calls=n)
        self.assertEqual(len(calls2), n)


# ================================================================ G：一致快照
class _ProxyCursor:
    def __init__(self, cur, fetchall_hook=None):
        self._cur = cur
        self._hook = fetchall_hook

    def fetchall(self):
        rows = self._cur.fetchall()
        if self._hook is not None:
            hook, self._hook = self._hook, None
            hook()
        return rows

    def __getattr__(self, name):
        return getattr(self._cur, name)

    def __iter__(self):
        return iter(self._cur)


class _ProxyConn:
    """sqlite3.Connection 代理：在指定 SQL 的 fetchall 之后插入一次"并发写"。"""

    def __init__(self, real, hook_sql, hook):
        object.__setattr__(self, "_real", real)
        object.__setattr__(self, "_hook_sql", hook_sql)
        object.__setattr__(self, "_hook", hook)

    def __getattr__(self, name):
        return getattr(self._real, name)

    def __setattr__(self, name, value):
        if name.startswith("_"):
            object.__setattr__(self, name, value)
        else:
            setattr(self._real, name, value)

    def execute(self, sql, params=()):
        cur = self._real.execute(sql, params)
        hook_sql = self._hook_sql
        if self._hook is not None and hook_sql in sql:
            return _ProxyCursor(cur, self._hook)
        return cur

    def close(self):
        return self._real.close()


class TestConsistentSnapshot(_Base):
    """WAL 库：两次查询之间发生重入库提交，读取必须仍是同一快照。"""

    def setUp(self):
        super().setUp()
        # 明确 WAL + 记录真实 connect（代理里模拟并发写时使用）
        mode = self.conn.execute("PRAGMA journal_mode=WAL").fetchone()[0]
        self.assertEqual(str(mode).lower(), "wal")
        self.conn.commit()
        self._real_connect = sqlite3.connect

    def _simulate_reimport(self):
        """模拟重入库：删旧块1/块2、写一条新块（p.2），bump revision 并 WAL 提交。

        旧快照 3 块（甲/乙/丙，p.1/p.2/p.3）；新快照 2 块（新 p.2 + 旧丙 p.3）。
        没有一致快照时：章计划会只剩 1 块（半章）、章材料会混入新内容。
        """
        real_connect = self._real_connect
        c = real_connect(str(self.dbpath), timeout=30)
        try:
            c.execute("DELETE FROM chunks WHERE doc_id=50 AND ord IN (1, 2)")
            c.execute(
                "INSERT INTO chunks(doc_id, ord, text, text_norm, heading_path, "
                "source_ref, char_len, token_est, content_hash, created_at) "
                "VALUES(50,1,?,?,?,?,?,?,?,0)",
                ("重入库后的乙" * 30, "重入库后的乙" * 30, "药理学 > 第1章 总论",
                 "p.2", 180, 90, db.sha256_text("重入库后的乙" * 30)))
            db.bump_data_revision(c)
            c.commit()                     # WAL：主库 mtime 可能不变
        finally:
            c.close()

    def test_textbook_plan_consistent_snapshot(self):
        """_plan：元数据 → 正文之间发生重入库，仍必须读到完整旧章（3 块）。"""
        orig = sqlite3.connect
        hook_sql = "SELECT id, heading_path FROM chunks"

        def connect(*a, **kw):
            return _ProxyConn(orig(*a, **kw), hook_sql, self._simulate_reimport)

        before = textbook_kp._plan(50, 1, db_path=self.dbpath)
        self.assertEqual(before["n_blocks"], 3)
        with mock.patch("core.textbook_kp.sqlite3.connect", side_effect=connect):
            plan = textbook_kp._plan(50, 1, db_path=self.dbpath)
        # 没有快照时：元数据拿到旧 id 列表，正文按旧 id 查新库只剩 id1 → 半章
        self.assertEqual(plan["n_blocks"], 3,
                         "章计划必须读到同一快照（3 块），不能是半章")
        # 段材料里应包含旧块2 的内容（快照），不能是新库内容
        material = "\n".join(s["material"] for s in plan["segments"])
        self.assertIn("乙" * 80, material)
        self.assertNotIn("重入库后的乙", material)

    def test_preview_read_chapter_ch_consistent_snapshot(self):
        """read_chapter_ch：locate（章节目录）→ 正文读取共享同一快照，不是半章。"""
        from core import preview

        orig = sqlite3.connect
        hook_sql = "SELECT id, ord, source_ref, heading_path FROM chunks"

        def connect(*a, **kw):
            return _ProxyConn(orig(*a, **kw), hook_sql, self._simulate_reimport)

        with mock.patch("core.preview.sqlite3.connect", side_effect=connect):
            ch, info, material, inv, stats = preview.read_chapter_ch(
                50, 1, db_path=self.dbpath)
        self.assertTrue(info.get("ok"), info)
        self.assertEqual(ch["n_blocks"], 3,
                         "快照里必须还有旧块2；否则就是 ok=True 的半章")
        self.assertIn("乙" * 200, material)
        self.assertNotIn("重入库后的乙", material)
        # 缓存不应把半章写进缓存：再读一次（快照已无并发写）读到新库的 2 块
        ch2, info2, material2, _inv2, _ = preview.read_chapter_ch(
            50, 1, db_path=self.dbpath)
        self.assertEqual(ch2["n_blocks"], 2, "重入库后新快照只有 2 块（不是半章）")
        self.assertIn("重入库后的乙", material2)

    def test_read_text_pages_by_ids_snapshot_conn(self):
        """read_text_pages_by_ids 接受内部 `_conn`：复用快照，不另开连接。

        WAL 的读快照是在**第一次读**时建立的（单独 BEGIN 不建快照），
        所以这里先做一次读，再模拟重入库，然后复用该连接读正文。
        """
        from core import kp_text

        conn = sqlite3.connect("file:" + self.dbpath.as_posix() + "?mode=ro",
                               uri=True)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("BEGIN")
            conn.execute("SELECT COUNT(*) FROM chunks").fetchone()   # 建立快照
            self._simulate_reimport()
            full = kp_text.read_text_pages_by_ids(50, [1, 2, 3], self.dbpath,
                                                  _conn=conn)
        finally:
            conn.close()
        self.assertEqual(full["n_blocks"], 3, "快照连接应读到旧 3 块")
        self.assertIn("乙" * 200,
                      "".join(b["text"] for bs in full["blocks"].values() for b in bs))
        # 新连接（新快照）读到重入库后的 2 块 —— 证明库确实变了、上面读的是旧快照
        fresh = kp_text.read_text_pages_by_ids(50, [1, 2, 3], self.dbpath)
        self.assertEqual(fresh["n_blocks"], 1,
                         "旧 id 集合在新库里只剩块3（半章）——重入库确实发生")


if __name__ == "__main__":
    unittest.main(verbosity=2)
