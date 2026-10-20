"""预习后端 v2（2026-10-20 重做）验收：按章多产物 + 小节细分 + 新提示词/schema。

全程**不读真实库、不调模型、不写真实 logs**：

- 所有产物写到 `_work/verify/t17_private/` 临时目录（monkeypatch `config.LOG_DIR`）；
- `_preview_call` / `estimate_preview` / `ensure_sections_artifact` / `read_chapter_ch` /
  `chapter_sections` / `load_preview_prompts` 全部用假实现（纯单测）；
- `ensure_sections_artifact` 用假 `core.sections.load_blocks/detect_sections` 测
  「缺产物本地生成 / 抽不出节不伪造 / dry-run 不写」；
- 提示词契约读真实 `logs/knowledge/提示词-预习总结.md`（只读，不调模型）。
"""
from __future__ import annotations

import json
import shutil
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import env  # noqa: E402

sys.path.insert(0, str(env.PROJECT))
from core import config, preview  # noqa: E402
from core import sections as sections_mod  # noqa: E402

T17_DIR = env.VERIFY_DIR / "t17_private"
# 保存真实实现（_Base.setUp 会打补丁；少数用例要用真的）
_REAL_ENSURE = preview.ensure_sections_artifact
_REAL_LOAD_PROMPTS = preview.load_preview_prompts
DOC = 50
CHAPTER = 2


def setUpModule():
    shutil.rmtree(T17_DIR, ignore_errors=True)
    T17_DIR.mkdir(parents=True, exist_ok=True)


def tearDownModule():
    shutil.rmtree(T17_DIR, ignore_errors=True)


# ---------------------------------------------------------------- 假数据
def _info(no=CHAPTER, name="药代动力学", pages=(19, 20)):
    pages = list(pages)
    return {"ok": True, "doc_id": DOC, "no": no, "name": name,
            "heading_path": "药理学 第6版 刘晓东 > 第%d章 %s" % (no, name),
            "block_ids": [101, 102], "pages": pages,
            "page_start": pages[0], "page_end": pages[-1],
            "source": "chunks.heading_path"}


def _chapter_ch(no=CHAPTER):
    pages = [19, 20]
    cover = {19: [101], 20: [102]}
    blocks = {19: [{"chunk_id": 101, "page": 19, "kind": "正文块", "ord": 0}],
              20: [{"chunk_id": 102, "page": 20, "kind": "正文块", "ord": 1}]}
    ch = {"blocks": blocks, "cover": cover, "figrows": {}, "pages_all": pages,
          "max_page": pages[-1], "n_blocks": 2}
    material = ("===== 第 19 页 =====\n半衰期是药物消除一半所需的时间。（p.19）\n"
                "===== 第 20 页 =====\n清除率反映机体消除药物的能力。（p.20）\n")
    inv = [{"chunk_id": 101, "page": 19, "kind": "正文块", "ref": "p.19", "pages": [19]},
           {"chunk_id": 102, "page": 20, "kind": "正文块", "ref": "p.20", "pages": [20]}]
    stats = {"mode": "full", "chars": len(material), "n_sources": 2,
             "dropped_fig_blocks": 0, "truncated_pages": []}
    return ch, _info(no), material, inv, stats


def _prompt():
    user = ("【教材】{deck}\n【本章】{chapter}（{heading_path}）\n"
            "【页范围】{page_label}（共 {n_pages} 页）\n"
            "【节级大纲】\n{sections_outline}\n"
            "【本章材料】\n{chapter_material}\n")
    return {"sys": "你是带课前预习的大学老师；先细分小节，让学生上课能跟上老师任何速度。",
            "user": user, "sha256": "fake-prompt-sha", "path": "fake.md",
            "chars": len(user) + 20}


def _est(out_path):
    return {"ok": True, "preview_version": preview.PREVIEW_VERSION,
            "out_path": str(out_path), "out_path_per_chapter": str(out_path),
            "title": "药理学 第6版 刘晓东", "chapter": "第2章 药代动力学",
            "chapter_no": CHAPTER, "chapter_name": "药代动力学",
            "heading_path": "x > 第2章 药代动力学", "pages": [19, 20],
            "page_label": "19-20", "n_pages": 2, "n_blocks": 2,
            "n_sections": 0, "sections_read": False, "sections_fallback": True,
            "sections_would_build": True, "model": "fake-model",
            "thinking": True, "reasoning_effort": "high", "max_tokens": 16384,
            "skip_would_happen": False}


