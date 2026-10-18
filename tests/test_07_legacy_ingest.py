"""P4 回归：旧版 Office（.ppt/.doc/.xls）从看板入库链路的**判定层**验收。

产品改动：commit 430b4d8 —— core 层本来就会把 legacy 自动转 OOXML
（core/extract.py::ensure_ooxml + tools/convert_legacy.ps1），但看板
raw 列表 / 导入预览 / 批处理计划把它当“不支持”。本模块只验证**判定与计划**：

  F92  GET /api/raw          → legacy 文件 supported=true / kind='legacy' / 带 note
  F93  POST /api/import      → 预览把 legacy 标为 supported 且带 note；unknown 仍不支持
  F94  POST /api/ingest/batch/plan → legacy 纳入待处理（n_unsupported=0、will_skip=false）
  F95  “转换产物已入库”场景：raw 里 x.ppt + x.pptx + documents 记录
       → /api/raw 的 x.ppt state='indexed' 且带 doc_id；batch/plan 的 x.ppt will_skip=true

⚠ 红线：**不真正触发转换/入库**——不跑 COM/PowerShell 转换脚本、不调 LLM、不写文档表
（除 F95 为模拟“产物已入库”而预插的一条 documents 记录，落点仍是临时副本库）。
"""

import json
import os
import sqlite3
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import env  # noqa: E402

_SRV = None
RAW_LEGACY = None          # 本模块专用的 raw 目录（放在沙箱根下 = 生产口径 raw 在 ROOT 下）
IMPORT_SRC = None
DOCS_BEFORE = None


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _ole() -> bytes:
    """合成一个“旧版 Office 二进制”文件头（判定只看扩展名，不解析内容）。"""
    return b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 96


def setUpModule():
    global _SRV, RAW_LEGACY, IMPORT_SRC, DOCS_BEFORE
    env.prepare(clean_data=True)

    # ① raw 目录放在沙箱根下：这样 documents.path 能保持“相对项目根 + 反斜杠”
    #    的生产口径（ingest._rel 的形态），_norm_doc_path/_norm_rel 才能逐字对上。
    RAW_LEGACY = env.SANDBOX_DIR / "raw_legacy"
    if RAW_LEGACY.exists():
        import shutil
        shutil.rmtree(RAW_LEGACY)
    RAW_LEGACY.mkdir(parents=True)

    # ② 未转换的 legacy（.ppt/.doc/.xls 各一份）
    for name in ("legacy_unindexed.ppt", "legacy_unindexed.doc", "legacy_unindexed.xls"):
        _write(RAW_LEGACY / name, _ole())

    # ③ “转换产物已入库”的成对文件：x.ppt（旧）+ x.pptx（新，mtime 更新）
    ppt = RAW_LEGACY / "legacy_converted.ppt"
    pptx = RAW_LEGACY / "legacy_converted.pptx"
    _write(ppt, _ole())
    _write(pptx, b"PK\x03\x04" + b"\x00" * 128)
    old = time.time() - 3600
    os.utime(ppt, (old, old))
    os.utime(pptx, (old + 60, old + 60))

    # ④ 往临时副本库插一条“产物 .pptx 已入库”的记录（不经过任何入库/转换）
    main_db = env.DATA_DIR / "main.db"
    doc_path = os.path.join("raw_legacy", "legacy_converted.pptx")  # ingest._rel 口径
    conn = sqlite3.connect(str(main_db))
    try:
        conn.execute(
            "INSERT INTO documents(path, abs_path, kind, title, content_hash, size_bytes,"
            " mtime, n_chunks, n_chars, status, role, corpus, indexed_at, updated_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (doc_path, str(pptx.resolve()), "pptx", "legacy_converted",
             env.sha256(pptx), pptx.stat().st_size, pptx.stat().st_mtime,
             19, 1000, "ok", "courseware", "main", time.time(), time.time()),
        )
        conn.commit()
        DOCS_BEFORE = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
    finally:
        conn.close()

    # ⑤ 导入预览用的外部目录（含 legacy + unknown + 正常格式）
    IMPORT_SRC = env.SANDBOX_DIR / "import_legacy_src"
    if IMPORT_SRC.exists():
        import shutil
        shutil.rmtree(IMPORT_SRC)
    for name in ("a.ppt", "b.doc", "c.xls"):
        _write(IMPORT_SRC / name, _ole())
    _write(IMPORT_SRC / "unknown.xyz", b"not a supported format")
    _write(IMPORT_SRC / "plain.md", b"# plain markdown\n")

    # ⑥ 起隔离实例：KB_RAW 指到本模块的 raw 目录（其余仍是临时副本）
    _SRV = env.Server(tag="legacy", extra_env={"KB_RAW": str(RAW_LEGACY)})
    _SRV.start()


def tearDownModule():
    if _SRV is not None:
        _SRV.stop()


def _post(path, payload):
    st, obj = _SRV.post_json(path, payload, timeout=120)
    return st, obj or {}


def _get(path, **params):
    st, obj = _SRV.get_json(path, timeout=120, **params)
    assert st == 200, f"{path} 返回 {st}: {obj}"
    return obj or {}


