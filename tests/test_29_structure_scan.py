# -*- coding: utf-8 -*-
"""task-37 · 化学结构式补全：结构页扫描 / 文本漏抽 / 整页图复用 / 选择性识图构建。

**全程不调真实 AI**（测试红线的两条实现）：
1. `setUpModule` 把 `core.structures._vision_call` 换成"一被调用就 AssertionError"的哨兵，
   跑完再恢复 —— 任何测试若真走到视觉调用路径，会直接失败而不是悄悄花钱；
2. 所有"真实构建"用例都注入 `vision_fn`（假视觉函数，返回固定 JSON），不碰网络。

数据只读：真实 `index/study.db`（sqlite `mode=ro`）与 `raw/*.pptx`（zip 只读）；
所有产物一律写 `_work/structure/tests-tmp*`（临时目录，tearDown 删除），
**不写真实 `logs/knowledge`**。

> 注：任务书说"doc46/40 都应该有候选"。实测 doc#46 的
> `raw/4M胆碱及N胆碱受体阻断药.pptx` 是**纯文本转换件**（25 页、每页 1 个文本框、
> 0 图片/0 矢量对象/0 媒体），所以它的候选数**正确地为 0**；扫描会给出一条
> `warnings` 提示"结构式可能在旧版 .ppt 里，请用 PowerPoint 另存为 .pptx 后重扫"。
> 因此本用例用 doc#40 / doc#47 验证"候选页>0"，并专门断言 doc#46 的 0 候选 + 可读告警。

## 覆盖
- 结构页扫描：字段/schema/候选页>0/阈值可调/阈值非法可读错误/无文档可读错误；
- dry-run：0 调用、0 落盘；
- 文本漏抽对比：不崩、覆盖率字段、逐页条目；
- 整页图复用：全命中、缺图可读、"默认不渲染"；
- 构建：dry-run 预算 / 假视觉落盘 schema / 缓存复用 / force / limit / 失败页不崩 /
  无 key 可读错误；
- 提示词：真源与 tracked 副本逐字一致、块可提取、占位符替换；
- CLI：--dry-run/--status/--run 无 --yes 取消（退出码 3，0 次调用）。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
PROJECT = TESTS_DIR.parent
sys.path.insert(0, str(PROJECT))

from core import structures  # noqa: E402

REAL_DB = PROJECT / "index" / "study.db"
RAW = PROJECT / "raw"
TOOL = PROJECT / "tools" / "structure_build.py"
TRACKED_PROMPT = PROJECT / "docs" / "prompts" / "提示词-结构式识图-v1-2026-10-24.md"

DOC_DIAGRAMS = 40      # 1中枢神经系统药理学概论：图/示意图页多（候选>0）
DOC_CHEM = 47          # 5抗精神失常药物：吩噻嗪类结构页多（候选>0）
DOC_TEXT_ONLY = 46     # 4M胆碱及N胆碱受体阻断药：文本转换件（候选=0，有告警）

_TMP = None
_ORIG_VISION = None


def _boom(*a, **kw):
    raise AssertionError("测试期间不允许发生真实视觉调用（_vision_call 被哨兵替换）")


def _ensure_tmp_root() -> Path:
    root = PROJECT / "_work" / "structure"
    root.mkdir(parents=True, exist_ok=True)
    return root


def setUpModule():  # noqa: N802
    global _TMP, _ORIG_VISION
    if not REAL_DB.exists():
        raise unittest.SkipTest("缺少真实主库 index/study.db（只读数据源）")
    if not (RAW / "1中枢神经系统药理学概论.pptx").exists():
        raise unittest.SkipTest("缺少真实课件 raw/*.pptx（只读数据源）")
    _TMP = Path(tempfile.mkdtemp(prefix="tests-tmp-",
                                 dir=str(_ensure_tmp_root())))
    # 红线：整个测试模块期间禁止真实视觉调用
    _ORIG_VISION = structures._vision_call
    structures._vision_call = _boom


def tearDownModule():  # noqa: N802
    global _TMP
    if _ORIG_VISION is not None:
        structures._vision_call = _ORIG_VISION
    if _TMP is not None and _TMP.exists():
        shutil.rmtree(_TMP, ignore_errors=True)


def _fake_vision(**overrides):
    """返回一个假视觉函数：固定 JSON + 固定 usage（不联网）。"""
    payload = {
        "has_structure": True,
        "structure": "母核：吩噻嗪三环；取代基：C2-Cl；官能团：二甲氨基；立体化学：无",
        "smiles": "",
        "reaction": "",
        "uncertainty": "medium",
        "notes": "假模型输出（测试用）",
    }
    payload.update(overrides)
    text = "```json\n" + json.dumps(payload, ensure_ascii=False) + "\n```"

    def fn(img_path, prompt, *, model, max_tokens, thinking):
        return {"ok": True, "text": text,
                "usage": {"prompt_tokens": 2400, "completion_tokens": 260},
                "elapsed": 0.012, "model": model, "error": ""}
    return fn


class TestStructureScan(unittest.TestCase):
    """零 AI 的 PPTX 结构页扫描。"""

    def test_scan_doc47_has_candidates(self):
        """真实课件 doc#47：候选页 >0，候选字段齐全、页号有序。"""
        r = structures.scan_pptx(DOC_CHEM, db_path=REAL_DB, dry_run=True)
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(r["n_pages"], 86)
        self.assertGreater(r["n_candidates"], 0)
        self.assertEqual(len(r["candidates"]), r["n_candidates"])
        pages = [c["page"] for c in r["candidates"]]
        self.assertEqual(len(pages), len(set(pages)))
        self.assertTrue(all(1 <= p <= r["n_pages"] for p in pages))
        cand = r["candidates"][0]
        for key in ("page", "reasons", "score", "shape_count", "text_chars",
                    "keywords", "vec_geom", "pic_count"):
            self.assertIn(key, cand)
        self.assertTrue(cand["reasons"])
        # candidates 按 score 降序
        scores = [c["score"] for c in r["candidates"]]
        self.assertEqual(scores, sorted(scores, reverse=True))
        self.assertIn("A 矢量元素", r["meta"]["rule"])

    def test_scan_doc40_has_candidates(self):
        """真实课件 doc#40：候选页 >0（示意图/图形页较多）。"""
        r = structures.scan_pptx(DOC_DIAGRAMS, db_path=REAL_DB, dry_run=True)
        self.assertTrue(r["ok"], r.get("error"))
        self.assertGreater(r["n_candidates"], 0)
        self.assertGreater(r["n_pages"], 0)

    def test_scan_text_only_deck_has_warning(self):
        """doc#46 是纯文本转换件：候选=0 是正确的，但必须有可读告警。"""
        r = structures.scan_pptx(DOC_TEXT_ONLY, db_path=REAL_DB, dry_run=True)
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(r["n_pages"], 25)
        self.assertEqual(r["n_candidates"], 0)
        self.assertTrue(r["warnings"], "纯文本转换件应给出 warnings")
        self.assertIn(".ppt", r["warnings"][0])

    def test_scan_dry_run_writes_nothing(self):
        """dry-run：不落盘（连目录都不创建）。"""
        out = _TMP / "scan-dry"
        self.assertFalse(out.exists())
        r = structures.scan_pptx(DOC_CHEM, db_path=REAL_DB, dry_run=True, out_dir=out)
        self.assertTrue(r["ok"])
        self.assertIsNone(r["written"])
        self.assertFalse((out / f"{DOC_CHEM}-结构页.json").exists())

    def test_scan_writes_schema(self):
        """非 dry-run：原子写 <doc>-结构页.json，meta/candidates 字段正确。"""
        r = structures.scan_pptx(DOC_CHEM, db_path=REAL_DB, dry_run=False,
                                 out_dir=_TMP)
        self.assertTrue(r["ok"])
        path = Path(r["written"])
        self.assertTrue(path.exists())
        data = json.loads(path.read_text(encoding="utf-8"))
        meta = data["meta"]
        for key in ("version", "doc_id", "source_pptx", "generated_at",
                    "n_pages", "n_candidates", "rule", "thresholds"):
            self.assertIn(key, meta)
        self.assertEqual(meta["doc_id"], DOC_CHEM)
        self.assertEqual(meta["n_candidates"], r["n_candidates"])
        self.assertTrue(data["candidates"])
        # 再扫一次：同样的输入 → 同样的候选页
        r2 = structures.scan_pptx(DOC_CHEM, db_path=REAL_DB, dry_run=True)
        self.assertEqual([c["page"] for c in r2["candidates"]],
                         [c["page"] for c in data["candidates"]])

    def test_scan_thresholds_tunable(self):
        """阈值可逐项覆盖；压到极严时候选=0；未知阈值名给可读错误。"""
        strict = {k: 10 ** 9 for k in structures.DEFAULT_SCAN_THRESHOLDS}
        r = structures.scan_pptx(DOC_CHEM, db_path=REAL_DB, dry_run=True,
                                 thresholds=strict)
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(r["n_candidates"], 0)
        self.assertEqual(r["meta"]["thresholds"]["vec_geom_min"], 10 ** 9)
        bad = structures.scan_pptx(DOC_CHEM, db_path=REAL_DB, dry_run=True,
                                   thresholds={"no_such_key": 1})
        self.assertFalse(bad["ok"])
        self.assertIn("未知扫描阈值", bad["error"])

    def test_scan_missing_doc_readable(self):
        """不存在的 doc_id：返回可读 error，不抛异常。"""
        r = structures.scan_pptx(999999, db_path=REAL_DB)
        self.assertFalse(r["ok"])
        self.assertIn("文档不存在", r["error"])


