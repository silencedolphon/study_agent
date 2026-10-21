"""教材补充后端（C-0124，2026-10-21）验收：按章匹配 / 每课件产物 / 零模型 / 幂等。

全程不碰真实库、不调模型、不写真实 logs：
- 临时 sqlite（documents + chunks）构造「教材两章 + 课件」；
- `config.LOG_DIR` 指到 `_work/verify/t19_private/`，`kp_build.out_file` monkeypatch 到临时产物；
- 断言：只查所选章（SQL 层面无整本扫描）、无教材→no_textbook、needs_backfill 提醒、
  幂等/force、`supplement_for_kp` 可读、KP 产物字节 hash 不变（不改原知识点）、
  以及 `kp_build.start_background` 的可选 on_done 回调（成功才调、异常吞掉）。
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import env  # noqa: E402

sys.path.insert(0, str(env.PROJECT))
from core import config, kp_build, teach  # noqa: E402

T19 = env.VERIFY_DIR / "t19_private"
DOC = 46
TB = 50
CH_A = "药理学 第6版 刘晓东 > 第6章 作用于胆碱受体药"
CH_B = "药理学 第6版 刘晓东 > 第19章 抗高血压药"


def setUpModule():
    shutil.rmtree(T19, ignore_errors=True)
    T19.mkdir(parents=True, exist_ok=True)


def tearDownModule():
    shutil.rmtree(T19, ignore_errors=True)


# ---------------------------------------------------------------- fixtures
def _new_db(name: str, *, with_textbook: bool = True) -> Path:
    p = T19 / f"{name}.db"
    try:
        p.unlink()
    except OSError:
        pass
    conn = sqlite3.connect(str(p))
    conn.row_factory = sqlite3.Row
    conn.executescript("""
    CREATE TABLE documents (
        id INTEGER PRIMARY KEY, path TEXT, abs_path TEXT, kind TEXT, title TEXT,
        content_hash TEXT, size_bytes INTEGER, mtime REAL, n_chunks INTEGER,
        role TEXT, corpus TEXT, status TEXT, warnings TEXT, extra TEXT,
        indexed_at REAL, updated_at REAL);
    CREATE TABLE chunks (
        id INTEGER PRIMARY KEY, doc_id INTEGER, ord INTEGER, text TEXT,
        text_norm TEXT, heading_path TEXT, topic TEXT, source_ref TEXT,
        char_len INTEGER, token_est INTEGER, content_hash TEXT, created_at REAL);
    """)
    conn.execute(
        "INSERT INTO documents(id,path,abs_path,kind,title,content_hash,n_chunks,"
        "role,corpus,status) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (DOC, "raw/x.pptx", "/x.pptx", "pptx",
         "3. 具有神经节阻断作用和促进组胺释放作用。", "h", 25,
         "courseware", "main", "ok"))
    if with_textbook:
        conn.execute(
            "INSERT INTO documents(id,path,abs_path,kind,title,content_hash,n_chunks,"
            "role,corpus,status) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (TB, "raw/药理学.pdf", "/药理学.pdf", "pdf",
             "药理学 第6版 刘晓东", "t", 30, "textbook", "main", "ok"))
        rows = [
            # 章 A：胆碱受体药（阿托品/M 受体）
            (501, 1, CH_A, "p.70", "阿托品能竞争性阻断M胆碱受体，使平滑肌松弛、腺体分泌减少。"),
            (502, 2, CH_A, "p.72", "东莨菪碱可阻断M胆碱受体，用于晕动病与麻醉前给药。"),
            (503, 3, CH_A, "p.74", "阿托品大剂量时也会阻断神经节N受体，出现心率加快。"),
            # 章 B：抗高血压药（完全无关）
            (601, 4, CH_B, "p.190", "硝苯地平阻断钙通道，扩张血管，用于抗高血压。"),
            (602, 5, CH_B, "p.192", "卡托普利抑制血管紧张素转化酶，降低血压。"),
        ]
        conn.executemany(
            "INSERT INTO chunks(id,doc_id,ord,text,heading_path,source_ref,char_len,"
            "token_est,created_at) VALUES(?,?,?,?,?,?,?,?,0)",
            [(cid, TB, ordv, txt, hp, ref, len(txt), int(len(txt) * 0.75))
             for cid, ordv, hp, ref, txt in rows])
    conn.commit()
    conn.close()
    return p


def _kp_doc(kps) -> dict:
    return {"meta": {"pipeline_version": "C-0058-v4", "doc_id": DOC},
            "knowledge_points": kps}


def _kp(kp_id, name, points, summary="", pages=(1,)):
    return {"id": kp_id, "doc_id": DOC, "name": name, "node_name": name,
            "points": list(points), "summary": summary or name,
            "pages": list(pages), "material_block_ids": [], "sources": []}


def _write_kps(path: Path, kps) -> Path:
    path.write_text(json.dumps(_kp_doc(kps), ensure_ascii=False, indent=1),
                    encoding="utf-8")
    return path


class _Base(unittest.TestCase):
    def setUp(self):
        self.dir = T19 / self._testMethodName
        shutil.rmtree(self.dir, ignore_errors=True)
        (self.dir / "knowledge").mkdir(parents=True, exist_ok=True)
        self.logdir = self.dir / "logs"
        self.logdir.mkdir(parents=True, exist_ok=True)
        self.kp_path = _write_kps(self.dir / "46-末级知识点总结.json", [
            _kp("KP-A1", "阿托品的阻断作用",
                ["阿托品竞争性阻断M胆碱受体，松弛平滑肌"],
                pages=[70]),
            _kp("KP-X1", "量子力学基础", ["薛定谔方程与波函数"]),
        ])
        self._patches = [
            mock.patch.object(config, "LOG_DIR", self.logdir),
            mock.patch.object(kp_build, "out_file", lambda doc_id: self.kp_path),
        ]
        for p in self._patches:
            p.start()
        self._conns = []

    def tearDown(self):
        for c in self._conns:
            try:
                c.close()
            except Exception:  # noqa: BLE001
                pass
        for p in reversed(self._patches):
            p.stop()

    def _db(self, **kw) -> Path:
        return _new_db(self._testMethodName, **kw)

    def _conn(self, path: Path) -> sqlite3.Connection:
        conn = sqlite3.connect(str(path))
        conn.row_factory = sqlite3.Row
        self._conns.append(conn)
        return conn


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


class TestSupplementCore(_Base):
    def test_chapter_scope_no_whole_book_scan(self):
        """只查所选章：passages 全来自 A 章；SQL 里没有整本教材的 SELECT。"""
        db = self._db()
        conn = self._conn(db)
        sqls: list = []

        class Proxy:
            def __init__(self, inner):
                self.inner = inner

            def execute(self, sql, params=()):
                sqls.append(" ".join(str(sql).split()))
                return self.inner.execute(sql, params)

            def __getattr__(self, name):
                return getattr(self.inner, name)

        proxy = Proxy(conn)
        out = self.dir / "logs" / "knowledge" / "46-教材补充.json"
        rep = teach.build_supplement(DOC, conn=proxy, out_path=out,
                                     log=lambda *a, **k: None)
        self.assertEqual(rep["status"], "done")
        meta = rep["meta"]
        self.assertEqual(meta["version"], teach.SUPPLEMENT_VERSION)
        self.assertEqual(meta["textbook_doc_id"], TB)
        self.assertEqual(meta["n_kps"], 2)
        self.assertEqual(meta["n_matched"], 1)
        self.assertEqual(meta["n_no_match"], 1)
        entry = rep["kps"]["KP-A1"]
        self.assertEqual(entry["status"], "done")
        self.assertIn("第6章", entry["chapter"]["name"])
        self.assertTrue(entry["passages"])
        a_ids = {501, 502, 503}
        self.assertTrue({p["chunk_id"] for p in entry["passages"]} <= a_ids,
                        "passages 只能来自被选中的 A 章")
        for p in entry["passages"]:
            self.assertTrue(70 <= p["page_start"] <= 74)
            self.assertIn("阿托品", p["text"] + "".join(p["hit_terms"]))
        self.assertTrue(all(p["page_start"] != 190 for p in entry["passages"]))
        self.assertEqual(rep["kps"]["KP-X1"]["status"], "no_match")
        self.assertEqual(rep["kps"]["KP-X1"]["passages"], [],
                         "0 分不得回退整本扫描")
        # SQL 证据：必须是按章查询，且不能出现整本教材 SELECT
        chunk_sqls = [s for s in sqls if "FROM chunks" in s]
        self.assertTrue(chunk_sqls)
        self.assertTrue(any("heading_path=?" in s for s in chunk_sqls),
                        f"应按 heading_path 查章：{chunk_sqls}")
        self.assertFalse(any(s == ("SELECT id, ord, text, heading_path, source_ref "
                                   "FROM chunks WHERE doc_id=? ORDER BY ord")
                             for s in chunk_sqls), "不得整本教材全扫描")

    def test_no_textbook_then_needs_backfill(self):
        """无教材 → no_textbook 状态文件；教材入库后 status.needs_backfill=True。"""
        db = self._db(with_textbook=False)
        conn = self._conn(db)
        rep = teach.build_supplement(DOC, conn=conn, log=lambda *a, **k: None)
        self.assertEqual(rep["status"], "no_textbook")
        art = self.logdir / "knowledge" / "46-教材补充.json"
        self.assertTrue(art.exists(), "无教材也要写状态文件（供后续提醒）")
        data = json.loads(art.read_text(encoding="utf-8"))
        self.assertEqual(data["meta"]["status"], "no_textbook")
        self.assertEqual(data["kps"], {})
        st = teach.supplement_status(DOC, conn=conn)
        self.assertFalse(st["has_textbook"])
        self.assertFalse(st["needs_backfill"], "没教材时不该提醒补做")
        self.assertIn("没有教材", st["reason"])
        self.assertEqual(st["supplement"]["status"], "no_textbook")
        # 教材入库（同一份 KP 产物不动）→ 需要提醒补做
        conn.execute(
            "INSERT INTO documents(id,path,abs_path,kind,title,content_hash,"
            "n_chunks,role,corpus,status) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (TB, "raw/药理学.pdf", "/药理学.pdf", "pdf", "药理学 第6版 刘晓东",
             "t", 30, "textbook", "main", "ok"))
        conn.executemany(
            "INSERT INTO chunks(id,doc_id,ord,text,heading_path,source_ref,char_len,"
            "token_est,created_at) VALUES(?,?,?,?,?,?,?,?,0)",
            [(501, TB, 1, "阿托品阻断M胆碱受体。", CH_A, "p.70", 12, 9),
             (601, TB, 2, "硝苯地平扩张血管。", CH_B, "p.190", 9, 7)])
        conn.commit()
        st2 = teach.supplement_status(DOC, conn=conn)
        self.assertTrue(st2["has_textbook"])
        self.assertTrue(st2["needs_backfill"], "教材入库后应提醒补做")
        self.assertIn("补做", st2["reason"])
        rep2 = teach.build_supplement(DOC, conn=conn, log=lambda *a, **k: None)
        self.assertEqual(rep2["status"], "done")
        st3 = teach.supplement_status(DOC, conn=conn)
        self.assertFalse(st3["needs_backfill"], "补做后不再提醒")

    def test_done_for_kp_and_kp_artifact_untouched(self):
        """成功后 supplement_for_kp 可读；KP 产物字节 hash 前后一致（不改原知识点）。"""
        db = self._db()
        conn = self._conn(db)
        h0 = _sha(self.kp_path)
        rep = teach.build_supplement(DOC, conn=conn, log=lambda *a, **k: None)
        self.assertEqual(rep["status"], "done")
        self.assertEqual(_sha(self.kp_path), h0, "教材补充不得改动原知识点产物")
        r = teach.supplement_for_kp(DOC, "KP-A1")
        self.assertTrue(r["ok"] and r["available"])
        self.assertEqual(r["textbook"]["doc_id"], TB)
        self.assertEqual(r["entry"]["status"], "done")
        self.assertTrue(r["entry"]["added_terms"], "应有教材里出现、KP 没写的词")
        self.assertLessEqual(len(r["entry"]["added_terms"]), 8)
        self.assertIn("教材", r["note"])
        r2 = teach.supplement_for_kp(DOC, "KP-X1")
        self.assertFalse(r2["available"])
        r3 = teach.supplement_for_kp(DOC, "KP-NOT-EXIST")
        self.assertFalse(r3["available"])
        self.assertTrue(r3["textbook"]["title"])
        # 原子写：不残留 .tmp
        self.assertFalse(any(self.logdir.rglob("*.tmp")))

    def test_idempotent_and_force(self):
        """二次 build → skipped（0 写）；force → done；KP 产物更新后不再 skipped。"""
        db = self._db()
        conn = self._conn(db)
        rep1 = teach.build_supplement(DOC, conn=conn, log=lambda *a, **k: None)
        self.assertEqual(rep1["status"], "done")
        art = self.logdir / "knowledge" / "46-教材补充.json"
        m1 = art.stat().st_mtime_ns
        rep2 = teach.build_supplement(DOC, conn=conn, log=lambda *a, **k: None)
        self.assertEqual(rep2["status"], "skipped")
        self.assertEqual(art.stat().st_mtime_ns, m1, "skipped 不应重写产物")
        rep3 = teach.build_supplement(DOC, conn=conn, force=True,
                                      log=lambda *a, **k: None)
        self.assertEqual(rep3["status"], "done", "force 必须重写")
        # KP 产物 mtime 变了 → 不再 skipped（提醒用户补做后应能重新生成）
        time.sleep(0.02)
        self.kp_path.write_text(self.kp_path.read_text(encoding="utf-8"),
                                encoding="utf-8")
        rep4 = teach.build_supplement(DOC, conn=conn, log=lambda *a, **k: None)
        self.assertEqual(rep4["status"], "done")

    def test_no_kps_no_write(self):
        """没有 KP 产物 → no_kps，不写补充文件。"""
        db = self._db()
        conn = self._conn(db)
        self.kp_path.unlink()
        rep = teach.build_supplement(DOC, conn=conn, log=lambda *a, **k: None)
        self.assertFalse(rep["ok"])
        self.assertEqual(rep["status"], "no_kps")
        self.assertFalse((self.logdir / "knowledge" / "46-教材补充.json").exists())

    def test_match_zero_returns_none(self):
        """`_match_chapter_for_kp` 章名 0 分 → None（不整本回退）；章名命中 → 该章。"""
        db = self._db()
        conn = self._conn(db)
        tb = teach.pick_textbook(conn)
        chapters = teach._chapter_candidates(conn, tb)
        self.assertEqual(len(chapters), 2)
        none_hit = teach._match_chapter_for_kp(
            chapters, _kp("KP-Z", "量子力学", ["薛定谔方程"]), "")
        self.assertIsNone(none_hit)
        self.assertIsNone(teach._match_chapter_for_kp([], _kp("KP-Z", "阿托品", []), ""))
        hit = teach._match_chapter_for_kp(
            chapters, _kp("KP-A", "阿托品", ["阻断M胆碱受体"]), "")
        self.assertIsNotNone(hit)
        self.assertIn("第6章", hit["name"])
        self.assertGreater(hit["score"], 0)


class TestKpBuildOnDoneCallback(unittest.TestCase):
    """`kp_build.start_background` 的可选 on_done：成功才调、异常吞掉、默认不变。"""

    def setUp(self):
        self.dir = T19 / self._testMethodName
        shutil.rmtree(self.dir, ignore_errors=True)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.out = self.dir / "46-末级知识点总结.json"
        self._patches = [
            mock.patch.object(kp_build, "out_file", lambda doc_id: self.out),
            mock.patch.object(kp_build, "build", lambda *a, **k: {"ok": True}),
        ]
        for p in self._patches:
            p.start()
        kp_build._STATE["running"] = False

    def tearDown(self):
        for p in reversed(self._patches):
            p.stop()
        kp_build._STATE["running"] = False

    def _wait_idle(self, timeout: float = 5.0) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if not kp_build.is_running():
                return True
            time.sleep(0.02)
        return not kp_build.is_running()

    def test_on_done_called_after_success(self):
        self.out.write_text("{}", encoding="utf-8")
        called = []
        r = kp_build.start_background(DOC, on_done=lambda d: called.append(d))
        self.assertTrue(r["started"])
        self.assertTrue(self._wait_idle())
        self.assertEqual(called, [DOC], "整理成功后应调用 on_done(doc_id)")

    def test_on_done_exception_swallowed_and_not_called_without_artifact(self):
        # 有产物 + 回调抛异常 → 线程照常结束，整理状态不受影响
        self.out.write_text("{}", encoding="utf-8")

        def _boom(_doc_id):
            raise RuntimeError("on_done boom")

        r = kp_build.start_background(DOC, on_done=_boom)
        self.assertTrue(r["started"])
        self.assertTrue(self._wait_idle())

        # 没有产物 → 不回调
        self.out.unlink()
        called = []
        r2 = kp_build.start_background(DOC, on_done=lambda d: called.append(d))
        self.assertTrue(r2["started"])
        self.assertTrue(self._wait_idle())
        self.assertEqual(called, [], "没有整理产物时不应回调")

        # 不传 on_done → 行为不变（能正常起停，不再额外回调）
        r3 = kp_build.start_background(DOC)
        self.assertTrue(r3["started"])
        self.assertTrue(self._wait_idle())


if __name__ == "__main__":
    unittest.main(verbosity=2)