class TestLegacyIngestDecision(unittest.TestCase):
    def _raw_files(self):
        raw = _get("/api/raw")
        return {f["name"]: f for f in raw["files"]}, raw

    def test_raw_list_legacy_supported(self):
        """F92：raw 列表里 .ppt/.doc/.xls 是 supported=true、kind='legacy'、带 note。"""
        files, raw = self._raw_files()
        for name in ("legacy_unindexed.ppt", "legacy_unindexed.doc", "legacy_unindexed.xls"):
            self.assertIn(name, files, f"raw 列表缺 {name}：{sorted(files)}")
            f = files[name]
            self.assertEqual(f["kind"], "legacy", f"{name} kind 应为 legacy")
            self.assertTrue(f["supported"], f"{name} 应 supported=true（旧格式可自动转换）")
            self.assertTrue(f.get("note"), f"{name} 缺 note 提示")
            self.assertIn("自动转换", f["note"])
            self.assertEqual(f["state"], "new", f"{name} 未转换时应为 new")
        # 没有 unknown 文件，因此 counts.unsupported 必须是 0
        self.assertEqual(raw["counts"]["unsupported"], 0)
        # 只做了判定，绝不能产生转换产物
        self.assertFalse((RAW_LEGACY / "legacy_unindexed.pptx").exists())
        self.assertFalse((RAW_LEGACY / "legacy_unindexed.docx").exists())
        self.assertFalse((RAW_LEGACY / "legacy_unindexed.xlsx").exists())

    def test_import_preview_legacy(self):
        """F93：导入预览把 legacy 标为 supported 且带 note；unknown 仍不支持。"""
        st, d = _post("/api/import", {"src": str(IMPORT_SRC), "sub": "legacy导入",
                                      "recursive": True, "dry_run": True})
        self.assertEqual(st, 200, f"导入预览失败：{d}")
        self.assertTrue(d["dry_run"])
        by_name = {f["name"]: f for f in d["files"]}
        for name in ("a.ppt", "b.doc", "c.xls"):
            self.assertIn(name, by_name, f"导入预览缺 {name}：{sorted(by_name)}")
            f = by_name[name]
            self.assertEqual(f["kind"], "legacy")
            self.assertTrue(f["supported"], f"{name} 应 supported=true")
            self.assertTrue(f.get("note"), f"{name} 缺 note")
            self.assertIn("自动转换", f["note"])
        unknown = by_name["unknown.xyz"]
        self.assertFalse(unknown["supported"], "unknown 格式仍应不支持")
        self.assertTrue(by_name["plain.md"]["supported"])
        # dry_run 不得真的复制文件
        self.assertFalse((RAW_LEGACY / "legacy导入").exists())
        # 也不得触发转换
        self.assertFalse((IMPORT_SRC / "a.pptx").exists())

    def test_batch_plan_legacy(self):
        """F94：批处理计划把 legacy 纳入待处理（n_unsupported=0 / will_skip=false）。"""
        names = ["legacy_unindexed.ppt", "legacy_unindexed.doc", "legacy_unindexed.xls"]
        paths = [str(RAW_LEGACY / n) for n in names]
        st, d = _post("/api/ingest/batch/plan", {"paths": paths})
        self.assertEqual(st, 200, f"批处理计划失败：{d}")
        self.assertEqual(d["n_files"], 3)
        self.assertEqual(d["n_unsupported"], 0, f"legacy 不应计入 unsupported：{d['files']}")
        self.assertGreaterEqual(d["n_todo"], 3, f"legacy 应进入待处理：{d['files']}")
        by_name = {f["name"]: f for f in d["files"]}
        for name in names:
            f = by_name[name]
            self.assertEqual(f["kind"], "legacy")
            self.assertTrue(f["supported"])
            self.assertFalse(f["will_skip"], f"{name} 未转换时不应 will_skip")
            self.assertTrue(f.get("note"), f"{name} 缺 note")
            self.assertIn("自动转换", f["note"])
        # 判定层不得产生任何转换产物或新增文档记录
        conn = sqlite3.connect(str(env.DATA_DIR / "main.db"))
        try:
            n_docs = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(n_docs, DOCS_BEFORE, "计划层不得写 documents 表")
        self.assertFalse((RAW_LEGACY / "legacy_unindexed.pptx").exists())

    def test_converted_artifact_indexed(self):
        """F95：产物已在库时，源 .ppt 显示 indexed+doc_id；计划里 will_skip=true。"""
        files, _raw = self._raw_files()
        self.assertIn("legacy_converted.ppt", files)
        f = files["legacy_converted.ppt"]
        self.assertEqual(f["kind"], "legacy")
        self.assertEqual(f["state"], "indexed",
                         f"转换产物已入库时源文件应显示已入库：{f}")
        self.assertIsNotNone(f.get("doc_id"), "已入库的 legacy 项应带 doc_id")
        conn = sqlite3.connect(str(env.DATA_DIR / "main.db"))
        try:
            row = conn.execute(
                "SELECT id, n_chunks FROM documents WHERE path LIKE ?",
                ("%legacy_converted.pptx",)).fetchone()
        finally:
            conn.close()
        self.assertIsNotNone(row, "预插的产物 documents 记录不存在")
        self.assertEqual(f["doc_id"], row[0])
        self.assertEqual(f.get("n_chunks"), row[1])
        self.assertIn("已自动转换", f.get("note", ""))
        self.assertIn("legacy_converted.pptx", f.get("note", ""))

        # 批处理计划：同一份源文件应被跳过（避免重复转换）
        st, d = _post("/api/ingest/batch/plan",
                      {"paths": [str(RAW_LEGACY / "legacy_converted.ppt")]})
        self.assertEqual(st, 200)
        self.assertEqual(d["n_files"], 1)
        item = d["files"][0]
        self.assertTrue(item["supported"])
        self.assertTrue(item["will_skip"], f"产物已入库时计划应 will_skip：{item}")
        self.assertEqual(d["n_todo"], 0)
        self.assertEqual(d["n_skip"], 1)
        self.assertIn("已转换", item.get("note", ""))
        # 整个测试模块都没有真正转换：仍只有那一个 .pptx 产物
        produced = sorted(p.name for p in RAW_LEGACY.glob("legacy_unindexed.*"))
        self.assertEqual(produced, ["legacy_unindexed.doc", "legacy_unindexed.ppt",
                                    "legacy_unindexed.xls"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
