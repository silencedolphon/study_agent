"""M0-6b（整理产物导入看板入口）+ M0-8（移动端基础）验收。

覆盖：
  · M0-6b API：`GET /api/kp-import/preview` 只读预览（dry_run、零写入）、
    `POST /api/kp-import/apply` 真写库且**二次 apply 新增 0**（幂等）、
    `doc_id` 过滤与非法值 400、`artifacts_dir` 不接受外部路径（防路径注入）；
  · M0-6b 前端：知识点页「导入已整理好的知识点（0 费用）」按钮存在并绑真实动作、
    确认弹窗/重复提示/导入后刷新都在代码里；
  · M0-8：≤560px 媒体查询把表格改成卡片式、长串断行；问答页自动聚焦用
    `focus({preventScroll:true})`（首屏不再被顶下去）；8 视图移动端无横向滚动的
    浏览器实测证据见 `_work/m0/waveB/report.md`（本文件做静态回归断言）。

隔离：用 `tests/env.py` 的沙箱服务 + 数据副本；产物 fixture 写在
`_work/verify/data/logs/knowledge/`（被测进程的 KB_LOG 指向这里），不碰真实 logs/。
"""

import json
import sqlite3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import env  # noqa: E402

_SRV = None
_ART_DIR = None
_DOC_ID = 2


def setUpModule():
    global _SRV, _ART_DIR
    env.prepare(clean_data=True)
    # 造一份整理产物 fixture：doc_id=2 在 fixture 主库里真实存在（有文档/切块）
    main_db = env.DATA_DIR / "main.db"
    with sqlite3.connect(str(main_db)) as c:
        doc = c.execute("SELECT id, title FROM documents WHERE id=?", (_DOC_ID,)).fetchone()
        assert doc, f"fixture 主库应有 doc_id={_DOC_ID}"
        chunks = [r[0] for r in c.execute(
            "SELECT id FROM chunks WHERE doc_id=? ORDER BY ord LIMIT 3", (_DOC_ID,))]
        assert chunks, f"fixture 主库 doc {_DOC_ID} 应有切块"
    _ART_DIR = env.DATA_DIR / "logs" / "knowledge"
    _ART_DIR.mkdir(parents=True, exist_ok=True)
    artifact = {
        "meta": {"doc_id": _DOC_ID, "pipeline_version": "m0-test-fixture",
                 "generated_at": "2026-10-18T00:00:00"},
        "knowledge_points": [
            {"id": "t-001", "name": "M0-6b-导入验收-甲",
             "node_path": ["第七章 药物排泄", "肾脏排泄"],
             "pages": [1], "summary": "导入验收用知识点甲",
             "points": ["要点一", "要点二"], "material_block_ids": [chunks[0]]},
            {"id": "t-002", "name": "M0-6b-导入验收-乙",
             "node_path": ["第七章 药物排泄", "胆汁排泄"],
             "pages": [1], "summary": "导入验收用知识点乙",
             "material_block_ids": [chunks[-1]]},
        ],
    }
    (_ART_DIR / f"{_DOC_ID}-末级知识点总结.json").write_text(
        json.dumps(artifact, ensure_ascii=False), encoding="utf-8")
    _SRV = env.Server(tag="m0import")
    _SRV.start()


def tearDownModule():
    if _SRV is not None:
        _SRV.stop()


def _get(path, **params):
    st, obj = _SRV.get_json(path, timeout=180, **params)
    return st, (obj or {})


def _post(path, payload):
    st, obj = _SRV.post_json(path, payload, timeout=180)
    return st, (obj or {})


def _kp_names():
    return {k["name"] for k in _get("/api/kps")[1]["knowledge_points"]}


