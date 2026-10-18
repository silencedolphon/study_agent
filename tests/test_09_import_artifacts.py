"""M0-3 整理产物 → 知识点表导入（tools/import_kp_artifacts.py + core/kp_import.py）验收。

全程只碰 `_work/verify/t09_private/` 下的**副本库**与自造产物 fixture，不读不写真实的
`logs/knowledge` 与 `index/study.db`（真实产物只由工具在真实副本上人工验收，
见 `_work/m0/import/report.md` 与 `refactor-report.md`）。

覆盖：
  · dry-run（默认）只预览、库与 meta 清单零写入；
  · --apply 建知识点 + 出处，且 `/api/kps` / `/api/stats` 立刻可见；
  · 二次 --apply 不重复建卡（幂等），kp_sources 不重复；
  · meta 导入清单被清掉后仍按「名称 + 摘要一致」认领，不重复建卡；
  · 同一产物内/跨课件**同名**节点自动加父级前缀区分；
  · 坏产物文件（坏 JSON）只记失败并继续，退出码仍 0；
  · --doc-id 只导指定课件；
  · `core.kp_import.run()` 直调（dashboard 接线路径）：纯函数、默认 dry-run 零写入、
    传 conn 时不关闭调用方连接、前置错误抛 `KpImportError`。
"""
from __future__ import annotations

import json
import re
import shutil
import sqlite3
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import env  # noqa: E402

# 直调用例要 import core（模拟 dashboard 的接法）；run_all 从项目根起跑，这里补路径
sys.path.insert(0, str(env.PROJECT))
from core import kp_import  # noqa: E402

PROJECT = env.PROJECT
TOOL = PROJECT / "tools" / "import_kp_artifacts.py"
# 用**私有目录**，不碰 tests/env.py 的共享 DATA_DIR：
# 多个智能体并发跑 run_all.py 时，共享 `_work/verify/data/` 会被各自的
# `env.prepare()` 删除/占用（本模块 setUpModule 实测 4 次
# `PermissionError: [WinError 32] main.db`），所以本模块改成
# 「只读复制 env.FIXTURE_DIR 的 fixture 到私有目录」，不参与那场竞争。
T09_DIR = env.VERIFY_DIR / "t09_private"
ART_DIR = T09_DIR / "artifacts"
V1_ART_DIR = T09_DIR / "artifacts_v1"
BASE_DB = T09_DIR / "base.db"
RUNTIME_DIRS = {name: T09_DIR / name for name in ("raw", "notes", "index", "logs")}
SETTINGS_FILE = T09_DIR / "config.local.json"
MANIFEST_KEY = "kp_import.artifacts.v1"
SUFFIX = "-末级知识点总结.json"
_PAGE_RE = re.compile(r"p\s*\.?\s*(\d+)(?:\s*[-–~—至]\s*(\d+))?", re.I)

DOC_A = 2   # 第七章 药物排泄（fixture 里较小，source_ref 形如 p.1 / p.1-2）
DOC_B = 3   # 第六章 药物代谢


# ---------------------------------------------------------------- fixture
def _chunks(db_path: Path, doc_id: int) -> list[dict]:
    conn = sqlite3.connect("file:%s?mode=ro" % db_path.as_posix(), uri=True)
    try:
        rows = conn.execute(
            "SELECT id, ord, source_ref FROM chunks WHERE doc_id=? ORDER BY ord",
            (doc_id,)).fetchall()
    finally:
        conn.close()
    out = []
    for cid, _ord, ref in rows:
        m = _PAGE_RE.search(ref or "")
        pages = [int(m.group(1)), int(m.group(2) or m.group(1))] if m else []
        out.append({"id": int(cid), "ref": ref or "", "pages": pages})
    return out


