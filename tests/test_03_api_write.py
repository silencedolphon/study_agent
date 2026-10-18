"""写操作 API 覆盖：全部只在 `_work/verify/data/` 的临时副本上执行。

包含：
  · 正常写路径：知识点 / 复习评分 / 笔记保存读回更新 / 设置 / 学科切换与课件计划
  · 预览与校验路径：导入、批量入库计划、清理、删除、改名（dry_run）
  · 两个已确认的产品缺陷用 expectedFailure 标注（修好后会变成 UnexpectedSuccess，
    从而在回归里被立刻发现），清单见 tests/matrix.py::KNOWN_BUGS
"""

import json
import sqlite3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import env  # noqa: E402

_SRV = None
_MAIN_DB = None


def setUpModule():
    global _SRV, _MAIN_DB
    env.prepare(clean_data=True)
    _MAIN_DB = env.DATA_DIR / "main.db"
    _SRV = env.Server(tag="write")
    _SRV.start()


def tearDownModule():
    if _SRV is not None:
        _SRV.stop()


def _post(path, payload):
    st, obj = _SRV.post_json(path, payload, timeout=180)
    return st, obj or {}


def _get(path, **params):
    """GET 并按 200 断言，直接返回 JSON 对象。"""
    st, obj = _SRV.get_json(path, timeout=180, **params)
    assert st == 200, f"{path} 返回 {st}: {obj}"
    return obj or {}


def _create_kp(name, **extra):
    st, obj = _post("/api/kp", {"name": name, "summary": "自动化验收", "subject": "验收", **extra})
    assert st == 200 and obj.get("ok"), f"创建知识点失败: {st} {obj}"
    return int(obj["kp_id"])


