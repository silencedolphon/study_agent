# Copyright (c) 2026 潜声海豚 <2227663973@qq.com>. All rights reserved.
# 本源码为著作权人私有作品（proprietary）：仅供经书面授权的测试使用；
# 未经著作权人许可，不得复制、分发、公开、发布或用于任何商业用途。
# Unauthorized copying, distribution or publication is strictly prohibited.
# File-ID: SA-18-889041ac   (project study_agent; keep this line)
"""学习层：知识点、掌握度、间隔重复调度、错题本。

调度算法用 SM-2（Anki 早期算法）而非 FSRS：SM-2 实现简单、行为可预测、易调试，
对个人学习助手足够；FSRS 需要额外参数拟合，收益不成比例。
**注意**：SM-2 只服务**知识点级**调度（`schedule()`）。卡 C-0061 起的**课件级**
遗忘曲线另有一套固定间隔表（见下方"课件级遗忘曲线"一节），两者并存、互不写入。

掌握度 mastery (0..1) 由两个信号合成：
- 复习间隔（间隔越长说明记得越牢）取 log 压缩
- 最近表现（lapses 惩罚、reps 增益）
这样"复习过的、间隔拉长的"点自然浮现为高掌握度。
"""
from __future__ import annotations

import json
import math
import os
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

from . import config, db

DAY = 86400.0
# 判定"已掌握"的阈值
MASTERED_THRESHOLD = 0.75


# ---------------- 知识点 ----------------
def upsert_kp(conn: sqlite3.Connection, name: str, summary: str = "",
              subject: str = "", chunk_ids: list[int] | None = None) -> int:
    now = db.now()
    row = conn.execute("SELECT id FROM knowledge_points WHERE name=?", (name,)).fetchone()
    if row:
        kp_id = int(row["id"])
        conn.execute(
            """UPDATE knowledge_points SET summary=COALESCE(NULLIF(?,''), summary),
               subject=COALESCE(NULLIF(?,''), subject), updated_at=? WHERE id=?""",
            (summary, subject, now, kp_id),
        )
    else:
        cur = conn.execute(
            """INSERT INTO knowledge_points(name, summary, subject, created_at, updated_at)
               VALUES(?,?,?,?,?)""",
            (name, summary, subject, now, now),
        )
        kp_id = int(cur.lastrowid or 0)
    # 出处关联：结论必须可追溯（`kp_sources` 是知识点 → 切块的唯一桥梁）。
    # [重建] 原函数体在 UPDATE 之后被截断，以下按表结构与"出处一等公民"的设计补全。
    # 只**新增**关联，不删既有——重复调用 upsert 不该把人工指定的出处抹掉。
    for cid in (chunk_ids or []):
        try:
            conn.execute(
                "INSERT OR IGNORE INTO kp_sources(kp_id, chunk_id, note) VALUES(?,?,?)",
                (kp_id, int(cid), ""),
            )
        except (sqlite3.Error, TypeError, ValueError):
            continue          # 单个坏 id 不该让整次 upsert 失败
    if chunk_ids:
        conn.execute("UPDATE knowledge_points SET had_sources=1 WHERE id=?", (kp_id,))
    conn.commit()
    return kp_id


def link_kp(conn: sqlite3.Connection, src_name: str, dst_name: str,
            kind: str = "prereq") -> int:
    """建前置依赖边（`SKILL.md`: `kp link "链式法则" "梯度消失"`）。

    [重建] 原文件里没有这个函数名可考（CLI 的 `kp link` 走 `cmd_kp`），
    但 `kp_edges(src_id,dst_id,kind)` 表存在且注释写明用途 ⇒ 按表语义补。
    两端知识点不存在时先 upsert 出来（名字即标识），返回 dst 的 id。
    """
    a = upsert_kp(conn, src_name)
    b = upsert_kp(conn, dst_name)
    conn.execute(
        "INSERT OR IGNORE INTO kp_edges(src_id, dst_id, kind) VALUES(?,?,?)",
        (a, b, kind or "prereq"),
    )
    conn.commit()
    return b

def mistake_stats(conn: sqlite3.Connection, include_resolved: bool = False) -> dict:
    where = "" if include_resolved else "WHERE resolved=0"
    rows = conn.execute(
        f"""SELECT COALESCE(NULLIF(reason,''),'未分类') AS reason, COUNT(*) AS n
            FROM mistakes {where} GROUP BY reason ORDER BY n DESC"""
    ).fetchall()
    total = conn.execute(f"SELECT COUNT(*) AS n FROM mistakes {where}").fetchone()["n"]
    open_n = conn.execute("SELECT COUNT(*) AS n FROM mistakes WHERE resolved=0").fetchone()["n"]
    # 反复出错的知识点
    hot = conn.execute(
        """SELECT k.name, COUNT(*) AS n FROM mistakes m
           JOIN knowledge_points k ON k.id=m.kp_id
           WHERE m.resolved=0 GROUP BY k.id HAVING n>=2 ORDER BY n DESC LIMIT 10"""
    ).fetchall()
    return {
        "total": int(total),
        "open": int(open_n),
        "by_reason": [{"reason": r["reason"], "n": int(r["n"])} for r in rows],
        "repeated_kps": [{"name": r["name"], "n": int(r["n"])} for r in hot],
    }


# ---------------- 笔记回写（抗上下文刷新的关键机制） ----------------
# 笔记格式（卡 C-0052 起）＝ 既有的 YAML front-matter **加几个可选键**：
#
#   ---
#   title: 教学纲要 · 知识点笔记        # 既有
#   kind: note                          # 既有（note/summary/mistake/cheatsheet）
#   created: 1758888888                 # 既有（unix 秒）
#   updated: 1758888888                 # 既有
#   tags: ["..."]                       # 既有（JSON 数组）
#   origin: knowledge_point             # 新增：knowledge_point | deck_chat
#   kp_id: LN-07                        # 新增：绑定的知识点 id（可空）
#   doc_id: 5                           # 新增：来源课件 id（可空）
#   source_label: 5 第五章… · 知识点 LN-07   # 新增：人可读的来源
#   question: 这份课件的重点是什么        # 新增：仅课件问答，记下问的是什么
#   ---
#
# 为什么扩展 front-matter 而不是新建库表：正文本来就只落在文件里（见本函数头），
# "这条笔记从哪来"是正文的**附属信息**，两处存必然不同步；而且 MCP 的
# kb_note_save/kb_note_list/kb_note_read 与看板读的是同一对函数，加键即两边同时生效。
# 旧笔记没有这些键 → `list_notes()` 里给空值，页面按"无来源"渲染，不会报错。
def _fm_scalar(v) -> str:
    """front-matter 的一行值：必须是**单行**标量。

    换行会破坏"一行一个键"的约定（读回来时后面几行会被当成新键），
    所以这里把任意值压成单行——宁可少一个换行，也不能写坏自己的格式。
    """
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float):
        v = f"{v:.0f}" if v.is_integer() else repr(v)
    return " ".join(str(v).split())