def _fake_call_factory(parsed, calls_seen=None):
    def _call(calls, kind, sys_p, user_p, **kw):
        if calls_seen is not None:
            calls_seen.append({"kind": kind, "user": user_p})
        rec = {"run": int(kw.get("run_no") or 1), "kind": kind, "ok": True,
               "tokens_in": 1200, "tokens_out": 800, "reasoning_tokens": 500,
               "cost_cny": 0.0021, "model": kw.get("model") or "fake-model"}
        calls.append(rec)
        return {"ok": True, "parsed": parsed, "usage": {}, "raw": "{}",
                "model": rec["model"], "error": "", "call_index": len(calls) - 1,
                "call": rec}
    return _call


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = T17_DIR / self._testMethodName
        shutil.rmtree(self.tmp, ignore_errors=True)
        (self.tmp / "knowledge").mkdir(parents=True, exist_ok=True)
        self._patches = [
            mock.patch.object(config, "LOG_DIR", self.tmp),
            mock.patch.object(preview, "load_preview_prompts", lambda *a, **k: _prompt()),
            mock.patch.object(preview, "read_chapter_ch",
                              lambda *a, **k: _chapter_ch()),
            mock.patch.object(preview, "estimate_preview",
                              side_effect=lambda *a, **k: _est(
                                  preview.preview_out_file_per_chapter(DOC, CHAPTER))),
            mock.patch.object(preview, "ensure_sections_artifact",
                              side_effect=lambda *a, **k: {
                                  "ok": True, "created": True, "read": False,
                                  "n_sections": 0, "n_chapters": 0,
                                  "source": "generated", "note": "fake"}),
            mock.patch.object(preview, "chapter_sections",
                              side_effect=lambda *a, **k: {
                                  "read": False, "source": "fake", "n_sections": 0,
                                  "fallback": True, "sections": [], "note": "no"}),
        ]
        for p in self._patches:
            p.start()
        self._enabled_old = config.__dict__.get("PREVIEW_ENABLED")
        config.__dict__["PREVIEW_ENABLED"] = 1
        self.log = lambda *a, **k: None

    def tearDown(self):
        if self._enabled_old is None:
            config.__dict__.pop("PREVIEW_ENABLED", None)
        else:
            config.__dict__["PREVIEW_ENABLED"] = self._enabled_old
        for p in reversed(self._patches):
            p.stop()