class TestTextGaps(unittest.TestCase):
    """python-pptx 顶层文本 vs XML 全部 <a:t> 的漏抽对比。"""

    def test_text_gaps_schema_and_no_crash(self):
        r = structures.scan_text_gaps(DOC_CHEM, db_path=REAL_DB)
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(r["n_pages"], 86)
        self.assertEqual(r["n_compared"], len(r["pages"]))
        self.assertGreaterEqual(r["total_xml_chars"], r["total_missing_chars"])
        self.assertGreaterEqual(r["total_xml_chars"], 0)
        self.assertLessEqual(r["coverage_ratio"], 1.0)
        self.assertGreaterEqual(r["coverage_ratio"], 0.0)
        page = r["pages"][0]
        for key in ("page", "pptx_top_chars", "xml_chars", "missing_chars",
                    "missing_runs", "samples"):
            self.assertIn(key, page)
        self.assertGreaterEqual(r["pages_with_gaps"], 0)

    def test_text_gaps_missing_doc_readable(self):
        r = structures.scan_text_gaps(999999, db_path=REAL_DB)
        self.assertFalse(r["ok"])
        self.assertIn("文档不存在", r["error"])


class TestPageImages(unittest.TestCase):
    """复用 logs/figure_desc 里已缓存的整页图。"""

    def test_images_reused(self):
        r = structures.find_page_images(DOC_CHEM, db_path=REAL_DB)
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(r["images_count"], r["n_pages"])
        self.assertEqual(r["missing_pages"], [])
        self.assertEqual(r["rendered"], 0)          # 默认不渲染
        self.assertEqual(len(r["images"]), r["n_pages"])

    def test_missing_images_readable_and_no_render(self):
        """把图目录指到空目录：缺图页全部列出、不自动渲染、不崩。"""
        orig = structures.page_image_dir
        structures.page_image_dir = lambda src: _TMP / "empty-figure-desc"
        try:
            r = structures.find_page_images(DOC_TEXT_ONLY, db_path=REAL_DB)
        finally:
            structures.page_image_dir = orig
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(r["images_count"], 0)
        self.assertEqual(r["missing_pages"], list(range(1, r["n_pages"] + 1)))
        self.assertEqual(r["rendered"], 0)
        self.assertEqual(r["images"], {})

    def test_missing_doc_readable(self):
        r = structures.find_page_images(999999, db_path=REAL_DB)
        self.assertFalse(r["ok"])
        self.assertIn("文档不存在", r["error"])