class TestKpImportApi(unittest.TestCase):
    """M0-6b：导入接口（preview → apply → 幂等）。"""

    def test_01_preview_is_dry_run(self):
        """预览：200 + dry_run + 将建 2 条，且**不写库**。"""
        names_before = _kp_names()
        st, pv = _get("/api/kp-import/preview")
        self.assertEqual(st, 200, f"preview 返回 {st}: {pv}")
        self.assertTrue(pv.get("ok"), pv)
        self.assertEqual(pv.get("mode"), "dry_run")
        self.assertEqual(pv.get("scanned_files"), 1)
        self.assertEqual(pv.get("docs_matched"), 1)
        self.assertEqual(pv.get("kps_would_create"), 2, pv)
        self.assertGreaterEqual(pv.get("kp_sources_would_add"), 2, pv)
        self.assertEqual(pv.get("kps_created"), 0, "dry_run 不得真建")
        # 产物目录固定：响应里落在隔离数据目录（KB_LOG）下，不是任意路径
        art = str(pv.get("artifacts_dir", "")).replace("/", "\\").lower()
        self.assertTrue(art.startswith(str(env.DATA_DIR).replace("/", "\\").lower()),
                        f"artifacts_dir 不在隔离数据目录下：{art}")
        self.assertEqual(_kp_names(), names_before, "preview 不得写库")

    def test_02_apply_then_idempotent(self):
        """真写：/api/kps 增加 2 条；二次 apply 新增 0（幂等）。"""
        st, ap = _post("/api/kp-import/apply", {})
        self.assertEqual(st, 200, f"apply 返回 {st}: {ap}")
        self.assertTrue(ap.get("ok"), ap)
        self.assertEqual(ap.get("mode"), "apply")
        self.assertEqual(ap.get("kps_created"), 2, ap)
        self.assertGreaterEqual(ap.get("kp_sources_added"), 2, ap)
        names = _kp_names()
        self.assertIn("M0-6b-导入验收-甲", names)
        self.assertIn("M0-6b-导入验收-乙", names)
        n_after = len(names)

        st, ap2 = _post("/api/kp-import/apply", {})
        self.assertEqual(st, 200, f"二次 apply 返回 {st}: {ap2}")
        self.assertEqual(ap2.get("kps_created"), 0, f"二次 apply 应新增 0：{ap2}")
        self.assertGreaterEqual(ap2.get("kps_existing"), 2, ap2)
        self.assertEqual(len(_kp_names()), n_after, "幂等：二次 apply 后数量不变")

    def test_03_doc_id_filter_and_invalid(self):
        """doc_id 过滤 + 非法 doc_id 返回 400 JSON。"""
        st, pv = _get("/api/kp-import/preview", doc_id=999999)
        self.assertEqual(st, 200, f"不存在的 doc_id 应正常返回空统计：{st} {pv}")
        self.assertEqual(pv.get("kps_found"), 0, pv)
        self.assertEqual(pv.get("kps_would_create"), 0, pv)

        st, bad = _get("/api/kp-import/preview", doc_id="abc")
        self.assertEqual(st, 400, f"非法 doc_id 应 400，实际 {st}: {bad}")
        self.assertIn("error", bad)

        st, bad2 = _post("/api/kp-import/apply", {"doc_id": "abc"})
        self.assertEqual(st, 400, f"apply 非法 doc_id 应 400，实际 {st}: {bad2}")
        self.assertIn("error", bad2)

    def test_04_artifacts_dir_not_injectable(self):
        """`artifacts_dir` 参数不被接受：外部路径被忽略，仍只读固定产物目录。"""
        st, pv = _get("/api/kp-import/preview",
                      artifacts_dir="C:\\Windows")
        self.assertEqual(st, 200, f"多余参数不应导致 500/400：{st} {pv}")
        self.assertEqual(pv.get("scanned_files"), 1, "仍只扫固定产物目录里的 1 份")
        art = str(pv.get("artifacts_dir", "")).replace("/", "\\").lower()
        self.assertTrue(art.startswith(str(env.DATA_DIR).replace("/", "\\").lower()),
                        f"外部 artifacts_dir 未被忽略：{art}")

        st, ap = _post("/api/kp-import/apply", {"artifacts_dir": "C:\\Windows"})
        self.assertEqual(st, 200, f"apply 多余参数不应失败：{st} {ap}")
        art2 = str(ap.get("artifacts_dir", "")).replace("/", "\\").lower()
        self.assertTrue(art2.startswith(str(env.DATA_DIR).replace("/", "\\").lower()),
                        f"外部 artifacts_dir 未被忽略：{art2}")


class TestImportUiAndMobile(unittest.TestCase):
    """M0-6b 前端入口 + M0-8 移动端静态回归。"""

    @classmethod
    def setUpClass(cls):
        r = _SRV.get("/", timeout=60)
        assert r.status == 200, f"首页返回 {r.status}"
        cls.page = r.text

    def test_import_button_and_flow(self):
        """知识点页有「导入已整理好的知识点（0 费用）」按钮与 preview→确认→apply 流程。"""
        self.assertIn('id="kp-import-open"', self.page, "缺少导入已整理产物的按钮")
        self.assertIn("导入已整理好的知识点（0 费用）", self.page, "按钮文案丢失")
        self.assertIn("/api/kp-import/preview", self.page, "前端未调用预览接口")
        self.assertIn("/api/kp-import/apply", self.page, "前端未调用导入接口")
        self.assertIn("function kpImportOpen(", self.page)
        self.assertIn("function kpImportApply(", self.page)
        # 与「批量整理」并排的常驻入口（同一张卡里）
        start = self.page.index("function kpBuildCard()")
        card = self.page[start:self.page.index("function bindKpBuild()", start)]
        self.assertIn('id="kp-build-open"', card)
        self.assertIn('id="kp-import-open"', card)
        # 保存确认弹窗里要写明"将新建 N 条 / 不花钱 / 幂等"
        self.assertIn("将新建", self.page)
        self.assertIn("不调用大模型、不联网、不花钱", self.page)
        self.assertIn("已是最新", self.page)

    def test_mobile_table_cards(self):
        """≤560px：表格改卡片式（block/堆叠），长串可断行——不靠横向滚动。"""
        self.assertIn("@media (max-width:560px)", self.page, "缺少窄屏媒体查询")
        media = self.page[self.page.index("@media (max-width:560px)"):]
        for rule in ("#view table{display:block", "#view thead{display:none",
                     "#view tr{display:block", "#view td{display:block"):
            self.assertIn(rule, media, f"窄屏表格卡片化缺规则：{rule}")
        self.assertIn("word-break:break-all", media, "长路径/代码未允许断行")
        self.assertIn("input,select,textarea{max-width:100%}", media,
                      "窄屏输入控件可能顶宽")

    def test_mobile_autofocus_no_scroll(self):
        """问答页自动聚焦用 preventScroll：首屏 scrollY=0（M0-8 第二个失败项）。"""
        self.assertIn("function safeFocus(", self.page, "缺少防滚动聚焦 helper")
        self.assertIn("preventScroll: true", self.page, "focus 未带 preventScroll")
        self.assertIn("if (CHAT.msgs.length === 0) safeFocus(ta);", self.page,
                      "问答页首屏仍用普通 focus()（会把页面滚下去）")
        self.assertNotIn("if (CHAT.msgs.length === 0) ta.focus();", self.page,
                         "旧的会滚屏的 focus() 调用还在")


if __name__ == "__main__":
    unittest.main(verbosity=2)