class TestWriteApi(unittest.TestCase):
    def test_kp_create(self):
        """知识点新建：写入成功、详情可读、同名 upsert 不重复、图谱联动。"""
        kp_id = _create_kp("验收-KP-创建")
        d = _get("/api/kp", id=kp_id)
        self.assertEqual(d["kp"]["name"], "验收-KP-创建")
        self.assertEqual(d["kp"]["reps"], 0)
        # 同名再次创建 → upsert 返回同一个 id
        same = _create_kp("验收-KP-创建")
        self.assertEqual(same, kp_id, "同名知识点应 upsert 而不是新建")
        g = _get("/api/graph")
        self.assertIn(kp_id, {n["id"] for n in g["nodes"]})
        # 列表里能看到
        kps = _get("/api/kps")["knowledge_points"]
        self.assertIn(kp_id, {k["id"] for k in kps})

    def test_review(self):
        """复习评分：SM-2 写回、统计联动、评分后不再进入今日队列。"""
        kp_id = _create_kp("验收-KP-复习")
        stats0 = _get("/api/stats")["knowledge"]
        st, r = _post("/api/review", {"kp_id": kp_id, "grade": 4,
                                      "question": "q", "answer": "a"})
        self.assertEqual(st, 200)
        self.assertTrue(r.get("ok", True))
        self.assertEqual(r["kp_id"], kp_id)
        self.assertEqual(r["reps"], 1)
        self.assertGreaterEqual(r["interval_days"], 1.0)
        self.assertGreaterEqual(r["due_in_days"], 1)
        self.assertIn("mastery", r)
        stats1 = _get("/api/stats")["knowledge"]
        self.assertGreaterEqual(stats1["reviews_today"], stats0["reviews_today"] + 1)
        # 已评分的（due 在未来）不在今日队列
        queue_ids = {q["id"] for q in _get("/api/queue", limit=50)["queue"]}
        self.assertNotIn(kp_id, queue_ids)
        # 未复习的新知识点会进入队列（status=new）
        new_id = _create_kp("验收-KP-队列新点")
        q = _get("/api/queue", limit=50)["queue"]
        item = next((x for x in q if x["id"] == new_id), None)
        self.assertIsNotNone(item, "新建未复习知识点应进入今日队列")
        self.assertEqual(item["status"], "new")

    def test_note_write(self):
        """笔记：保存 → 列表 → 原位更新（按 file 精确写回，落盘内容可核对）。"""
        kp_id = _create_kp("验收-KP-笔记")
        st, saved = _post("/api/note/kp", {
            "kp_id": kp_id, "kp_name": "验收-KP-笔记", "doc_id": 2,
            "body": "# 笔记标题\n\n笔记正文验收内容。",
        })
        self.assertEqual(st, 200)
        self.assertTrue(saved.get("file"))
        file = saved["file"]
        path = Path(saved["path"])
        self.assertEqual(path.resolve().parent, (env.DATA_DIR / "notes").resolve(),
                         "笔记没有落到隔离 notes/ 目录")
        self.assertTrue(path.exists())
        notes = _get("/api/notes")["notes"]
        self.assertIn(file, {n["file"] for n in notes})
        # 按 file 原地更新（接口按文件写回，不依赖按名读取）
        st, upd = _post("/api/note/update", {
            "file": file, "body": "# 改后标题\n\n改后正文。", "mtime": None})
        self.assertEqual(st, 200)
        self.assertEqual(upd["file"], file)
        disk = path.read_text(encoding="utf-8", errors="replace")
        self.assertIn("改后正文", disk)
        self.assertNotIn("笔记正文验收内容", disk)
        # 按名读回（BUG-2 修复后必须精确命中本文件）
        detail = _get("/api/note", name=file)
        self.assertIn("改后正文", detail["content"])
        # 列表里的 chars 应随之变化
        note = next(n for n in _get("/api/notes")["notes"] if n["file"] == file)
        self.assertGreater(note["chars"], 0)

    def test_note_deck(self):
        """课件答疑笔记：question/answer 落盘并出现在笔记列表。"""
        st, saved = _post("/api/note/deck", {
            "doc_id": 2, "question": "什么是药物代谢", "answer": "酶催化的结构改变",
        })
        self.assertEqual(st, 200)
        self.assertTrue(saved.get("file"))
        notes = _get("/api/notes")["notes"]
        self.assertIn(saved["file"], {n["file"] for n in notes})
        self.assertIn("AI答疑", saved["title"])

    def test_note_update_guard(self):
        """笔记更新路径安全：拒绝目录穿越 / 非纯文件名。"""
        for bad in ("../evil.md", "子目录/x.md", "..\\evil.md", ""):
            st, obj = _post("/api/note/update", {"file": bad, "body": "x"})
            self.assertEqual(st, 200)
            self.assertIn("error", obj, f"非法路径 {bad!r} 未被拒绝：{obj}")
            self.assertFalse((env.DATA_DIR / "evil.md").exists())

    def test_settings_write(self):
        """设置保存/重置：只写隔离的 config.local.json。"""
        st, saved = _post("/api/settings", {"values": {"CHAT_MODEL": "deepseek-flash"}})
        self.assertEqual(st, 200)
        self.assertIn("CHAT_MODEL", saved.get("saved", []))
        settings_file = Path(env.DATA_DIR / "config.local.json")
        self.assertTrue(settings_file.exists())
        self.assertIn(str(env.VERIFY_DIR), str(settings_file.resolve()))
        st, reset = _post("/api/settings/reset", {"keys": ["CHAT_MODEL"]})
        self.assertEqual(st, 200)
        self.assertTrue(reset.get("ok"))

    def test_import_preview(self):
        """按路径导入预览：只算不搬，目标落在隔离 raw/ 下。"""
        src = env.DATA_DIR / "ingest_src"
        st, d = _post("/api/import", {"src": str(src), "sub": "导入子目录",
                                      "recursive": True, "dry_run": True})
        self.assertEqual(st, 200)
        self.assertTrue(d["dry_run"])
        self.assertEqual(d["count"], 1)
        self.assertTrue(d["files"][0]["supported"])
        self.assertIn(str(env.DATA_DIR / "raw"), str(d["dest"]))
        # dry_run 不得真的复制文件
        self.assertFalse((env.DATA_DIR / "raw" / "导入子目录" / "小样.md").exists())

    def test_ingest_batch_plan(self):
        """批量入库计划：识别待入库文件与成本字段。"""
        target = env.DATA_DIR / "raw" / "验收样例.md"
        st, d = _post("/api/ingest/batch/plan", {"paths": [str(target)]})
        self.assertEqual(st, 200)
        self.assertEqual(d["n_files"], 1)
        self.assertEqual(d["n_todo"], 1)
        self.assertEqual(d["n_unsupported"], 0)
        self.assertIn("est_cost_cny", d)

    def test_cleanup_preview(self):
        """清理孤儿数据（dry_run）：只返回统计不动库。"""
        st, d = _post("/api/cleanup", {"dry_run": True})
        self.assertEqual(st, 200)
        for key in ("orphan_kps", "deleted_kps", "reviews", "edges", "documents"):
            self.assertIn(key, d)
        self.assertEqual(d["documents"], 3)

    def test_doc_delete_preview(self):
        """单份删除影响预览：报告切块数与孤儿处理，且不真删。"""
        st, d = _post("/api/doc/delete", {"doc_id": 2, "dry_run": True,
                                          "purge_orphans": True})
        self.assertEqual(st, 200)
        self.assertTrue(d["dry_run"])
        self.assertEqual(d["deleted_doc_id"], 2)
        self.assertGreater(d["chunks_removed"], 0)
        self.assertIn("orphan_preview", d)
        docs = _get("/api/docs")["documents"]
        self.assertEqual(len(docs), 3, "dry_run 不应真的删除文档")

    def test_doc_rename_preview(self):
        """文档标题改名预览：只改标题，不动文件，dry_run 不落库。"""
        st, d = _post("/api/doc/rename", {"doc_id": 2, "title": "第七章 药物排泄-预览改名",
                                          "dry_run": True})
        self.assertEqual(st, 200)
        self.assertTrue(d["ok"])
        self.assertTrue(d["dry_run"])
        self.assertEqual(d["title"]["to"], "第七章 药物排泄-预览改名")
        cur = _get("/api/doc", id=2)
        self.assertEqual(cur["document"]["title"], "第七章 药物排泄")

    def test_subject_switch_deck(self):
        """学科切换 + 课件月历 + 入计划/标记（全部落在沙箱学科 fixture）。"""
        st, d = _post("/api/subjects/switch", {"slug": "验收-药物化学"})
        self.assertEqual(st, 200)
        self.assertEqual(d["current"], "验收-药物化学")
        self.assertIn(str(env.SANDBOX_DIR), d["paths"]["DB_PATH"])
        cal = _get("/api/deck-calendar", ym="2026-10")
        self.assertGreaterEqual(cal["stats"]["plans"], 1)
        self.assertTrue(cal["days"], "fixture 学科应有真实课件复习计划")
        # 入计划：给 doc 1 建固定间隔复习
        st, en = _post("/api/deck-enroll", {"doc_id": 1})
        self.assertEqual(st, 200)
        self.assertTrue(en["ok"])
        self.assertEqual(en["doc_id"], 1)
        self.assertEqual(len(en["days"]), 5)
        # 标记某天完成
        first = cal["days"][sorted(cal["days"])[0]][0]
        st, mk = _post("/api/deck-mark", {"plan_id": first["plan_id"],
                                          "round": first["round"], "state": "done"})
        self.assertEqual(st, 200)
        self.assertTrue(mk["ok"])
        self.assertEqual(mk["state"], "done")
        # 切回默认学科
        st, back = _post("/api/subjects/switch", {"slug": "default"})
        self.assertEqual(st, 200)
        self.assertEqual(back["current"], "default")

    def test_subject_create_rename(self):
        """新建学科（空 schema 库）→ 列表可见 → 重命名 → 切回默认。"""
        st, d = _post("/api/subjects/create", {"name": "验收新建学科"})
        self.assertEqual(st, 200)
        self.assertTrue(d["ok"])
        self.assertTrue(d["current"].startswith("验收新建学科"))
        slug = d["current"]
        newdb = Path(d["paths"]["DB_PATH"])
        self.assertTrue(newdb.exists(), "新学科应创建空 schema 库")
        self.assertIn(str(env.SANDBOX_DIR), str(newdb))
        st, rn = _post("/api/subjects/rename", {"slug": slug, "name": "验收新建学科-改名"})
        self.assertEqual(st, 200)
        listing = _get("/api/subjects")
        names = {s["name"] for s in listing["subjects"]}
        self.assertIn("验收新建学科-改名", names)
        st, back = _post("/api/subjects/switch", {"slug": "default"})
        self.assertEqual(back["current"], "default")

    # ---------------- 回归用例：曾是 BUG-1/2/3，已由产品修复（ec394bb）转正 ----------------
    def test_docs_delete_preview(self):
        """批量删除预览（修复回归）：200 + dry_run 报告，且预览不真删。"""
        st, d = _post("/api/docs/delete", {"doc_ids": [2], "dry_run": True})
        self.assertEqual(st, 200, f"批量删除预览返回 {st}: {d.get('error')}")
        self.assertTrue(d.get("dry_run"))
        self.assertEqual(d["n_docs"], 1)
        self.assertEqual(d["documents"][0]["doc_id"], 2)
        self.assertGreater(d["documents"][0]["chunks_removed"], 0)
        # 沙箱里没有真实 logs/ 产物，所以 artifacts_total 可以是 0；字段结构必须齐全
        self.assertIn("artifacts_total", d)
        self.assertIn("artifacts_bytes_total", d)
        # 预览不应真删
        self.assertEqual(len(_get("/api/docs")["documents"]), 3)

    def test_rename_raw_preview(self):
        """raw 文件重命名预览（修复回归）：200 + 改名计划，且预览不动磁盘。"""
        st, d = _post("/api/rename/raw", {"rel": "验收样例.md",
                                          "new_name": "验收样例-改名.md",
                                          "dry_run": True})
        self.assertEqual(st, 200, f"重命名预览返回 {st}: {d.get('error')}")
        self.assertTrue(d.get("ok"), d)
        self.assertTrue(d.get("dry_run"))
        self.assertTrue((env.DATA_DIR / "raw" / "验收样例.md").exists(),
                        "dry_run 不应真的移动文件")
        self.assertFalse((env.DATA_DIR / "raw" / "验收样例-改名.md").exists())

    def test_note_lookup_by_name(self):
        """按名读取笔记（修复回归）：name 命中哪篇就返回哪篇。"""
        kp_id = _create_kp("验收-KP-命中错位")
        _post("/api/note/kp", {"kp_id": kp_id, "kp_name": "验收-KP-命中错位",
                               "doc_id": 2, "body": "# A\n\n第一篇笔记内容。"})
        _post("/api/note/deck", {"doc_id": 2, "question": "问题B",
                                 "answer": "第二篇笔记内容。"})
        names = [n["file"] for n in _get("/api/notes")["notes"]]
        self.assertGreaterEqual(len(names), 2, f"至少要有两篇笔记才能验证：{names}")
        for target in names[:2]:
            detail = _get("/api/note", name=target)
            self.assertIn("note", detail, f"按名读笔记失败：{detail.get('error')}")
            self.assertEqual(detail["note"]["file"], target,
                             f"请求 {target} 却返回了 {detail['note']['file']}")
        # 不存在的名字必须是明确的 error，而不是随便返回一篇
        missing = _get("/api/note", name="不存在的笔记-xyz.md")
        self.assertIn("error", missing)

    def test_bad_query_params_robust(self):
        """非法数字查询参数（修复回归）：不再 500，回落默认值；非法学科 slug 返回 400。"""
        for path, params in (
            ("/api/doc", {"id": "abc"}),
            ("/api/chunk", {"id": "abc"}),
            ("/api/search", {"q": "x", "k": "abc"}),
            ("/api/kp", {"id": "abc"}),
            ("/api/queue", {"limit": "abc"}),
        ):
            st, obj = _SRV.get_json(path, timeout=60, **params)
            self.assertEqual(st, 200, f"{path} {params} 返回 {st}（应取默认值而不是 500）")
            self.assertIsInstance(obj, dict, f"{path} {params} 返回不是 JSON 对象")
            self.assertNotIn("ValueError", str(obj.get("error", "")), f"{path} 仍抛 ValueError")
        # 搜索/队列在默认值下应给出可用结构
        st, s = _SRV.get_json("/api/search", timeout=60, q="x", k="abc")
        self.assertIn("hits", s)
        st, q = _SRV.get_json("/api/queue", timeout=60, limit="abc")
        self.assertIn("queue", q)
        # 非法学科 slug：400 + JSON error（不能 500 / 断连接）
        st, obj = _SRV.post_json("/api/subjects/switch", {"slug": "不存在的学科XYZ"})
        self.assertEqual(st, 400, f"非法学科 slug 应 400，实际 {st}: {obj}")
        self.assertIn("error", obj)


if __name__ == "__main__":
    unittest.main(verbosity=2)