class TestPrompt(unittest.TestCase):
    """提示词真源 / tracked 副本 / 占位符。"""

    def test_prompt_source_and_tracked_copy_identical(self):
        p = structures.load_prompt()
        self.assertTrue(p["sys"] and p["user"])
        self.assertIn("SMILES", p["sys"])
        self.assertIn("uncertainty", p["sys"])
        self.assertIn("JSON", p["sys"])
        self.assertIn("{deck_title}", p["user"])
        src = Path(p["path"])
        self.assertTrue(src.exists())
        import hashlib
        self.assertEqual(p["sha256"], hashlib.sha256(src.read_bytes()).hexdigest())
        self.assertTrue(TRACKED_PROMPT.exists(), "tracked 提示词副本缺失")
        self.assertEqual(
            src.read_bytes(), TRACKED_PROMPT.read_bytes(),
            "logs/knowledge 真源与 docs/prompts tracked 副本必须逐字一致")

    def test_build_user_prompt_fills_placeholders(self):
        p = structures.load_prompt()
        out = structures.build_user_prompt(
            p["user"], deck_title="测试课件", page=7,
            page_text="母核：苯二氮卓", reasons=["B 强结构关键词：母核"])
        self.assertIn("测试课件", out)
        self.assertIn("第 7 页", out)
        self.assertIn("母核：苯二氮卓", out)
        self.assertIn("B 强结构关键词", out)
        self.assertNotIn("{deck_title}", out)
        self.assertNotIn("{page_text}", out)