def _kp(uid, name, path, pages, chunk_ids=None, summary="", points=None):
    rec = {
        "id": uid,
        "doc_id": 0,  # 组装时按所在产物改
        "name": name,
        "node_name": name,
        "node_path": list(path),
        "pages": list(pages),
        "summary": summary or f"自动化验收摘要：{name}。",
        "points": points or ["要点一", "要点二"],
        "insufficient": False,
    }
    if chunk_ids:
        rec["sources"] = [{"page": "p.1", "chunk_ids": list(chunk_ids), "figure_desc": False}]
        rec["material_block_ids"] = list(chunk_ids)
    return rec


def _write_artifact(path: Path, doc_id: int, chapter: str, kps: list[dict]) -> None:
    doc = {
        "meta": {
            "doc_id": doc_id, "chapter": chapter, "deck": chapter,
            "pipeline_version": "C-0058-v4", "source_card": "TEST-09",
            "model": "fixture", "kp_total": len(kps),
        },
        "structure": {"root": {"id": "N01", "name": chapter, "children": []},
                      "nodes": [], "n_pages": 5},
        "knowledge_points": [],
    }
    for kp in kps:
        rec = dict(kp)
        rec["doc_id"] = doc_id
        doc["knowledge_points"].append(rec)
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")


def setUpModule():
    """在**本模块私有目录**里造产物 fixture（不调用 env.prepare，避免共享 DATA_DIR 竞争）。

    fixture 库从 `env.FIXTURE_DIR` 只读复制（run_all.py 在开跑前已拍好快照）；
    每个用例再从私有 `base.db` 复制一份自己的库。
    """
    if T09_DIR.exists():
        shutil.rmtree(T09_DIR, ignore_errors=True)
    T09_DIR.mkdir(parents=True, exist_ok=True)
    for d in RUNTIME_DIRS.values():
        d.mkdir(parents=True, exist_ok=True)
    fixture = env.FIXTURE_DIR / "main.db"
    if not fixture.exists():
        env.ensure_fixtures(refresh=False)
    env.copy_sqlite(fixture, BASE_DB)
    for d in (ART_DIR, V1_ART_DIR):
        d.mkdir(parents=True, exist_ok=True)

    ca = _chunks(BASE_DB, DOC_A)
    cb = _chunks(BASE_DB, DOC_B)
    assert len(ca) >= 6 and len(cb) >= 6, "fixture 库切块不够，无法造产物"
    ca = [c for c in ca if c["pages"]][:6]
    cb = [c for c in cb if c["pages"]][:3]
    assert ca and cb

    # 文档 A：3 条（显式出处 / 页兜底 / 无出处各一条）
    _write_artifact(
        ART_DIR / f"{DOC_A}{SUFFIX}", DOC_A, "第七章 药物排泄", [
            _kp("KP-T1-1", "验收导入-甲", ["第七章 药物排泄", "肾排泄", "验收导入-甲"],
                ca[0]["pages"], chunk_ids=[ca[0]["id"]], summary="验收导入甲：肾排泄概述。"),
            _kp("KP-T1-2", "验收导入-乙", ["第七章 药物排泄", "胆汁排泄", "验收导入-乙"],
                ca[1]["pages"], summary="验收导入乙：只给覆盖页，靠页兜底找块。"),
            _kp("KP-T1-3", "验收导入-丙", ["第七章 药物排泄", "其他途径", "验收导入-丙"],
                [], summary="验收导入丙：没有页也没有显式块。"),
        ])
    # 文档 B：与 A **同名同 id** 的节点（真实产物里 KP-N03-1 跨 5 份课件重复），
    # 用来证明去重键必须带 doc_id，且同名靠父级前缀区分。
    _write_artifact(
        ART_DIR / f"{DOC_B}{SUFFIX}", DOC_B, "第六章 药物代谢", [
            _kp("KP-T1-1", "验收导入-甲", ["第六章 药物代谢", "I相反应", "验收导入-甲"],
                cb[0]["pages"], chunk_ids=[cb[0]["id"]], summary="验收导入甲：药物代谢概述。"),
        ])
    # 坏产物：解析失败必须只记 failure 并继续
    (ART_DIR / f"999{SUFFIX}").write_text("{ 这不是合法 JSON", encoding="utf-8")