class TestPreviewSchema(_Base):
    def test_new_schema_validation_and_artifact(self):
        """新 schema：小节写入结构、pages 越界清空、空/重名小节丢弃、不采信模型 chunk_ids。"""
        parsed = {
            "overview": "本章主线：药物在体内的处置。",
            "study_order": ["先看半衰期", "再看清除率"],
            "difficulties": ["半衰期与清除率的关系"],
            "prerequisites": ["消除动力学基础"],
            "exam_focus": ["半衰期计算"],
            "subsections": [
                {"title": "吸收与生物利用度", "pages": [19, 20], "why": "说明进入体内的量",
                 "core": [{"name": "生物利用度", "explain": "进入体循环的比例"}],
                 "lecture": "老师会先用口服举例，再对比静脉。",
                 "pitfalls": ["把绝对与相对生物利用度混为一谈"],
                 "self_check": [{"q": "F 表示什么？", "a": "生物利用度"}]},
                {"title": "半衰期", "pages": [19, 999], "why": "决定给药间隔",
                 "core": [{"name": "半衰期", "explain": "药物消除一半所需时间"}],
                 "lecture": "老师会画半对数图。", "pitfalls": [], "self_check": []},
                {"title": "", "pages": [20], "why": "没有标题应丢弃"},
                {"title": "吸收与生物利用度", "pages": [20], "why": "重名应去重",
                 "core": [{"name": "重复", "explain": "x"}]},
                {"title": "空小节"},
            ],
            "key_terms": [{"term": "半衰期", "en": "t1/2",
                           "def": "药物消除一半所需时间", "why": "决定给药间隔"}],
        }
        seen = []
        with mock.patch.object(preview, "_preview_call",
                               side_effect=_fake_call_factory(parsed, seen)):
            rep = preview.build_chapter_preview(DOC, CHAPTER, log=self.log)
        self.assertTrue(rep["ok"])
        self.assertEqual(rep["status"], "done")
        out = Path(rep["out_path"])
        self.assertTrue(out.exists(), "应按章落盘")
        self.assertEqual(out.name, "50-预习总结-第2章.json")
        art = json.loads(out.read_text(encoding="utf-8"))
        meta = art["meta"]
        self.assertEqual(meta["preview_version"], preview.PREVIEW_VERSION)
        self.assertEqual(meta["chapter_no"], CHAPTER)
        self.assertEqual(meta["status"], "done")
        self.assertEqual(meta["parser"], "v2_subsections")
        self.assertEqual(meta["n_subsections"], 2)
        self.assertEqual(meta["usage_this_run"]["calls"], 1, "每章只应有 1 次调用")
        self.assertEqual(meta["prompt_sha256"], "fake-prompt-sha")
        self.assertIn("sections_source", meta)
        subs = art["subsections"]
        self.assertEqual([s["title"] for s in subs], ["吸收与生物利用度", "半衰期"])
        self.assertEqual(subs[0]["pages"], [19, 20])
        self.assertEqual(subs[0]["page_label"], "19-20")
        self.assertEqual(subs[0]["chunk_ids"], [101, 102], "出处由管线按页算，不采信模型")
        self.assertEqual(subs[1]["pages"], [], "越界页码应清空该小节 pages")
        warns = "；".join(meta["warnings"])
        self.assertIn("页码不在本章范围", warns)
        self.assertIn("重复", warns)
        self.assertIn("title", warns)
        self.assertIn("没有任何内容字段", warns)
        # 章级 summary / terms / sources
        self.assertEqual(art["summary"]["overview"], parsed["overview"])
        self.assertEqual(art["summary"]["study_order"], parsed["study_order"])
        self.assertEqual(art["summary"]["exam_focus"], parsed["exam_focus"])
        self.assertEqual([t["term"] for t in art["terms"]], ["半衰期"])
        self.assertEqual(sorted(s["chunk_id"] for s in art["sources"]), [101, 102])
        # 用户消息里必须带上章级材料与大纲（1 次调用里完成小节细分）
        self.assertEqual(len(seen), 1)
        self.assertIn("===== 第 19 页 =====", seen[0]["user"])
        self.assertIn("第2章 药代动力学", seen[0]["user"])

    def test_legacy_key_points_fallback(self):
        """模型只给旧 key_points → 按 section 归组兜底生成 subsections，并标记 parser。"""
        parsed = {
            "overview": "旧 schema 的章级概述。",
            "key_points": [
                {"point": "吸收", "detail": "药物进入体循环", "pages": [19], "section": 1},
                {"point": "分布", "detail": "药物分布到组织", "pages": [20], "section": 1},
                {"point": "代谢", "detail": "肝脏代谢", "pages": [20]},
            ],
            "key_terms": [{"term": "半衰期", "en": "", "def": "一半时间", "why": "重要"}],
        }
        secs = [{"index": 1, "title": "第一节 体内过程", "page_start": 19,
                 "page_end": 20, "level": 1, "form": "第X节", "pages": [19, 20]}]
        with mock.patch.object(preview, "chapter_sections",
                              side_effect=lambda *a, **k: {
                                  "read": True, "source": "fake", "n_sections": 1,
                                  "fallback": False, "sections": secs, "note": ""}), \
             mock.patch.object(preview, "_preview_call",
                              side_effect=_fake_call_factory(parsed)):
            rep = preview.build_chapter_preview(DOC, CHAPTER, log=self.log)
        art = json.loads(Path(rep["out_path"]).read_text(encoding="utf-8"))
        self.assertEqual(art["meta"]["parser"], "legacy_key_points")
        self.assertEqual(art["meta"]["n_subsections"], 2)
        self.assertIn(art["meta"]["sections_source"], ("none", "generated", "artifact"))
        subs = art["subsections"]
        self.assertTrue(subs[0]["from_legacy_key_points"])
        self.assertEqual(subs[0]["title"], "第一节 体内过程")
        self.assertEqual([c["name"] for c in subs[0]["core"]], ["吸收", "分布"])
        self.assertEqual(subs[1]["title"], "小节 2")
        self.assertEqual(art["summary"]["overview"], parsed["overview"])
        self.assertIn("旧 schema", "；".join(art["meta"]["warnings"]))

    def test_multi_chapter_paths_status_and_read(self):
        """多章产物互不覆盖；preview_status / read_preview_artifact 可读；旧单文件兼容。"""
        outs = []

        def _build(ch, overview, out_name_expected):
            parsed = {"overview": overview, "subsections": [
                {"title": "唯一小节", "pages": [19], "why": "w",
                 "core": [{"name": "概念", "explain": "e"}]}]}
            with mock.patch.object(preview, "read_chapter_ch",
                                   side_effect=lambda *a, **k: _chapter_ch(ch)), \
                 mock.patch.object(preview, "estimate_preview",
                                   side_effect=lambda *a, **k: dict(
                                       _est(preview.preview_out_file_per_chapter(DOC, ch)),
                                       chapter="第%d章" % ch, chapter_no=ch,
                                       title="药理学 第6版 刘晓东")), \
                 mock.patch.object(preview, "_preview_call",
                                   side_effect=_fake_call_factory(parsed)):
                rep = preview.build_chapter_preview(DOC, ch, log=self.log)
            outs.append(Path(rep["out_path"]))
            return rep

        r1 = _build(1, "第一章概述", "50-预习总结-第1章.json")
        r2 = _build(2, "第二章概述", "50-预习总结-第2章.json")
        self.assertEqual({p.name for p in outs},
                         {"50-预习总结-第1章.json", "50-预习总结-第2章.json"})
        self.assertTrue(all(p.exists() for p in outs), "两章产物应并存、互不覆盖")
        st = preview.preview_status(DOC)
        self.assertIn(1, st)
        self.assertIn(2, st)
        self.assertEqual(st[1]["n_subsections"], 1)
        self.assertEqual(st[2]["status"], "done")
        self.assertEqual(st["_schema"], preview.PREVIEW_SCHEMA)
        rd1 = preview.read_preview_artifact(DOC, 1)
        rd2 = preview.read_preview_artifact(DOC, "第2章")
        self.assertTrue(rd1["ok"] and rd2["ok"])
        self.assertEqual(rd1["artifact"]["summary"]["overview"], "第一章概述")
        self.assertEqual(rd2["artifact"]["summary"]["overview"], "第二章概述")
        self.assertTrue(rd1["is_current_version"])
        # 旧单文件（v1 路径）只读兼容：章号匹配时可读，并标 legacy
        legacy = preview.legacy_preview_out_file(DOC)
        legacy.write_text(json.dumps({
            "meta": {"preview_version": "C-0094-preview-v1", "chapter_no": 3,
                     "chapter_name": "旧章", "pages": [1], "status": "done"},
            "summary": {"overview": "旧产物", "key_points": [
                {"point": "旧要点", "detail": "旧细节", "pages": [1]}]},
            "sections": [], "sources": [],
        }, ensure_ascii=False), encoding="utf-8")
        st2 = preview.preview_status(DOC)
        self.assertIn("_old_single", st2)
        self.assertEqual(st2["_old_single"]["chapter_no"], 3)
        rd3 = preview.read_preview_artifact(DOC, 3)
        self.assertTrue(rd3["ok"])
        self.assertTrue(rd3["legacy"])
        self.assertEqual(rd3["artifact"]["summary"]["overview"], "旧产物")
        self.assertTrue(rd3["artifact"]["meta"]["legacy"])


