# -*- coding: utf-8 -*-
"""C-0052 自测（临时脚本，验完即删）：笔记 front-matter 扩展的读写与兼容。"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core import config, db, study  # noqa: E402

tmp = Path(tempfile.mkdtemp(prefix="c0052_notes_"))
config.NOTES_DIR = tmp
conn = db.connect()
db.init_db(conn)

p1 = study.save_note(
    conn, "教学纲要 · 知识点笔记", "第一条正文", tags=["c0052"],
    extra={"origin": "knowledge_point", "kp_id": "LN-07", "doc_id": 5,
           "source_label": "5 第五章 脂环烃 · 知识点 LN-07",
           "question": "提问里有冒号: 和 空格", "nope": None})
p2 = study.save_note(conn, "教学纲要 · 知识点笔记", "第二条正文", unique=True)
p3 = study.save_note(conn, "旧式笔记", "没有 extra 的老写法")

print("P1", Path(p1).name)
print("P2", Path(p2).name)
print("P3", Path(p3).name)
assert Path(p1).name != Path(p2).name, "unique 没有生效"
assert Path(p3).name == "旧式笔记.md"
print("\n--- p1 内容 ---")
print(Path(p1).read_text(encoding="utf-8"))

notes = {n["file"]: n for n in study.list_notes()}
n1 = notes[Path(p1).name]
assert n1["kp_id"] == "LN-07" and n1["doc_id"] == 5
assert n1["origin"] == "knowledge_point"
assert n1["source_label"] == "5 第五章 脂环烃 · 知识点 LN-07"
assert n1["question"] == "提问里有冒号: 和 空格"
assert n1["created"] > 1e9 and n1["title"] == "教学纲要 · 知识点笔记"
n3 = notes[Path(p3).name]
assert n3["kp_id"] == "" and n3["doc_id"] == 0 and n3["origin"] == ""
assert n3["title"] == "旧式笔记" and n3["chars"] > 0
print("list_notes 解析 OK：", {k: n1[k] for k in
                               ("title", "kp_id", "doc_id", "source_label", "created")})

hit = study.notes_for(kp_id="LN-07")
assert [h["file"] for h in hit] == [Path(p1).name], hit
assert [h["file"] for h in study.notes_for(doc_id=5)] == [Path(p1).name]
assert study.notes_for() == []
print("notes_for OK：kp=1 条 / doc=2 条 / 无条件=0 条")

# 兼容旧笔记（根 notes/ 里的真实文件）：逐条解析都不许抛
old = Path(ROOT / "notes")
if old.exists():
    config.NOTES_DIR = old
    got = study.list_notes()
    assert len(got) == len(list(old.glob("*.md")))
    print(f"旧目录兼容 OK：{len(got)} 篇，样例 "
          f"{[(g['title'], g['origin'], g['kp_id']) for g in got[:2]]}")
conn.close()
print("\n全部通过")