# ---------------------------------------------------------------- 工具函数
def _env_for_tool(db_path: Path) -> dict:
    """工具子进程环境：KB_* 全指私有目录，KB_TESTING=1 开启真实库护栏。"""
    run_env = env.base_env(db_path=BASE_DB)
    run_env.update({
        "KB_RAW": str(RUNTIME_DIRS["raw"]),
        "KB_NOTES": str(RUNTIME_DIRS["notes"]),
        "KB_INDEX": str(RUNTIME_DIRS["index"]),
        "KB_LOG": str(RUNTIME_DIRS["logs"]),
        "KB_SETTINGS_FILE": str(SETTINGS_FILE),
        "KB_TESTING": "1",   # 连真实库会被 core.db.connect 护栏拒绝
    })
    return run_env


def _run_tool(db_path: Path, *extra: str, artifacts_dir: Path | None = None) -> tuple[int, dict]:
    ad = Path(artifacts_dir) if artifacts_dir else ART_DIR
    cmd = [str(env.PYTHON), "-X", "utf8", str(TOOL),
           "--db", str(db_path), "--artifacts-dir", str(ad), "--json", *extra]
    # 注意：KB_DB 保持私有 base.db，**不要**指成 db_path——
    # core.db.connect 的 KB_TESTING 护栏基准是「进程启动时的 config.DB_PATH」，
    # 若把 KB_DB 指成待写副本，护栏会把副本误判成真实库而拒连（已踩）。
    cp = subprocess.run(cmd, cwd=str(PROJECT), env=_env_for_tool(db_path),
                        capture_output=True, timeout=180)
    out = cp.stdout.decode("utf-8", "replace")
    try:
        stats = json.loads(out[out.find("{"):]) if "{" in out else {}
    except ValueError:
        stats = {}
    assert cp.returncode == 0, (
        f"工具退出码 {cp.returncode}\nstdout={out}\nstderr={cp.stderr.decode('utf-8', 'replace')}")
    return cp.returncode, stats