class TestBuildStructures(unittest.TestCase):
    """选择性识图构建：dry-run 预算 / 假视觉落盘 / 缓存 / limit / 失败页。"""

    def _candidate_pages(self, n=2):
        r = structures.scan_pptx(DOC_CHEM, db_path=REAL_DB, dry_run=True)
        return [c["page"] for c in r["candidates"][:n]]

    def test_dry_run_zero_calls_zero_writes(self):
        out = _TMP / "build-dry"
        plan = structures.build_structures(
            DOC_CHEM, dry_run=True, db_path=REAL_DB, out_dir=out, log=None)
        self.assertTrue(plan["ok"], plan.get("error"))
        self.assertTrue(plan["dry_run"])
        self.assertEqual(plan["n_calls"], 0)
        self.assertEqual(plan["calls"], [])
        self.assertIsNone(plan["written"])
        self.assertFalse((out / f"{DOC_CHEM}-结构式.json").exists())
        est = plan["estimate"]
        self.assertGreater(est["calls"], 0, "doc#47 有候选页，预计调用应 >0")
        self.assertGreater(est["cost_cny_peak"], 0)
        self.assertGreaterEqual(est["cost_cny_peak"], est["cost_cny_off_peak"])
        self.assertTrue(est["assumptions"])
        self.assertEqual(plan["prompt_sha256"], structures.load_prompt()["sha256"])

    def test_fake_vision_writes_schema(self):
        pages = self._candidate_pages(2)
        out = _TMP / "build-real"
        r = structures.build_structures(
            DOC_CHEM, pages=pages, dry_run=False, db_path=REAL_DB, out_dir=out,
            vision_fn=_fake_vision(), log=None)
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(r["n_calls"], 2)
        self.assertEqual(r["n_cached"], 0)
        path = Path(r["written"])
        self.assertTrue(path.exists())
        data = json.loads(path.read_text(encoding="utf-8"))
        meta = data["meta"]
        for key in ("version", "doc_id", "model", "calls", "cost_cny", "tokens",
                    "generated_at", "elapsed_s", "prompt_sha256", "n_pages",
                    "n_errors"):
            self.assertIn(key, meta)
        self.assertEqual(meta["calls"], 2)
        self.assertGreater(meta["cost_cny"], 0)
        self.assertEqual(meta["tokens"]["in"], 4800)
        self.assertEqual(meta["tokens"]["out"], 520)
        for p in pages:
            e = data["pages"][str(p)]
            self.assertIn(e["has_structure"], (True, False))
            self.assertEqual(e["uncertainty"], "medium")
            self.assertTrue(e["image_hash"])
            self.assertEqual(e["prompt_sha256"], meta["prompt_sha256"])
            self.assertIn("structure", e)
            self.assertEqual(e["error"], "")
        # 本模块红线：真实 _vision_call 一次都没被调用
        self.assertEqual(r["errors"], [])

    def test_cache_reuse_and_force(self):
        pages = self._candidate_pages(1)
        out = _TMP / "build-cache"
        fake = _fake_vision()
        r1 = structures.build_structures(DOC_CHEM, pages=pages, dry_run=False,
                                         db_path=REAL_DB, out_dir=out,
                                         vision_fn=fake, log=None)
        self.assertEqual(r1["n_calls"], 1)
        r2 = structures.build_structures(DOC_CHEM, pages=pages, dry_run=False,
                                         db_path=REAL_DB, out_dir=out,
                                         vision_fn=fake, log=None)
        self.assertEqual(r2["n_calls"], 0)
        self.assertEqual(r2["n_cached"], 1)
        self.assertIsNone(r2["written"], "全缓存命中不应重写产物")
        r3 = structures.build_structures(DOC_CHEM, pages=pages, dry_run=False,
                                         db_path=REAL_DB, out_dir=out,
                                         vision_fn=fake, force=True, log=None)
        self.assertEqual(r3["n_calls"], 1)
        # dry-run 也应识别缓存（预算按真实需调用页数算）
        plan = structures.build_structures(DOC_CHEM, pages=pages, dry_run=True,
                                           db_path=REAL_DB, out_dir=out, log=None)
        self.assertEqual(plan["n_cached"], 1)
        self.assertEqual(plan["estimate"]["calls"], 0)

    def test_limit_then_resume(self):
        pages = self._candidate_pages(2)
        out = _TMP / "build-limit"
        fake = _fake_vision()
        r1 = structures.build_structures(DOC_CHEM, pages=pages, dry_run=False,
                                         db_path=REAL_DB, out_dir=out,
                                         vision_fn=fake, limit=1, log=None)
        self.assertEqual(r1["n_calls"], 1)
        self.assertEqual(len(r1["pages_skipped_by_limit"]), 1)
        r2 = structures.build_structures(DOC_CHEM, pages=pages, dry_run=False,
                                         db_path=REAL_DB, out_dir=out,
                                         vision_fn=fake, log=None)
        self.assertEqual(r2["n_calls"], 1, "第二轮应补跑剩下的 1 页")
        self.assertEqual(r2["n_cached"], 1)

    def test_failure_pages_recorded_not_crash(self):
        pages = self._candidate_pages(2)
        out = _TMP / "build-fail"
        n = {"i": 0}

        def broken(img_path, prompt, *, model, max_tokens, thinking):
            n["i"] += 1
            if n["i"] == 1:
                raise RuntimeError("模拟网络中断")
            return {"ok": True, "text": "这不是 JSON，模型跑题了",
                    "usage": {"prompt_tokens": 100, "completion_tokens": 10},
                    "elapsed": 0.01, "model": model, "error": ""}

        r = structures.build_structures(DOC_CHEM, pages=pages, dry_run=False,
                                        db_path=REAL_DB, out_dir=out,
                                        vision_fn=broken, log=None)
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(r["n_calls"], 2)
        self.assertEqual(len(r["errors"]), 2)
        data = json.loads(Path(r["written"]).read_text(encoding="utf-8"))
        for p in pages:
            e = data["pages"][str(p)]
            self.assertTrue(e["error"], "失败页必须记 error")
            self.assertEqual(e["has_structure"], False)
        self.assertEqual(data["meta"]["n_errors"], 2)

    def test_no_api_key_readable(self):
        """真实构建但没有 key：可读错误、0 调用、0 写盘。"""
        out = _TMP / "build-nokey"
        saved = structures._api_key
        structures._api_key = lambda: None
        try:
            r = structures.build_structures(DOC_CHEM, pages=self._candidate_pages(1),
                                            dry_run=False, db_path=REAL_DB,
                                            out_dir=out, log=None)
        finally:
            structures._api_key = saved
        self.assertFalse(r["ok"])
        self.assertIn("DEEPSEEK_API_KEY", r["error"])
        self.assertFalse((out / f"{DOC_CHEM}-结构式.json").exists())

    def test_pages_out_of_range_readable(self):
        r = structures.build_structures(DOC_TEXT_ONLY, pages=[9999], dry_run=False,
                                        db_path=REAL_DB, out_dir=_TMP / "oob",
                                        vision_fn=_fake_vision(), log=None)
        self.assertFalse(r["ok"])
        self.assertIn("页号越界", r["error"])