def save_note(conn: sqlite3.Connection, title: str, body: str, tags: list[str] | None = None,
              source_refs: list[str] | None = None, kind: str = "note",
              extra: dict | None = None, unique: bool = False) -> str:
    """把结论落成 notes/ 下的 markdown 文件。

    这是本系统对抗上下文刷新的核心手段：向量检索是有损的、不可解释的，
    而一份写下来的笔记是无损的、人可读的、下次直接读就行。

    `extra`：附加到 front-matter 的键值（卡 C-0052 用它记来源：origin/kp_id/
    doc_id/source_label）。值是 None 或空串的键**不写**——写了空键会让
    "这条笔记没有来源"与"来源是空的"两种状态分不清。
    `unique`：文件名撞了就往后排（`-2`、`-3`…）。默认 False＝保持既有行为
    （同名即覆写，MCP/CLI 的旧用法不变）；知识点笔记与课件问答是"一次次记"，
    覆写掉上一条等于把人写的东西弄丢了，所以那两处显式传 True。
    """
    config.ensure_dirs()
    slug = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in title)[:60]
    slug = slug.strip("-") or "note"
    path = config.NOTES_DIR / f"{slug}.md"
    if unique:
        i = 2
        while path.exists() and i < 1000:
            path = config.NOTES_DIR / f"{slug}-{i}.md"
            i += 1
    now = db.now()
    lines = [
        "---",
        f"title: {_fm_scalar(title) or '未命名'}",
        f"kind: {_fm_scalar(kind) or 'note'}",
        f"created: {now:.0f}",
        f"updated: {now:.0f}",
        f"tags: {json.dumps(tags or [], ensure_ascii=False)}",
    ]
    for k, v in (extra or {}).items():
        if v is None or v == "":
            continue
        s = _fm_scalar(v)
        if s:
            lines.append(f"{k}: {s}")
    lines += [
        "---",
        "",
        f"# {_fm_scalar(title) or '未命名'}",
        "",
        body.strip(),
        "",
    ]
    if source_refs:
        lines.append("## 出处")
        lines.append("")
        for r in source_refs:
            lines.append(f"- {_fm_scalar(r)}")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    return str(path)


def parse_note_meta(text: str) -> dict:
    """读笔记的 front-matter（只认最外层"一行一个 `键: 值`"）。

    刻意不引 PyYAML：这份 front-matter 是 `save_note` 自己写的，结构固定；
    多一个依赖就多一处离线环境装不上的风险（本项目离线优先）。
    值里可以带冒号（`source_label: 课件 · 知识点 LN-07`），按**第一个**冒号切。
    """
    head = (text or "").lstrip("\ufeff \t\r\n")
    if not head.startswith("---"):
        return {}
    end = head.find("\n---", 3)
    if end < 0:
        return {}
    meta: dict[str, str] = {}
    for line in head[3:end].splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        k, v = line.split(":", 1)
        meta[k.strip()] = v.strip()
    return meta