class TestDryRunAndSections(_Base):
    def test_dry_run_writes_nothing(self):
        """dry-run（含旧名 build_preview）：不调模型、不写任何文件。"""
        rep = preview.build_chapter_preview(DOC, CHAPTER, dry_run=True, log=self.log)
        self.assertTrue(rep["ok"] and rep["dry_run"])
        self.assertIn("out_path", rep)
        rep2 = preview.build_preview(DOC, CHAPTER, dry_run=True, log=self.log)
        self.assertTrue(rep2["ok"] and rep2["dry_run"])
        files = list((self.tmp / "knowledge").glob("*"))
        self.assertEqual(files, [], "dry-run 不得写任何产物/细目文件")

    def test_ensure_sections_missing_no_fake(self):
        """缺细目：抽不出节如实留空（不写空产物）；抽出节则本地生成、二次复用。"""
        # a) 切分结果为空 → 不伪造、不写文件
        empty = sections_mod.Sections(doc_id=DOC, chapters=[])
        with mock.patch.object(preview, "ensure_sections_artifact", _REAL_ENSURE), \
             mock.patch.object(sections_mod, "load_blocks",
                              return_value=([], [], {"title": "药理学"})), \
             mock.patch.object(sections_mod, "detect_sections", return_value=empty):
            dry = preview.ensure_sections_artifact(DOC, dry_run=True)
        self.assertTrue(dry.get("would_generate"))
        self.assertFalse(preview.sections_out_file(DOC).exists(),
                         "ensure dry-run 不得写文件")
        with mock.patch.object(preview, "ensure_sections_artifact", _REAL_ENSURE), \
             mock.patch.object(sections_mod, "load_blocks",
                              return_value=([], [], {"title": "药理学"})), \
             mock.patch.object(sections_mod, "detect_sections", return_value=empty):
            r0 = preview.ensure_sections_artifact(DOC)
        self.assertTrue(r0["ok"])
        self.assertFalse(r0["created"])
        self.assertEqual(r0["n_sections"], 0)
        self.assertIn("不伪造节", r0["note"])
        self.assertFalse(preview.sections_out_file(DOC).exists(), "不许写空产物")

        # b) 有节 → 本地生成；再调 → 复用（created=False, read=True）
        sec = sections_mod.Section(
            chapter_no=1, chapter_name="绪论", index=1, form="第X节", level=1,
            number="一", title="第一节 绪论", raw="第一节 绪论",
            page_start=1, page_end=2)
        chs = sections_mod.ChapterSections(number=1, name="绪论", page_start=1,
                                           page_end=2, sections=[sec])
        nonempty = sections_mod.Sections(doc_id=DOC, chapters=[chs], book_title="药理学")
        with mock.patch.object(preview, "ensure_sections_artifact", _REAL_ENSURE), \
             mock.patch.object(sections_mod, "load_blocks",
                              return_value=([], [], {"title": "药理学"})), \
             mock.patch.object(sections_mod, "detect_sections", return_value=nonempty):
            r1 = preview.ensure_sections_artifact(DOC)
            r2 = preview.ensure_sections_artifact(DOC)
        self.assertTrue(r1["created"])
        self.assertEqual(r1["n_sections"], 1)
        self.assertTrue(preview.sections_out_file(DOC).exists())
        self.assertFalse(r2["created"])
        self.assertTrue(r2["read"])
        self.assertIn("0 次模型调用", r1["note"])

    def test_prompt_contract(self):
        """真实提示词真源：两个块可提取、USER 占位符齐全、目标/小节要求写进 SYS。"""
        with mock.patch.object(preview, "load_preview_prompts", _REAL_LOAD_PROMPTS):
            p = preview.load_preview_prompts()
        self.assertGreater(len(p["sys"]), 500)
        self.assertGreater(len(p["user"]), 100)
        for ph in ("{deck}", "{chapter}", "{heading_path}", "{page_label}",
                   "{n_pages}", "{sections_outline}", "{chapter_material}"):
            self.assertIn(ph, p["user"], "USER 占位符缺失：%s" % ph)
        for kw in ("小节", "跟上", "JSON", "pages"):
            self.assertIn(kw, p["sys"], "SYS 未覆盖关键要求：%s" % kw)
        review = env.PROJECT / "docs" / "preview" / "提示词-预习总结-v2-2026-10-20.md"
        self.assertTrue(review.exists(), "tracked 审查副本应存在")
        self.assertEqual(preview.PREVIEW_VERSION, "C-0110-preview-v2")


if __name__ == "__main__":
    unittest.main(verbosity=2)