class TestStatus(unittest.TestCase):
    def test_status_before_and_after(self):
        empty = structures.structure_status(DOC_CHEM, out_dir=_TMP / "status-none")
        self.assertTrue(empty["ok"])
        self.assertFalse(empty["scan"]["exists"])
        self.assertFalse(empty["structures"]["exists"])
        out = _TMP / "status"
        scan = structures.scan_pptx(DOC_CHEM, db_path=REAL_DB, dry_run=False,
                                    out_dir=out)
        first = [c["page"] for c in scan["candidates"][:1]]
        r = structures.build_structures(
            DOC_CHEM, pages=first, dry_run=False, db_path=REAL_DB, out_dir=out,
            vision_fn=_fake_vision(), log=None)
        self.assertTrue(r["ok"], r.get("error"))
        st = structures.structure_status(DOC_CHEM, out_dir=out)
        self.assertTrue(st["scan"]["exists"])
        self.assertEqual(st["scan"]["n_candidates"], scan["n_candidates"])
        self.assertTrue(st["structures"]["exists"])
        self.assertEqual(st["structures"]["calls"], 1)
        self.assertEqual(len(st["structures"]["with_structure"]), 1)


class TestCli(unittest.TestCase):
    """CLI 壳：默认 dry-run、--run 无 --yes 必须取消（退出码 3，0 调用）。"""

    def _run(self, args):
        return subprocess.run(
            [sys.executable, "-X", "utf8", str(TOOL), *args],
            cwd=str(PROJECT), capture_output=True, text=True, timeout=180,
            stdin=subprocess.DEVNULL,
            env={**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"})

    def test_cli_scan_dry_run_json(self):
        cp = self._run(["--doc", str(DOC_CHEM), "--dry-run", "--json",
                        "--db", str(REAL_DB),
                        "--out-dir", str(_TMP / "cli-scan")])
        self.assertEqual(cp.returncode, 0, cp.stderr[-800:])
        data = json.loads(cp.stdout)
        self.assertTrue(data["ok"])
        self.assertGreater(data["n_candidates"], 0)
        self.assertFalse((_TMP / "cli-scan" / f"{DOC_CHEM}-结构页.json").exists())

    def test_cli_run_without_yes_cancels(self):
        cp = self._run(["--doc", str(DOC_CHEM), "--run", "--limit", "1",
                        "--db", str(REAL_DB),
                        "--out-dir", str(_TMP / "cli-run")])
        self.assertEqual(cp.returncode, 3, cp.stdout[-500:] + cp.stderr[-500:])
        self.assertIn("已取消", cp.stderr)
        self.assertIn("未发生任何模型调用", cp.stderr)
        self.assertFalse((_TMP / "cli-run" / f"{DOC_CHEM}-结构式.json").exists())

    def test_cli_status_json(self):
        cp = self._run(["--doc", str(DOC_CHEM), "--status", "--json",
                        "--out-dir", str(_TMP / "cli-status")])
        self.assertEqual(cp.returncode, 0, cp.stderr[-800:])
        data = json.loads(cp.stdout)
        self.assertTrue(data["ok"])
        self.assertFalse(data["scan"]["exists"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