def _as_num(v, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _fm_list(v) -> list[str]:
    """front-matter 里的 JSON 数组（`tags` 就是它）。

    写的时候是 `json.dumps(tags)`（既有行为，不改），读回来就按 JSON 解；
    手改坏的、写成纯文本的一律当空表——读笔记不该因为一行 tags 就整条失败。
    """
    try:
        data = json.loads(v) if v else []
    except (TypeError, ValueError):
        return []
    return [str(x) for x in data] if isinstance(data, list) else []


# ---------------- 笔记：只取"人自己写的那部分" / 只换正文地改（卡 C-0082） ----------------
# 人拍板：「查看笔记……我现在想**只出现我自己写进去的内容**（去掉 front-matter、
# 自动标题、「## 出处」）；同时**可以在知识点窗口修改笔记的内容**」。
#
# 下面这一组就是为这两件事写的，规则一律**反过来照着 `save_note` 的写法读**，
# 不去猜内容：`save_note` 写出来的一定是
#   `---` + fm + `---` + 空行 + `# <title>` + 空行 + 正文 + [空行 + `## 出处` + 列表]
# 所以"机器加的壳"能被精确认出来，而人自己写的东西（含人自己写的同名标题）
# 不会被误伤。


def _line_spans(s: str) -> list[tuple[int, int, str]]:
    """按 `\\n` 切行，并给出每行在 `s` 里的**下标**（卡 C-0082）。

    留着下标才能"只换中间那一段"——front-matter 与「## 出处」段原样搬过去。
    刻意不用 `splitlines()`：它会吃掉行尾符，拿它重拼一次就会把整篇笔记的
    行尾风格换掉（notes/ 下的文件是 CRLF，见 `_note_rebuild`）。
    """
    out: list[tuple[int, int, str]] = []
    i = 0
    while True:
        j = s.find("\n", i)
        if j < 0:
            out.append((i, len(s), s[i:]))
            return out
        out.append((i, j, s[i:j]))
        i = j + 1


def _note_split(rest: str, title: str) -> tuple[int, int, str]:
    """在 front-matter **之后**的文本里划出正文区：返回 (起点, 终点, 正文)。

    起点：跳过前导空行，再跳过"与 fm.title 一模一样的那行一级标题"及其后的
    空行之后（这一段是"不动的部分"，回写时原样带回去）；
    终点：最后一个自动追加的「## 出处」段之前（找不到就是文件尾）。
    正文本身只用于显示，两个下标用于"只替换这一段"（见 `update_note_body`）。
    """
    spans = _line_spans(rest)
    n = len(spans)
    k = 0
    while k < n and not spans[k][2].strip():
        k += 1
    if title and k < n and spans[k][2].strip() == f"# {title}":
        # 标题认下来之后**再跳一次空行**：`rest[:start]` 要连标题后的那个空行
        # 一起留给"不动的部分"，否则回写时 `# 标题` 会与正文贴在一起（卡 C-0082）。
        k += 1
        while k < n and not spans[k][2].strip():
            k += 1
    start = spans[k][0] if k < n else len(rest)
    end = len(rest)
    for j in range(n - 1, -1, -1):
        if spans[j][2].strip() != "## 出处":
            continue
        # 只认"后面到文件尾全是空行或 `- ` 行"的那一个——这正是 save_note 追加
        # 出来的形状。人自己在正文中间写的同名标题后面还接着别的内容，不算。
        if all((not spans[t][2].strip())
               or spans[t][2].lstrip().startswith("- ")
               for t in range(j + 1, n)):
            end = spans[j][0]
        break
    if end < start:      # 病态输入（比如标题落在出处段之后）：不越界，退成空正文
        end = start
    return start, end, rest[start:end].strip()


def note_body(text: str) -> str:
    """笔记里**只有人自己写进去的那部分**（卡 C-0082）。

    剥掉三样"机器加的壳"：front-matter、自动生成的一级标题（`# <title>`，
    **只有与 fm.title 逐字相同时才认**，人自己改过标题就留着不删）、末尾
    自动追加的「## 出处」段。边界情形一律**保守**：
      · 没有 front-matter 的老笔记 = **整篇原文照旧**，一个字都不动
        （老文件不该因为这条新规则被截掉什么）；
      · 认不出 fm 边界（`---` 不闭合）同样回落原文；
      · 「## 出处」只认最后一个、且它后面到文件尾只有空行或 `- ` 行的那个。
    """
    raw = text or ""
    meta = parse_note_meta(raw)
    if not meta:
        return raw
    head = raw.lstrip("\ufeff \t\r\n")
    end = head.find("\n---", 3)
    if end < 0:
        return raw
    return _note_split(head[end + 4:], str(meta.get("title") or "").strip())[2]


# ---------------- 掌握度与间隔重复（SM-2） ----------------
# [重建] 本段整段丢失（恢复版从 `_fm_touch_updated` 直接跳到 `list_notes`）。
# 依据：模块 docstring 写明"掌握度由间隔的 log 压缩 + 最近表现合成"；
# SM-2 语义在 SKILL.md 里有原文（0-2 重置为 1 天 / 3 勉强 / 4 正确 / 5 轻松；
# 间隔 1 天 → 6 天 → 按 ease 倍数增长）。以下按此实现。
def compute_mastery(kp) -> float:
    """知识点掌握度 0..1：间隔越长越牢，lapses 惩罚、reps 增益。"""
    days = float(kp["interval_days"] or 0.0)
    reps = int(kp["reps"] or 0)
    lapses = int(kp["lapses"] or 0)
    # 间隔项：log 压缩，30 天≈0.59、180 天≈0.72（再长收益递减）
    s = math.log1p(max(days, 0.0)) / math.log1p(180.0)
    # 熟练度项：复习次数越多越稳，8 次封顶
    r = min(reps / 8.0, 1.0)
    v = 0.6 * s + 0.4 * r
    # 遗忘惩罚：每次 lapses 打 12%，最多打掉一半
    v *= max(0.5, 1.0 - 0.12 * lapses)
    return round(min(1.0, max(0.0, v)), 4)


def schedule(conn: sqlite3.Connection, kp_id: int, grade: int) -> dict:
    """SM-2：按评分更新间隔/难度/到期时间。返回新状态（供 CLI 打印）。"""
    row = conn.execute("SELECT * FROM knowledge_points WHERE id=?", (int(kp_id),)).fetchone()
    if not row:
        raise ValueError(f"知识点不存在: {kp_id}")
    g = int(max(0, min(5, int(grade))))
    ease = float(row["ease"] or 2.5)
    reps = int(row["reps"] or 0)
    lapses = int(row["lapses"] or 0)
    interval = float(row["interval_days"] or 0.0)

    if g < 3:
        # 没通过：回到 1 天，并记一次遗忘（掌握度会因此下降）
        lapses += 1
        interval = 1.0
        reps += 1
    else:
        if reps == 0:
            interval = 1.0
        elif reps == 1:
            interval = 6.0
        else:
            interval = max(1.0, round(interval * ease, 1))
        reps += 1
    # ease 自身也要随表现漂移（SM-2 原式），下限 1.3 防"越练越难"
    ease = max(1.3, ease + (0.1 - (5 - g) * (0.08 + (5 - g) * 0.02)))
    due = db.now() + interval * DAY
    conn.execute(
        """UPDATE knowledge_points SET ease=?, interval_days=?, reps=?, lapses=?,
           due_at=?, last_review=?, updated_at=? WHERE id=?""",
        (ease, interval, reps, lapses, due, db.now(), db.now(), int(kp_id)),
    )
    return {"kp_id": int(kp_id), "grade": g, "interval_days": interval,
            "ease": round(ease, 3), "reps": reps, "lapses": lapses, "due_at": due}


def review_queue(conn: sqlite3.Connection, limit: int = 20, subject: str = "") -> list[dict]:
    """今日到期队列（只取**复习过**且到期的：新知识点不进队列，先学再复习）。"""
    now = db.now()
    sql = ("SELECT * FROM knowledge_points "
           "WHERE due_at<=? AND last_review IS NOT NULL")
    args: list = [now]
    if subject:
        sql += " AND subject=?"
        args.append(subject)
    sql += " ORDER BY due_at LIMIT ?"
    args.append(int(limit))
    out = []
    for r in conn.execute(sql, args).fetchall():
        out.append({
            "id": int(r["id"]), "name": r["name"], "subject": r["subject"] or "",
            "mastery": compute_mastery(r),
            "interval_days": float(r["interval_days"] or 0),
            "due_at": r["due_at"], "reps": int(r["reps"] or 0),
            "lapses": int(r["lapses"] or 0),
        })
    return out


def due_queue(conn: sqlite3.Connection, limit: int = 20, include_new: bool = True,
              subject: str = "") -> list[dict]:
    """到期复习队列：先到期的优先，新知识点（从未复习）排在到期项之后。

    [C-0107 真值回填] 本函数在重建的 `core/study.py` 里**整个丢了**，同一位置
    只剩一个 `review_queue(conn, limit, subject)`（少 `include_new` 参数、
    且不含新知识点那一段）。字节码真值
    `_work/strings/study.funcs.txt::L156` 明确写着：

        due_queue   args=4   vars: conn, limit, include_new, subject, now,
                                    params, sub, rows, r, out, d, rest

    ⇒ 函数名与参数个数**以 core 侧真值为准**（不是 dashboard 调错名字），
    所以修法是补回本函数，而不是给 `review_queue` 起别名。
    SQL 与返回键名逐条取自真值常量表 `study.strings.txt` 中标 `[due_queue]`
    的常量（"SELECT * FROM knowledge_points\\n  WHERE due_at IS NOT NULL AND
    due_at <= ?"、"last_review IS NULL AND due_at IS NULL"、
    "SELECT * FROM knowledge_points WHERE "、" ORDER BY created_at LIMIT ?"、
    "\\n  ORDER BY due_at ASC LIMIT ?"、" AND subject=?"，键名
    overdue_days / mastery / due_at / status / "due" / "new"）。
    """
    now = db.now()
    sub = " AND subject=?" if subject else ""
    out: list[dict] = []

    # ① 到期的（复习过、且到点）
    params: list = [now]
    if subject:
        params.append(subject)
    params.append(max(0, int(limit)))
    rows = conn.execute(
        "SELECT * FROM knowledge_points\n            WHERE due_at IS NOT NULL AND due_at <= ?"
        + sub + "\n            ORDER BY due_at ASC LIMIT ?",
        params).fetchall()
    for r in rows:
        out.append({
            "id": int(r["id"]), "name": r["name"], "subject": r["subject"] or "",
            "mastery": compute_mastery(r),
            "interval_days": float(r["interval_days"] or 0),
            "due_at": r["due_at"], "reps": int(r["reps"] or 0),
            "lapses": int(r["lapses"] or 0),
            "status": "due",
            "overdue_days": max(0, int((now - float(r["due_at"] or now)) // DAY)),
        })

    # ② 新知识点（从未复习）排在到期项**之后**，只补满剩余额度
    rest = max(0, int(limit) - len(out))
    if include_new and rest > 0:
        p2: list = []
        if subject:
            p2.append(subject)
        p2.append(rest)
        rows2 = conn.execute(
            "SELECT * FROM knowledge_points WHERE "
            + "last_review IS NULL AND due_at IS NULL" + sub
            + " ORDER BY created_at LIMIT ?",
            p2).fetchall()
        for r in rows2:
            out.append({
                "id": int(r["id"]), "name": r["name"], "subject": r["subject"] or "",
                "mastery": compute_mastery(r),
                "interval_days": float(r["interval_days"] or 0),
                "due_at": r["due_at"], "reps": int(r["reps"] or 0),
                "lapses": int(r["lapses"] or 0),
                "status": "new",
                "overdue_days": 0,
            })
    return out


def record_review(conn: sqlite3.Connection, kp_id: int, grade: int,
                  question: str = "", answer: str = "", note: str = "",
                  elapsed_ms: int = 0) -> dict:
    """记一次复习：写 `reviews` 证据链 + 调 `schedule` 更新状态。"""
    st = schedule(conn, kp_id, grade)
    conn.execute(
        """INSERT INTO reviews(kp_id, grade, correct, question, answer, user_note,
                               elapsed_ms, ts) VALUES(?,?,?,?,?,?,?,?)""",
        (int(kp_id), st["grade"], 1 if st["grade"] >= 3 else 0,
         question or "", answer or "", note or "", int(elapsed_ms or 0), db.now()),
    )
    conn.commit()
    return st


# ---------------- 课件级遗忘曲线（卡 C-0061） ----------------
# 与知识点级的 SM-2 是**两套账、互不写入**（见 `db.DECK_SCHEMA` 的说明）。
# 固定间隔表：1 / 3 / 7 / 16 / 35 天，共 5 轮——这是用户拍板的节奏。
DECK_INTERVALS = (1, 3, 7, 16, 35)
# [重建] 以下三个成绩常量的**数值**仍是推断的（原文件整段丢失）；
# 但它们的**函数名**已经不是推断：卡 C-0108 用字节码真值把本节的
# `_deck_today` / `_deck_ensure` / `_deck_load` / `_deck_days` / `_deck_summary` /
# `enroll_deck` / `deck_mark` 逐条补回（见下方各函数 docstring 里的 [C-0108 真值回填]），
# 函数名与参数个数逐字取自 `_work/strings/study.funcs.txt`。
DECK_GRADE_WRONG = 0
DECK_GRADE_VAGUE = 1
DECK_GRADE_RIGHT = 2


def deck_curve(intervals=DECK_INTERVALS, start: float | None = None) -> list[str]:
    """按固定间隔表算出 5 个日历日（`YYYY-MM-DD`），供月历标注。

    [重建] 依据 `db.DECK_SCHEMA` 的注释：`due_day` 存 TEXT 日历日，
    `intervals` 存 JSON 快照（"计划按录入当时那套间隔走"）。
    """
    base = datetime.fromtimestamp(start if start is not None else db.now()).date()
    return [(base + timedelta(days=int(d))).isoformat() for d in intervals]


def deck_calendar(conn: sqlite3.Connection, ym: str = "", today=None) -> dict:
    """某个月的课件复习日程（月历的数据源，只读）。

    返回 `{"ym", "days": {"YYYY-MM-DD": [项…]}, "stats": {...}}`。
    每项带 `overdue`（逾期天数，按**日历日**相减；仅 pending 才算）。
    表不存在（旧学科库）时返回空月历而不是抛错——月历显示为空，页签不该 500。

    [C-0107 真值回填] 重建版把本函数丢成了 `deck_curve()`（只算 5 个日期的
    工具函数），而 dashboard 调的是 `study.deck_calendar(conn, ym)`。字节码
    真值 `study.funcs.txt::L339` 写着：

        deck_calendar  args=3  vars: conn, ym, today, d0, y, m, parts, yy, mo,
                                     first, nxt, today_s

    ⇒ 真值在 **core 侧**（不是 dashboard 调错名），所以补回本函数；
    `deck_curve()` 是另一件事（间隔表 → 5 个日期），保留不动。
    返回结构与三条 COUNT 查询、日期格式宽度（"%04d-%02d"）全部取自真值常量表
    `study.strings.txt` 中标 `[deck_calendar]` 的常量。
    """
    # today 归一化：None / date / datetime / "YYYY-MM-DD" 都接受
    if today is None or today == "":
        d_today = date.fromtimestamp(db.now())
    elif isinstance(today, datetime):
        d_today = today.date()
    elif isinstance(today, date):
        d_today = today
    else:
        d_today = date.fromisoformat(str(today)[:10])
    today_s = d_today.isoformat()

    # ym 归一化：空或非法都退回 today 所在月
    parts = str(ym or "").split("-")
    yy = mo = 0
    if len(parts) >= 2 and parts[0].strip().isdigit() and parts[1].strip().isdigit():
        yy, mo = int(parts[0]), int(parts[1])
    if not (1 <= mo <= 12):
        yy, mo = d_today.year, d_today.month
    ym_s = "%04d-%02d" % (yy, mo)
    first = ym_s + "-01"
    d0 = date(yy, mo, 1)
    nxt = (d0 + timedelta(days=32)).replace(day=1).isoformat()

    days: dict = {}
    stats = {"total": 0, "pending": 0, "overdue": 0, "plans": 0,
             "pending_all": 0, "overdue_all": 0, "today": today_s}
    try:
        rows = conn.execute(
            """SELECT r.id, r.plan_id, r.round, r.due_day, r.state, r.done_at,
                      p.doc_id, p.title, p.n_rounds, p.intervals
               FROM deck_reviews r JOIN deck_plans p ON p.id = r.plan_id
               WHERE p.status='active' AND r.due_day >= ? AND r.due_day < ?
               ORDER BY r.due_day, p.title, r.round""",
            (first, nxt)).fetchall()
        stats["plans"] = int(conn.execute(
            "SELECT COUNT(*) AS n FROM deck_plans WHERE status='active'"
        ).fetchone()["n"])
        stats["pending_all"] = int(conn.execute(
            """SELECT COUNT(*) AS n FROM deck_reviews r
               JOIN deck_plans p ON p.id = r.plan_id
               WHERE p.status='active' AND r.state='pending'"""
        ).fetchone()["n"])
        stats["overdue_all"] = int(conn.execute(
            """SELECT COUNT(*) AS n FROM deck_reviews r
               JOIN deck_plans p ON p.id = r.plan_id
               WHERE p.status='active' AND r.state='pending' AND r.due_day < ?""",
            (today_s,)).fetchone()["n"])
    except sqlite3.OperationalError:
        # 旧学科库还没被新版本建过 deck_* 表：给空月历，别让页签 500
        return {"ym": ym_s, "days": {}, "stats": stats}

    for r in rows:
        due = str(r["due_day"] or "")
        state = str(r["state"] or "pending")
        overdue = 0
        if state == "pending" and due:
            try:
                overdue = max(0, (d_today - date.fromisoformat(due)).days)
            except ValueError:
                overdue = 0
        did = int(r["doc_id"] or 0)
        days.setdefault(due, []).append({
            "id": int(r["id"]), "plan_id": int(r["plan_id"]),
            "round": int(r["round"] or 0), "due_day": due, "state": state,
            "done_at": r["done_at"], "doc_id": did,
            "title": r["title"] or ("文档 %d" % did),
            "n_rounds": int(r["n_rounds"] or 0),
            "overdue": overdue,
        })
        stats["total"] += 1
        if state == "pending":
            stats["pending"] += 1
            if overdue > 0:
                stats["overdue"] += 1

    return {"ym": ym_s, "days": days, "stats": stats}


def decks_for(conn: sqlite3.Connection, doc_ids) -> dict:
    """批量取若干课件**当前计划**的按钮状态（给课件清单用，避免 N+1 查询）。

    只读；只认 `status='active'`；表不存在（旧学科库没跑过 `init_db()`）时返回空 dict，
    让页签照常渲染成"未录入"，而不是 500。

    [C-0107 真值回填] 重建版同样丢了本函数（`/api/mindmap`、`/api/terms` 报
    `AttributeError: module 'core.study' has no attribute 'decks_for'`）。
    字节码真值 `study.funcs.txt::L466`：`decks_for  args=2  vars: conn,
    doc_ids, x, ids, marks, rows, r`；主 SQL 与 docstring 逐字取自真值常量表
    `study.strings.txt` 中标 `[decks_for]` 的常量（含 `plan_id` / `rounds_done`
    / `next_due` / `n_rounds` 四个别名）。
    返回值的键名按**调用方契约**定：真值 `dashboard.py::_artifact_doc_list`
    读的是 `deck_plan_id` / `deck_next_due` / `deck_rounds_done` /
    `deck_rounds_total`（前端 `dashboard.strings.txt` 里 JS 也用同名字段），
    这里给出这四个键，页签才能真的显示"已录入 · 下次 xx"。
    """
    ids: list = []
    for x in (doc_ids or []):
        try:
            ids.append(int(x))
        except (TypeError, ValueError):
            continue
    marks: dict = {}
    if not ids:
        return marks
    try:
        rows = conn.execute(
            "SELECT p.id AS plan_id, p.doc_id, p.n_rounds,\n"
            "                       (SELECT COUNT(*) FROM deck_reviews r\n"
            "                         WHERE r.plan_id=p.id AND r.state='done') AS rounds_done,\n"
            "                       (SELECT MIN(r.due_day) FROM deck_reviews r\n"
            "                         WHERE r.plan_id=p.id AND r.state='pending') AS next_due\n"
            "                FROM deck_plans p\n"
            "                WHERE p.status='active' AND p.doc_id IN ("
            + ",".join("?" * len(ids)) + ")",
            ids).fetchall()
    except sqlite3.OperationalError:
        return marks          # 旧学科库还没建 deck_* 表：当作"未录入"
    for r in rows:
        marks[int(r["doc_id"])] = {
            "deck_plan_id": int(r["plan_id"] or 0),
            "deck_next_due": r["next_due"] or "",
            "deck_rounds_done": int(r["rounds_done"] or 0),
            "deck_rounds_total": int(r["n_rounds"] or 0),
        }
    return marks


# ---------------- 录入与标记（卡 C-0108 真值回填） ----------------
# 下面五个函数是 `study.funcs.txt` 里 `enroll_deck`(L287) / `deck_mark`(L408) 的
# 配套私有函数（L227 _deck_today、L234 _deck_ensure、L251 _deck_load、
# L262 _deck_days、L270 _deck_summary）。重建时这一整段丢失，导致
# `POST /api/deck-enroll` 与 `POST /api/deck-mark` 一律 404（`dashboard.py::_do_post`
# 的分派分支依赖 `study.enroll_deck` / `study.deck_mark` 存在）。
# 本段每一条 SQL 都逐字取自 `study.strings.txt` 里对应标记的常量。
def _deck_today(today=None) -> str:
    """今天的**本地日历日**（与前端 `fmtTime` 的 toLocaleString 同口径）。

    [C-0108 真值回填] `study.funcs.txt::L227  _deck_today  args=1  vars: today`；
    docstring 取自 `study.strings.txt` 标 `[_deck_today]` 的常量。
    `today` 传日期串时原样归一化，供测试注入"今天"。
    """
    if today in (None, ""):
        return date.fromtimestamp(db.now()).isoformat()
    if isinstance(today, datetime):
        return today.date().isoformat()
    if isinstance(today, date):
        return today.isoformat()
    try:
        return date.fromisoformat(str(today)[:10]).isoformat()
    except ValueError:
        return date.fromtimestamp(db.now()).isoformat()


def _deck_ensure(conn: sqlite3.Connection) -> None:
    """写路径兜底：两张表不存在就地补建（幂等）。

    [C-0108 真值回填] `study.funcs.txt::L234  _deck_ensure  args=1  vars: conn`；
    两条探测 SQL（`SELECT 1 FROM deck_plans LIMIT 1` /
    `SELECT 1 FROM deck_reviews LIMIT 1`）与 docstring 逐字取自
    `study.strings.txt` 标 `[_deck_ensure]` 的常量。真值 docstring 说明了它为什么
    必须存在：看板的**只读连接不跑 `init_db()`**（`dashboard.py::_conn`），
    而 `/api/deck-enroll` 走的是会初始化的写连接——但"切到一个还没被新版本
    建过表的学科库"时，表可能真的还不存在。先探测、缺了才建，避免每次录入都写 DDL。
    """
    try:
        conn.execute("SELECT 1 FROM deck_plans LIMIT 1")
        conn.execute("SELECT 1 FROM deck_reviews LIMIT 1")
        return
    except sqlite3.OperationalError:
        pass
    conn.executescript(db.DECK_SCHEMA)


def _deck_load(conn: sqlite3.Connection, day: str) -> int:
    """某天还压着多少**待复习**项（软分摊与顺延都按它判断负载）。

    [C-0108 真值回填] `study.funcs.txt::L251  _deck_load  args=2  vars: conn, day, row`；
    SQL 取自 `study.strings.txt` 标 `[_deck_load]` 的常量。
    """
    row = conn.execute(
        """SELECT COUNT(*) AS n FROM deck_reviews r
           JOIN deck_plans p ON p.id = r.plan_id
           WHERE p.status='active' AND r.state='pending' AND r.due_day=?""",
        (day,)).fetchone()
    return int(row["n"] if row else 0)


def _deck_days(conn: sqlite3.Connection, plan_id: int) -> list[dict]:
    """一条计划的全部轮次明细。

    [C-0108 真值回填] `study.funcs.txt::L262  _deck_days  args=2  vars: conn,
    plan_id, rows, r`；SQL 与键名（`round` / `due_day` / `state` / `done_at`）
    取自 `study.strings.txt` 标 `[_deck_days]` 的常量。
    """
    rows = conn.execute(
        """SELECT round, due_day, state, done_at FROM deck_reviews
           WHERE plan_id=? ORDER BY round""",
        (plan_id,)).fetchall()
    return [{"round": int(r["round"] or 0),
             "due_day": r["due_day"] or "",
             "state": r["state"] or "pending",
             "done_at": r["done_at"]} for r in rows]


def _deck_summary(conn: sqlite3.Connection, plan) -> dict:
    """一条计划的当前状态（录入接口的返回体与"已录入"分支共用）。

    [C-0108 真值回填] `study.funcs.txt::L270  _deck_summary  args=2  vars: conn,
    plan, pid, done, nxt`；两条 SQL 与键名（`done` / `due_day` / `doc_id` /
    `title` / `n_rounds` / `intervals` / `offset_days`）取自
    `study.strings.txt` 标 `[_deck_summary]` 的常量。
    """
    pid = int(plan["id"])
    done = int(conn.execute(
        "SELECT COUNT(*) AS n FROM deck_reviews WHERE plan_id=? AND state='done'",
        (pid,)).fetchone()["n"])
    nxt_row = conn.execute(
        """SELECT due_day FROM deck_reviews WHERE plan_id=? AND state='pending'
           ORDER BY round LIMIT 1""",
        (pid,)).fetchone()
    nxt = str(nxt_row["due_day"]) if nxt_row and nxt_row["due_day"] else ""
    return {
        "plan_id": pid,
        "doc_id": int(plan["doc_id"] or 0),
        "title": plan["title"] or ("文档 %d" % int(plan["doc_id"] or 0)),
        "subject": plan["subject"] or "",
        "n_rounds": int(plan["n_rounds"] or 0),
        "intervals": plan["intervals"] or "",
        "offset_days": int(plan["offset_days"] or 0),
        "rounds_done": done,
        "next_due": nxt,
    }


def enroll_deck(conn: sqlite3.Connection, doc_id, subject: str = "",
                today=None) -> dict:
    """把一个课件录入遗忘曲线复习计划（**幂等**：重复点击不重建、不覆盖进度）。

    一次事务里发生两件事：写一条 `deck_plans` + 一次写死全部 5 轮的 `deck_reviews`。
    幂等分支返回 `already=True`，**绝不**重置已经完成的轮次——这是最容易写错的地方：
    若每次点击都重建，人点两下就把做过的复习清零了。

    [C-0108 真值回填] 本函数是 `study.funcs.txt::L287`

        enroll_deck  args=4  vars: conn, doc_id, subject, today, d0, row, out,
                                    trow, title, offset, now, cur

    逐字对应：`args=4` ⇒ `conn/doc_id/subject/today`（`today` 补 `None` 默认值，
    因为真值 dashboard 侧的调用点 `api_deck_enroll` 只传 3 个参数：
    `study.enroll_deck(conn, doc_id, subject=subject)` —— 与
    `rebuilt/dashboard/0138-api_deck_enroll.py` 一致）。局部名 `d0`=今天、
    `row`=幂等分支查到的计划行、`out`=返回体、`trow`=第一轮那一行、
    `title`=课件标题快照、`offset`=软分摊偏移、`now`=录入时刻、`cur`=INSERT 游标。
    4 条 SQL 与三条键名（`title` / `subject`）全部取自 `study.strings.txt`
    标 `[enroll_deck]` 的常量；返回体键名按**调用方契约**（前端
    `dashboard.strings.txt::deckEnrollClick` 读 `already/title/rounds_done/n_rounds/
    next_due/days[].due_day/plan_id`）。
    """
    if not doc_id:
        return {"ok": False, "error": "缺少 doc_id"}
    doc_id = int(doc_id)
    now = db.now()
    d0 = _deck_today(today)
    _deck_ensure(conn)

    row = conn.execute(
        "SELECT * FROM deck_plans WHERE doc_id=? AND status='active'",
        (doc_id,)).fetchone()
    if row:
        out = _deck_summary(conn, row)
        out["ok"] = True
        out["already"] = True
        return out

    title = ""
    trow = conn.execute("SELECT title FROM documents WHERE id=?", (doc_id,)).fetchone()
    if trow:
        title = trow["title"] or ""

    # 软分摊的起始偏移：只有**真做软分摊**才会有非 0 值。
    # ★ 无法从字节码真值唯一确定它的算式（见 result.json 的 uncertain），
    # 这里取保守值 0（= 第 1 轮就是今天），与 `db.DECK_SCHEMA` 的
    # `offset_days INTEGER DEFAULT 0 -- 录入软分摊的起始偏移（天）` 同义。
    offset = 0
    intervals = [int(x) for x in DECK_INTERVALS]
    cur = conn.execute(
        """INSERT INTO deck_plans(doc_id, title, subject, enrolled_at, intervals,
           n_rounds, offset_days, status, created_at, updated_at)
           VALUES(?,?,?,?,?,?,?,'active',?,?)""",
        (doc_id, title, subject or "", now, json.dumps(intervals, separators=(",", ":")),
         len(intervals), offset, now, now))
    plan_id = int(cur.lastrowid or 0)
    for i, gap in enumerate(intervals, 1):
        day = (date.fromisoformat(d0) + timedelta(days=offset + int(gap))).isoformat()
        conn.execute(
            """INSERT INTO deck_reviews(plan_id, round, due_day, state)
               VALUES(?,?,?,'pending')""",
            (plan_id, i, day))
    conn.commit()

    plan = conn.execute("SELECT * FROM deck_plans WHERE id=?", (plan_id,)).fetchone()
    out = _deck_summary(conn, plan)
    out["ok"] = True
    out["already"] = False
    out["days"] = _deck_days(conn, plan_id)
    out["today"] = d0
    return out


def deck_mark(conn: sqlite3.Connection, plan_id, round_, state: str = "done",
              today=None) -> dict:
    """标记某一轮"做完了 / 待复习 / 跳过"，并按**唯一一条顺延规则**维护后续日期。

    三条口径（与方案 §7.2 写死的一致）：
      1. **逾期不改计划**：`due_day` 一旦写下就不因为"没做"而变化（月历必须稳定）；
      2. **唯一的顺延**：标记完成时，若紧接着的下一轮**已经过期**，把它顺延到
         "明天起第一个负载 < cap 的日子"；
      3. 不做"跳过"的自动处理（`skipped` 只由人显式给），跳到下一轮同样受规则 2 约束。

    [C-0108 真值回填] 本函数是 `study.funcs.txt::L408`

        deck_mark  args=5  vars: conn, plan_id, round_, state, today, pid, rnd,
                                  row, d0, now, shifted, nxt

    逐字对应：`args=5` ⇒ `conn/plan_id/round_/state/today`（`state` 补 `"done"`
    默认值，因为真值 dashboard 侧调用点 `api_deck_mark` 是
    `study.deck_mark(conn, plan_id, round_, state)`，而
    `rebuilt/dashboard/0139-api_deck_mark.py` 里 `state` 是必给的；
    `today` 补 `None`）。局部名 `pid`=计划 id、`rnd`=轮次、`row`=该轮那一行、
    `d0`=今天、`now`=时刻、`shifted`=被顺延的轮次列表、`nxt`=下一待复习日。
    6 条 SQL 与键名（`pending` / `due_day` / `state` / `round`）全部取自
    `study.strings.txt` 标 `[deck_mark]` 的常量；返回体键名按调用方契约
    （前端 `dashboard.strings.txt::markDeckDone` 读 `next_due`）。
    """
    pid = int(plan_id or 0)
    rnd = int(round_ or 0)
    st = str(state or "done")
    if st not in ("done", "pending", "skipped"):
        return {"ok": False, "error": "非法 state: " + st}
    now = db.now()
    d0 = _deck_today(today)
    _deck_ensure(conn)

    row = conn.execute("SELECT * FROM deck_plans WHERE id=?", (pid,)).fetchone()
    if not row:
        return {"ok": False, "error": "复习项不存在"}
    trow = conn.execute(
        "SELECT * FROM deck_reviews WHERE plan_id=? AND round=?",
        (pid, rnd)).fetchone()
    if not trow:
        return {"ok": False, "error": "复习项不存在"}

    conn.execute("UPDATE deck_reviews SET state=?, done_at=? WHERE id=?",
                 (st, now if st == "done" else None, int(trow["id"])))

    # 规则 2：唯一的顺延。仅"标记完成 / 跳过"后触发；逾期的不改（规则 1）。
    shifted: list = []
    if st in ("done", "skipped"):
        d_mark = date.fromisoformat(d0)
        floor = d_mark + timedelta(days=1)          # "明天起"
        later = conn.execute(
            """SELECT id, round, due_day FROM deck_reviews
               WHERE plan_id=? AND round>? AND state='pending' ORDER BY round""",
            (pid, rnd)).fetchall()
        prev_day = d_mark
        for r in later:
            try:
                due = date.fromisoformat(str(r["due_day"]))
            except ValueError:
                continue
            if due < floor:
                need = prev_day + timedelta(days=1)
                due = need if need > floor else floor
            if due <= prev_day:                     # 同一计划内严格递增
                due = prev_day + timedelta(days=1)
            due_s = due.isoformat()
            if due_s != str(r["due_day"]):
                conn.execute("UPDATE deck_reviews SET due_day=? WHERE id=?",
                             (due_s, int(r["id"])))
                shifted.append(int(r["round"]))
            prev_day = due

    # 全部轮次都做完 ⇒ 计划收口；否则回到 active（例如把 done 改回 pending）
    rest = int(conn.execute(
        "SELECT COUNT(*) AS n FROM deck_reviews\n           WHERE plan_id=? AND state<>'done'",
        (pid,)).fetchone()["n"])
    if rest == 0:
        conn.execute("UPDATE deck_plans SET status='finished', updated_at=? WHERE id=?",
                     (now, pid))
    else:
        # 真值里这一条带 `WHERE id=? AND status='finished'`：只在计划已收口时复活它
        conn.execute("""UPDATE deck_plans SET status='active', updated_at=?
               WHERE id=? AND status='finished'""", (now, pid))
        conn.execute("UPDATE deck_plans SET updated_at=? WHERE id=?", (now, pid))
    conn.commit()

    nxt_row = conn.execute(
        """SELECT due_day FROM deck_reviews WHERE plan_id=? AND state='pending'
           ORDER BY round LIMIT 1""",
        (pid,)).fetchone()
    nxt = str(nxt_row["due_day"]) if nxt_row and nxt_row["due_day"] else ""
    return {"ok": True, "plan_id": pid, "round": rnd, "state": st,
            "done_at": now, "shifted": shifted, "next_due": nxt,
            "finished": rest == 0, "today": d0}


REASON_TAGS = ("概念混淆", "计算失误", "前置缺失", "审题错误", "记忆遗忘", "方法不会")


def add_mistake(conn: sqlite3.Connection, question: str = "", my_answer: str = "",
                right_answer: str = "", reason: str = "", kp_id: int | None = None,
                chunk_id: int | None = None, missing_prereq: str = "",
                doc_id: int | None = None) -> int:
    """记一道错题；带 `kp_id` 时**该知识点自动降级并提前复习**（SKILL.md 原文）。"""
    cur = conn.execute(
        """INSERT INTO mistakes(kp_id, doc_id, chunk_id, question, my_answer,
                                right_answer, reason, missing_prereq, resolved, created_at)
           VALUES(?,?,?,?,?,?,?,?,0,?)""",
        (kp_id, doc_id, chunk_id, question or "", my_answer or "",
         right_answer or "", reason or "", missing_prereq or "", db.now()),
    )
    mid = int(cur.lastrowid or 0)
    if kp_id:
        # 自动降级：间隔归零 + 立刻到期 + 记一次 lapses（掌握度随之下降）
        conn.execute(
            """UPDATE knowledge_points
               SET interval_days=0, due_at=?, lapses=COALESCE(lapses,0)+1,
                   updated_at=? WHERE id=?""",
            (db.now(), db.now(), int(kp_id)),
        )
    conn.commit()
    return mid


def resolve_mistake(conn: sqlite3.Connection, mistake_id: int) -> int:
    """把一道错题标记为已掌握（`resolved=1`）。"""
    conn.execute("UPDATE mistakes SET resolved=1 WHERE id=?", (int(mistake_id),))
    conn.commit()
    return int(mistake_id)


def read_note(name: str) -> dict:
    """按名字**模糊匹配**读一篇笔记原文（SKILL.md: `note read "<名字>"`）。"""
    if not config.NOTES_DIR.exists():
        return {"error": "还没有笔记目录"}
    want = str(name or "").strip().lower()
    if not want:
        return {"error": "请给出笔记名（支持模糊匹配）"}
    hits: list[Path] = []
    for p in sorted(config.NOTES_DIR.glob("*.md")):
        if want in p.stem.lower() or want in p.name.lower():
            hits.append(p)
    if not hits:                      # 文件名没中，再匹配 front-matter 标题
        for p in sorted(config.NOTES_DIR.glob("*.md")):
            text = p.read_text(encoding="utf-8", errors="replace")
            title = str(parse_note_meta(text).get("title") or "").lower()
            if want in title:
                hits.append(p)
    if not hits:
        return {"error": f"没有匹配的笔记：{name}"}
    p = hits[0]
    text = p.read_text(encoding="utf-8", errors="replace")
    meta = parse_note_meta(text)
    return {"file": p.name, "title": meta.get("title") or p.stem,
            "meta": meta, "text": text, "body": note_body(text),
            "matches": len(hits)}


def _fm_touch_updated(head: str, fm_end: int) -> str:
    """把 front-matter 里的 `updated:` **就地**改成现在（卡 C-0082）。

    只动这一行的值：键名、冒号后的空白、其余键（created/kind/tags/origin/
    kp_id/doc_id/source_label…）一字不动。这一行不存在、值是空的、或值不是
    数字（人手改坏了）时**直接原样返回**——宁可 updated 旧着，也不给笔记
    新增一个键、改一次结构。
    """
    block = head[3:fm_end]
    for s, e, line in _line_spans(block):
        key, sep, val = line.partition(":")
        if not sep or key.strip() != "updated" or not val.strip():
            continue
        try:
            float(val.strip())
        except ValueError:
            return head
        vs = len(key) + 1
        while vs < len(line) and line[vs] in " \t":
            vs += 1
        # 行尾的 `\r` 留在 `line` 里（按 `\n` 切行）——它是**行尾符的一半**，
        # 不能跟着旧值一起丢，否则这一行就从 CRLF 变成 LF（卡 C-0082 实测踩过）。
        ve = len(line.rstrip())
        return (head[:3 + s] + line[:vs] + f"{db.now():.0f}" + line[ve:]
                + head[3 + e:])
    return head


def _note_rebuild(raw: str, body: str) -> str:
    """把 `raw` 的正文区换成 `body`，其余**逐字**保留（卡 C-0082）。"""
    head = raw.lstrip("\ufeff \t\r\n")
    prefix = raw[:len(raw) - len(head)]
    # 行尾跟着原文件走：notes/ 下的文件是 CRLF（`save_note` 在 Windows 上就是
    # 这么写出来的），照原样还原，别让一次编辑把整篇的行尾风格换掉。
    eol = "\r\n" if "\r\n" in raw else "\n"
    new_body = body.replace("\r\n", "\n").replace("\r", "\n").rstrip("\n")
    fm_end = head.find("\n---", 3) if head.startswith("---") else -1
    if fm_end < 0:
        # 老笔记（没有 front-matter）：整篇就是人写的东西，正文区即全文
        return prefix + new_body.replace("\n", eol) + eol
    head = _fm_touch_updated(head, fm_end)
    fm_end = head.find("\n---", 3)
    rest = head[fm_end + 4:]
    title = str(parse_note_meta(head).get("title") or "").strip()
    start, end, _ = _note_split(rest, title)
    tail = rest[end:]
    # 正文与「## 出处」之间留一个空行（save_note 写出来就是这个形状）；
    # 没有出处段时（正文直接到文件尾）只要一个换行。
    sep = (eol + eol) if tail else eol
    return (prefix + head[:fm_end + 4] + rest[:start]
            + new_body.replace("\n", eol) + sep + tail)


def update_note_body(file: str, body: str,
                     expect_mtime: float | None = None) -> dict:
    """**只换正文**地改一篇笔记（卡 C-0082：知识点窗口里直接改）。

    刻意不复用 `save_note`：它按标题重算文件名、整篇重写、`extra` 全丢——
    人会因此丢掉 kp_id/doc_id（"跳回知识点"当场就断）与「## 出处」段。
    这里反过来做：把笔记切成 front-matter / 标题 / 正文 / 出处四段，只替换
    正文那一段，其余逐字搬过去，`updated:` 就地改（见 `_fm_touch_updated`）。

    `expect_mtime`：调用方读到的文件修改时间。对不上就**不写**、回 conflict
    —— `_lock` 只护本进程，MCP 的 kb_note_save 与另一个看板实例都改得动同一
    批文件，不比对就会把别人刚写进去的内容盖掉。
    落盘走同目录临时文件 + `os.replace`（原子替换）：直接 `write_text` 一旦
    中途失败，留下的就是半篇笔记。
    """
    name = str(file or "")
    # 只收 notes/ 下的**纯文件名**。多学科切库会换 config.NOTES_DIR
    # （core/subjects.py），任何路径分隔符 / 上级引用 / 隐藏前缀一律拒绝，
    # 免得一个 `../../x.md` 把库外的文件写了。
    if (not name or Path(name).name != name or not name.endswith(".md")
            or name.startswith(".")):
        return {"error": "文件名非法：只接受 notes/ 下的 .md 纯文件名"}
    path = config.NOTES_DIR / name
    try:
        if not path.is_file():
            return {"error": f"笔记不存在：{name}"}
        st = path.stat()
    except OSError:
        return {"error": f"笔记不存在：{name}"}
    if expect_mtime:
        try:
            drift = abs(st.st_mtime - float(expect_mtime))
        except (TypeError, ValueError):
            drift = 0.0
        if drift > 0.01:
            return {"error": "这篇笔记刚在别处被改过（可能另一处也开着它），"
                             "已拦下这次保存：请重开再看一遍再改",
                    "conflict": True, "mtime": st.st_mtime}
    try:
        with open(path, "r", encoding="utf-8", errors="replace", newline="") as f:
            raw = f.read()
    except OSError as e:
        return {"error": f"读不到笔记：{e}"}
    text = _note_rebuild(raw, str(body or ""))
    tmp = path.with_name(f"{path.name}.tmp{os.getpid()}")
    try:
        with open(tmp, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        os.replace(tmp, path)
    except OSError as e:
        try:
            tmp.unlink()
        except OSError:
            pass
        return {"error": f"写入失败：{e}"}
    return {"file": name, "path": str(path), "chars": len(text),
            "mtime": path.stat().st_mtime}


def list_notes() -> list[dict]:
    if not config.NOTES_DIR.exists():
        return []
    out = []
    for p in sorted(config.NOTES_DIR.glob("*.md")):
        text = p.read_text(encoding="utf-8", errors="replace")
        meta = parse_note_meta(text)
        title = meta.get("title") or p.stem
        out.append({"file": p.name, "path": str(p), "title": title,
                    "mtime": p.stat().st_mtime, "chars": len(text),
                    # 卡 C-0052：来源与记录时间。旧笔记没有这些键 → 空值 / 0，
                    # 页面按"无来源"渲染（不报错，也不假装有来源）。
                    "created": _as_num(meta.get("created")),
                    "kind": meta.get("kind", ""),
                    "tags": _fm_list(meta.get("tags")),
                    "origin": meta.get("origin", ""),
                    "kp_id": meta.get("kp_id", ""),
                    "doc_id": int(_as_num(meta.get("doc_id"))),
                    "source_label": meta.get("source_label", ""),
                    "question": meta.get("question", "")})
    # [重建] 原函数体在 append 之后被截断，缺这一个 return —— 少了它，
    # `list_notes()` 会返回 None，笔记页签与 `notes_for()` 全部空掉。
    return out


def notes_for(kp_id: str = "", doc_id: int = 0) -> list[dict]:
    """按来源过滤笔记（卡 C-0052 的「查看笔记」与笔记页签跳转用）。

    `kp_id`：只要**绑定该知识点**的笔记；`doc_id`：只要来源是这份课件的
    （知识点笔记与课件问答都带 doc_id）。两个都给＝同时满足。
    一个都不给则返回空——"没有条件"不等于"全部"，免得调用方误用成全量接口。
    """
    if not kp_id and not doc_id:
        return []
    want_kp = str(kp_id).strip()
    want_doc = int(doc_id or 0)
    out = []
    for n in list_notes():
        if want_kp and str(n.get("kp_id") or "") != want_kp:
            continue
        if want_doc and int(n.get("doc_id") or 0) != want_doc:
            continue
        out.append(n)
    return out


# ---------------- 看板数据 ----------------
def dashboard(conn: sqlite3.Connection) -> dict:
    now = db.now()
    docs = conn.execute(
        """SELECT COUNT(*) AS n, COALESCE(SUM(n_chunks),0) AS chunks,
                  COALESCE(SUM(n_chars),0) AS chars FROM documents"""
    ).fetchone()
    by_kind = conn.execute(
        "SELECT kind, COUNT(*) AS n FROM documents GROUP BY kind ORDER BY n DESC"
    ).fetchall()
    kp = conn.execute("SELECT COUNT(*) AS n FROM knowledge_points").fetchone()
    due = conn.execute(
        "SELECT COUNT(*) AS n FROM knowledge_points WHERE due_at<=? AND last_review IS NOT NULL",
        (now,)).fetchone()
    new_kp = conn.execute(
        "SELECT COUNT(*) AS n FROM knowledge_points WHERE last_review IS NULL").fetchone()
    reviews_today = conn.execute(
        "SELECT COUNT(*) AS n FROM reviews WHERE ts>=?",
        (now - (now % DAY),)).fetchone()
    weak: list[dict] = []
    for r in conn.execute(
        "SELECT * FROM knowledge_points WHERE reps>0 ORDER BY interval_days ASC LIMIT 50"
    ).fetchall():
        m = compute_mastery(r)
        weak.append({"id": int(r["id"]), "name": r["name"], "mastery": m,
                     "interval_days": float(r["interval_days"] or 0),
                     "lapses": int(r["lapses"] or 0), "due_at": r["due_at"]})
    weak.sort(key=lambda d: d["mastery"])
    status = conn.execute(
        "SELECT status, COUNT(*) AS n FROM documents GROUP BY status"
    ).fetchall()
    return {
        "documents": {"total": int(docs["n"]), "chunks": int(docs["chunks"]),
                      "chars": int(docs["chars"]),
                      "by_kind": [{"kind": r["kind"], "n": int(r["n"])} for r in by_kind],
                      "by_status": [{"status": r["status"], "n": int(r["n"])} for r in status]},
        "knowledge": {"total": int(kp["n"]), "due": int(due["n"]),
                      "new": int(new_kp["n"]), "reviews_today": int(reviews_today["n"])},
        "weakest": weak[:10],
        "mistakes": mistake_stats(conn),
        "notes": len(list_notes()),
        "fts5": db.get_meta(conn, "fts5", "0") == "1",
    }