def _db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect("file:%s?mode=ro" % path.as_posix(), uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _count(path: Path, table: str) -> int:
    conn = _db(path)
    try:
        return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    finally:
        conn.close()


def _manifest(path: Path):
    conn = _db(path)
    try:
        row = conn.execute("SELECT value FROM meta WHERE key=?", (MANIFEST_KEY,)).fetchone()
    finally:
        conn.close()
    return json.loads(row[0])["items"] if row else None


# 并发跑套件时 8897–8899 会被别的模块/别的智能体抢占（真实踩到过：进程刚起就被
# 别人的清理脚本杀掉，GET 直接 connection refused）。这里优先用不常用的 8913+，
# 并对「起服务 → 取数」整体重试一次。
_PORT_CANDIDATES = (8913, 8914, 8915, 8897, 8898, 8899)


def _with_instance(db_path: Path, fn):
    """在隔离看板实例上执行 fn(srv)，端口冲突/实例中途被杀时换端口重试一次。"""
    extra_env = {
        "KB_RAW": str(RUNTIME_DIRS["raw"]),
        "KB_NOTES": str(RUNTIME_DIRS["notes"]),
        "KB_INDEX": str(RUNTIME_DIRS["index"]),
        "KB_LOG": str(RUNTIME_DIRS["logs"]),
        "KB_SETTINGS_FILE": str(SETTINGS_FILE),
    }
    last = None
    for attempt in range(2):
        for port in _PORT_CANDIDATES:
            if not env.port_free(port):
                continue
            srv = env.Server(db_path=db_path, port=port,
                             tag=f"t09-import-{attempt}-{port}",
                             extra_env=extra_env)
            try:
                srv.start()
                if not srv.alive:
                    raise RuntimeError("看板进程启动后即退出（端口可能被并发测试抢占）")
                return fn(srv)
            except (RuntimeError, OSError) as e:
                last = e
                try:
                    srv.stop()
                except Exception:  # noqa: BLE001
                    pass
        import time as _time
        _time.sleep(0.5)
    raise last or RuntimeError("找不到可用端口起隔离看板")


class _Base(unittest.TestCase):
    def setUp(self):
        self.db_path = T09_DIR / f"t09-{self._testMethodName}.db"
        shutil.copy2(BASE_DB, self.db_path)   # 每个用例一份干净副本，互不影响

    def tearDown(self):
        for p in (self.db_path, Path(str(self.db_path) + "-wal"),
                  Path(str(self.db_path) + "-shm")):
            try:
                p.unlink()
            except OSError:
                pass


# ---------------------------------------------------------------- 用例
class TestImportArtifacts(_Base):
    def test_dry_run_does_not_write(self):
        """默认 dry-run：只预览统计，库表与 meta 清单零写入。"""
        _, s = _run_tool(self.db_path)
        self.assertEqual(s["mode"], "dry_run")
        self.assertEqual(s["scanned_files"], 3, "应扫到 2 份好产物 + 1 份坏产物")
        self.assertEqual(s["parsed_files"], 2)
        self.assertEqual(len(s["failed_files"]), 1, "坏 JSON 只记失败，不中断")
        self.assertIn("999" + SUFFIX, s["failed_files"][0]["file"])
        self.assertEqual(s["kps_found"], 4)                  # A 3 条 + B 1 条
        self.assertEqual(s["docs_matched"], 2)
        self.assertEqual(s["kps_would_create"], 4)
        self.assertGreater(s["kp_sources_would_add"], 0)
        self.assertEqual(_count(self.db_path, "knowledge_points"), 0, "dry-run 不得写知识点")
        self.assertEqual(_count(self.db_path, "kp_sources"), 0, "dry-run 不得写出处")
        self.assertIsNone(_manifest(self.db_path), "dry-run 不应写导入清单")

    def test_apply_writes_and_api_visible(self):
        """--apply 真写库；同名节点靠父级前缀区分；/api/kps、/api/stats 立即可见。"""
        _, s = _run_tool(self.db_path, "--apply")
        self.assertEqual(s["mode"], "apply")
        self.assertEqual(s["kps_created"], 4)
        self.assertEqual(s["kps_existing"], 0)
        self.assertGreater(s["kp_sources_added"], 0)
        self.assertEqual(_count(self.db_path, "knowledge_points"), 4)
        self.assertEqual(_count(self.db_path, "kp_sources"), s["kp_sources_added"])
        # 同名两条（不同课件）都被前缀区分，且库里没有重名
        conn = _db(self.db_path)
        try:
            names = [r["name"] for r in conn.execute(
                "SELECT name FROM knowledge_points ORDER BY id").fetchall()]
            dup = conn.execute(
                "SELECT COUNT(*) FROM (SELECT name FROM knowledge_points "
                "GROUP BY name HAVING COUNT(*)>1)").fetchone()[0]
            had = conn.execute(
                "SELECT COUNT(*) FROM knowledge_points WHERE had_sources=1").fetchone()[0]
            src_docs = conn.execute(
                "SELECT COUNT(DISTINCT c.doc_id) FROM kp_sources s "
                "JOIN chunks c ON c.id=s.chunk_id").fetchone()[0]
            notes = conn.execute(
                "SELECT note FROM kp_sources WHERE note<>'' LIMIT 1").fetchone()
        finally:
            conn.close()
        self.assertEqual(dup, 0)
        same = [n for n in names if n.endswith("验收导入-甲")]
        self.assertEqual(len(same), 2, f"两条同名知识点应都能导入: {names}")
        self.assertNotEqual(same[0], same[1])
        self.assertTrue(any(n.endswith("验收导入-乙") for n in names))
        self.assertTrue(any(n.endswith("验收导入-丙") for n in names))
        self.assertEqual(had, 3, "3 条有出处的知识点 had_sources 应为 1")
        self.assertEqual(src_docs, 2, "出处应覆盖两个课件")
        self.assertTrue(notes and notes[0].startswith("import:v1:"), "kp_sources.note 应有导入溯源")
        self.assertEqual(len(_manifest(self.db_path) or {}), 4, "应写 4 条导入清单")

        # 起隔离看板（只读 API）确认「导入即见」
        def _probe(srv):
            status, obj = srv.get_json("/api/kps")
            self.assertEqual(status, 200)
            api_names = [k["name"] for k in obj["knowledge_points"]]
            self.assertEqual(len(api_names), 4)
            for n in api_names:
                self.assertIn(n, names)
            status, st = srv.get_json("/api/stats")
            self.assertEqual(status, 200)
            self.assertEqual(st["knowledge"]["total"], 4)
            return api_names

        _with_instance(self.db_path, _probe)

    def test_second_apply_idempotent(self):
        """二次 --apply：新增 0，出处不重复，kp_id 不变（清单命中）。"""
        _run_tool(self.db_path, "--apply")
        kps1 = _count(self.db_path, "knowledge_points")
        src1 = _count(self.db_path, "kp_sources")
        manifest1 = _manifest(self.db_path)

        _, s2 = _run_tool(self.db_path, "--apply")
        self.assertEqual(s2["kps_created"], 0, "二次导入不得新建知识点")
        self.assertEqual(s2["kps_existing"], 4)
        self.assertEqual(s2["kp_sources_added"], 0, "二次导入不得重复插出处")
        self.assertEqual(_count(self.db_path, "knowledge_points"), kps1)
        self.assertEqual(_count(self.db_path, "kp_sources"), src1)
        manifest2 = _manifest(self.db_path)
        self.assertEqual({k: v["kp_id"] for k, v in manifest1.items()},
                         {k: v["kp_id"] for k, v in manifest2.items()},
                         "重复导入后 kp_id 映射必须稳定")

    def test_manifest_lost_still_idempotent(self):
        """清单丢失（meta 行被清）后，按「名称 + 摘要一致」认领，仍不重复建卡。"""
        _run_tool(self.db_path, "--apply")
        n1 = _count(self.db_path, "knowledge_points")
        conn = sqlite3.connect(str(self.db_path))
        try:
            conn.execute("DELETE FROM meta WHERE key=?", (MANIFEST_KEY,))
            conn.commit()
        finally:
            conn.close()
        _, s = _run_tool(self.db_path, "--apply")
        self.assertEqual(s["kps_created"], 0, "清单丢失也不能重复建卡")
        self.assertEqual(s["kps_existing"], 4)
        self.assertEqual(_count(self.db_path, "knowledge_points"), n1)
        self.assertEqual(len(_manifest(self.db_path) or {}), 4, "认领后应重建清单")

    def test_doc_id_filter(self):
        """--doc-id 只导指定课件（统计也只算该课件的条目）。"""
        _, s = _run_tool(self.db_path, "--doc-id", str(DOC_A))
        self.assertEqual(s["kps_found"], 3)          # 只有文档 A 的 3 条
        self.assertEqual(s["kps_would_create"], 3)
        self.assertEqual(_count(self.db_path, "knowledge_points"), 0)


    def test_v1_artifact_layout(self):
        """旧 v1 产物（`path` 而非 `node_path`、无 material_block_ids）也能导入。"""
        cb = [c for c in _chunks(BASE_DB, DOC_B) if c["pages"]]
        self.assertTrue(cb)
        doc = {
            "meta": {"doc_id": DOC_B, "chapter": "第六章 药物代谢",
                     "pipeline_version": "C-0032"},
            "knowledge_points": [
                {"id": "LN-01", "name": "验收导入-v1旧格式",
                 "path": ["第六章 药物代谢", "旧格式节点", "验收导入-v1旧格式"],
                 "pages": cb[0]["pages"], "summary": "v1 摘要正文。",
                 "points": ["v1 要点甲", "v1 要点乙"],
                 "sources": [{"page": cb[0]["ref"], "chunk_ids": [cb[0]["id"]]}]},
                {"id": "LN-02", "name": "验收导入-v1无出处",
                 "path": ["第六章 药物代谢", "旧格式节点", "验收导入-v1无出处"],
                 "pages": [], "summary": ""},
            ],
        }
        (V1_ART_DIR / f"{DOC_B}{SUFFIX}").write_text(
            json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
        _, s = _run_tool(self.db_path, "--apply", artifacts_dir=V1_ART_DIR)
        self.assertEqual(s["parsed_files"], 1)
        self.assertEqual(s["kps_created"], 2, "v1 格式的 2 条都应导入")
        self.assertEqual(_count(self.db_path, "knowledge_points"), 2)
        conn = _db(self.db_path)
        try:
            row = conn.execute(
                "SELECT summary FROM knowledge_points WHERE name LIKE '%验收导入-v1旧格式%'"
            ).fetchone()
            nsrc = conn.execute(
                "SELECT COUNT(*) FROM kp_sources s JOIN knowledge_points k ON k.id=s.kp_id "
                "WHERE k.name LIKE '%验收导入-v1旧格式%'").fetchone()[0]
        finally:
            conn.close()
        self.assertIsNotNone(row, "v1 条目的名称应可导入")
        self.assertIn("要点：v1 要点甲", row[0], "v1 的 points 也应拼进 summary")
        self.assertEqual(nsrc, 1, "v1 的 sources.chunk_ids 应落 kp_sources")

    def test_core_run_direct_dry_run(self):
        """`core.kp_import.run()` 直调（dashboard 接线路径）：纯函数 + 零写入。"""
        stats = kp_import.run(db_path=self.db_path, artifacts_dir=ART_DIR)
        self.assertEqual(stats["mode"], "dry_run")
        self.assertEqual(stats["kps_found"], 4)
        self.assertEqual(stats["kps_would_create"], 4)
        self.assertEqual(stats["kps_created"], 0, "默认 apply=False，创建数必须为 0")
        self.assertGreater(stats["kp_sources_would_add"], 0)
        self.assertIsInstance(stats["elapsed_ms"], int)
        self.assertEqual(_count(self.db_path, "knowledge_points"), 0, "dry-run 零写入")
        self.assertEqual(_count(self.db_path, "kp_sources"), 0)
        self.assertIsNone(_manifest(self.db_path))

        # 传 conn：调用方拥有连接，函数不得关闭它；dry-run 仍零写入
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            stats2 = kp_import.run(conn=conn, artifacts_dir=ART_DIR)
            self.assertEqual(stats2["mode"], "dry_run")
            self.assertEqual(stats2["kps_found"], 4)
            conn.execute("SELECT COUNT(*) FROM knowledge_points").fetchone()  # 连接仍可用
        finally:
            conn.close()
        self.assertEqual(_count(self.db_path, "knowledge_points"), 0, "传 conn 也不得写入")

        # 前置错误：库/产物目录不存在 → API 抛 KpImportError（CLI 映射退出码 2）
        with self.assertRaises(kp_import.KpImportError):
            kp_import.run(db_path=T09_DIR / "not-exists.db", artifacts_dir=ART_DIR)
        with self.assertRaises(kp_import.KpImportError):
            kp_import.run(db_path=self.db_path, artifacts_dir=T09_DIR / "no-artifacts")


def tearDownModule():
    shutil.rmtree(T09_DIR, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
