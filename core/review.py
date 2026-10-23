"""复习流程后端（C-0127，2026-10-23）：复习前自测 + 三步复习数据。

用户需求（2026-10-23）：
  1) 复习前自测：随机抽课件**一半**知识点，用知识点总结文字**挖名词填空**；
     不能全对＝不熟悉 → 得该课件熟悉度（持久化，供遗忘曲线月历/进度悬浮窗用）。
  2) 自测后三步复习：按思维导图逐个 KP → 整份课件笔记 → 预习重点名词；
     本模块只提供**数据**（kps/notes/terms/selftest），不写前端。
  3) 与遗忘曲线月历打通：熟悉度/未掌握 KP 写入 `logs/review/<doc>-自测.json`。

**零 AI 调用、零新依赖**：名词抽取用启发式（无 jieba）；章节映射复用
`core/teach.py::map_courseware_chapter(allow_ai=False)` 的本地兜底；预习名词读
`logs/knowledge/<教材id>-预习总结-第<章>.json` 的 `terms`；笔记只用 `core/study.py` 的
元数据（正文按需由 `/api/note` 读，本模块不搬运正文）。

## 前端契约（task-32/33；dashboard 路由由前端智能体统一落地，本模块只提供函数）

| 路由 | 调用 | 返回 |
|---|---|---|
| `GET /api/review/coursewares` | `list_courseware_reviews(conn)`（**conn 只读**，锁内） | `{"ok":true,"items":[{doc_id,title,n_kps,selftest:{exists,familiarity,n_sample,n_unfamiliar,updated_at},terms:{available,n,source},chapter:{no,name}}]}` |
| `GET /api/review/flow?doc_id=` | `build_flow(conn, doc_id)`（**conn 只读**，锁内） | `{ok,doc:{id,title},kps[],notes:{groups[],unbound[],n_notes},terms:{source,chapter,items[],n},selftest:{...},stages[]}` |
| `GET /api/review/selftest?doc_id=` | `start_selftest(doc_id, db_path=None)`（**不需要 conn**，锁外） | `{ok,doc_id,title,session_id,n_total,n_sample,ttl_seconds,kps[],items:[{kp_id,name,segments[]}],note}` |
| `POST /api/review/selftest/submit` | `submit_selftest(doc_id,session_id,answers)`（**锁外**，要写 `logs/review/`） | `{ok,familiarity,n_sample,n_total,n_familiar,n_unfamiliar,unfamiliar_ids,results[]}` |
| `GET /api/review/selftest/status?doc_id=` | `selftest_status(doc_id)`（**不需要 conn**，锁外） | `{ok,exists,familiarity,n_sample,n_total,n_unfamiliar,updated_at,unfamiliar_ids,kps}` |

- 失败一律返回 `{"ok":false,"error":"人话","_code":400}`（不进异常）；路由用 `_json_auto`
  发送即可把 `_code` 映射成 HTTP 400 并从 JSON 剥掉，**不会 500**。
- `conn`/`db_path` 都可注入：`build_flow/list_courseware_reviews` 接现成只读 conn；
  `start_selftest` 接 `db_path`（标题查库用）；`submit_selftest/selftest_status` 只碰文件。
- 会话在**进程内存**（TTL 30min）；服务重启后需重新开始自测（持久化结果不丢）。
- 提交格式：`answers` 可以是 `{kp_id:[blank1,blank2,...]}`，也可以是扁平 list（按 items/blank 顺序）。
"""
from __future__ import annotations

import json
import math
import os
import random
import re
import secrets
import sqlite3
import threading
import time
import unicodedata
from pathlib import Path

from . import config
from . import study

REVIEW_VERSION = "C-0127-review-v2"
REVIEW_SUFFIX = "-自测.json"
SESSION_TTL = 1800            # 自测会话 30 分钟（进程内存，过期需重新开始）
NORM_VERSION = "C-0127-norm-v1"
_MAX_BLANKS = 4
_MIN_BLANKS = 2
_HINT_ELLIPSIS = "…"

# 常见虚词/单字停用词：不拿来挖空（宁可少挖，也不把"的/是/了"当名词）
_STOP_CHARS = set("的了是在和与及为可使能会有无之其这那也都而或就对从到把被让给不很更最太"
                  "一二三四五六七八九十几每个种条项点些我们你他她它吗呢吧啊哦嗯则若因"
                  "由于并且但所以如果虽然然而以及等")
_STOP_TERMS = set("""
什么 怎么 为什么 可以 不能 可能 应该 需要 进行 通过 由于 因此 所以 但是 而且 或者 以及
这些 那些 这个 那个 其中 包括 主要 常见 一般 通常 具有 产生 引起 导致 增加 减少 作用
""".split())

# 进程内会话：session_id -> {doc_id,title,created,items:[{kp_id,name,segments,answers}],...}
_SELFTEST: dict[str, dict] = {}
_SESS_LOCK = threading.Lock()


class ReviewError(Exception):
    """复习模块的可读错误（路由层转 200/400 JSON，不抛 500）。"""

    def __init__(self, msg: str, code: int = 400):
        super().__init__(msg)
        self.code = int(code)


# ---------------------------------------------------------------- 基础路径/IO
def review_dir() -> Path:
    return config.LOG_DIR / "review"


def selftest_out_file(doc_id: int) -> Path:
    return review_dir() / ("%d%s" % (int(doc_id), REVIEW_SUFFIX))


def _atomic_write_json(data: dict, out: Path) -> None:
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, out)


def _load_json(path: Path) -> dict:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _kb():
    from . import kp_build
    return kp_build


def _teach():
    from . import teach
    return teach


def _kp_artifact(doc_id: int):
    """读 KP 产物 → `(path, kps, meta)`；无产物 `(path, [], {})`。"""
    kb = _kb()
    path = kb.out_file(doc_id)
    doc = kb._load_prev(path)
    kps = [k for k in (doc.get("knowledge_points") or []) if isinstance(k, dict)]
    meta = doc.get("meta") if isinstance(doc.get("meta"), dict) else {}
    return path, kps, meta


def _doc_title(conn, doc_id: int) -> str:
    try:
        row = conn.execute("SELECT title FROM documents WHERE id=?",
                           (int(doc_id),)).fetchone()
        if row:
            title = str((row[0] if not hasattr(row, "keys") else row["title"]) or "").strip()
            if title:
                return title
    except sqlite3.Error:
        pass
    try:
        return _kb().doc_title_v2(int(doc_id))
    except Exception:  # noqa: BLE001
        return "文档 %d" % int(doc_id)


# ---------------------------------------------------------------- 自测持久化
def _read_selftest(doc_id: int) -> dict:
    return _load_json(selftest_out_file(doc_id))


def _selftest_summary(data: dict) -> dict:
    """把持久化结果压成**自洽**摘要：一切计数都从 `kps` 现算。

    ⚠ 2026-10-23 修（dash-fixer 现场报告）：v1 曾把历史会话结果合并进 `kps`，
    `meta` 却是本次结果 → 出现"熟悉度 100% 但不熟 1"（`familiarity=1.0` 而
    `n_unfamiliar=1`）。v2 起 `kps` **只保留本次抽中的 KP**，状态不再读 meta 的
    计数（meta 仅作留档），因此 `familiarity == n_familiar/n_sample`、
    `n_unfamiliar == len(unfamiliar_ids)` 恒成立；旧 v1 文件按同一规则现算，
    并标 `legacy=true`（下一次提交即升级成 v2）。
    """
    if not data:
        return {"exists": False, "familiarity": 0.0, "n_sample": 0,
                "n_familiar": 0, "n_unfamiliar": 0, "n_total": 0,
                "updated_at": "", "unfamiliar_ids": [], "legacy": False}
    meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
    kps = data.get("kps") if isinstance(data.get("kps"), dict) else {}
    unfamiliar = [str(k) for k, v in kps.items()
                  if isinstance(v, dict) and not v.get("familiar")]
    n_sample = len(kps)
    n_familiar = n_sample - len(unfamiliar)
    return {
        "exists": True,
        "familiarity": round(n_familiar / n_sample, 4) if n_sample else 0.0,
        "n_sample": n_sample,
        "n_familiar": n_familiar,
        "n_total": int(meta.get("n_total") or 0),
        "n_unfamiliar": len(unfamiliar),
        "updated_at": str(meta.get("generated_at") or ""),
        "unfamiliar_ids": unfamiliar,
        "legacy": str(meta.get("version") or "") != REVIEW_VERSION,
    }


def selftest_status(doc_id: int) -> dict:
    """读已持久化的自测结果；从未测过 → `{ok:true,exists:false,...}`。"""
    data = _read_selftest(doc_id)
    summary = _selftest_summary(data)
    kps = data.get("kps") if isinstance(data.get("kps"), dict) else {}
    out = {"ok": True, "doc_id": int(doc_id), **summary,
           "kps": {k: {"familiar": bool((v or {}).get("familiar")),
                       "correct": int((v or {}).get("correct") or 0),
                       "answers": list((v or {}).get("answers") or []),
                       "user_answers": list((v or {}).get("user_answers") or [])}
                   for k, v in kps.items() if isinstance(v, dict)}}
    return out


# ---------------------------------------------------------------- 章节 / 名词
def _resolve_chapter(conn, doc_id: int) -> dict:
    """课件 → 教材章（**只读**：优先 supplement meta.chapter_map，无则本地兜底）。"""
    teach = _teach()
    try:
        data = _load_json(teach.supplement_out_file(doc_id))
    except Exception:  # noqa: BLE001
        data = {}
    meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
    cm = meta.get("chapter_map") if isinstance(meta.get("chapter_map"), dict) else {}
    if int(cm.get("chapter_no") or 0):
        return {"no": int(cm.get("chapter_no") or 0),
                "name": str(cm.get("name") or cm.get("chapter_name") or ""),
                "page_label": str(cm.get("page_label") or ""),
                "heading_path": str(cm.get("heading_path") or ""),
                "source": "supplement_meta"}
    # 本地兜底：out_path 指向一个**不存在的缓存路径** → 只读、不写 supplement 文件
    try:
        cache = review_dir() / "_map_never_written.json"
        cm = teach.map_courseware_chapter(doc_id, allow_ai=False,
                                          out_path=cache, log=lambda *a, **k: None)
    except Exception:  # noqa: BLE001
        return {}
    if int((cm or {}).get("chapter_no") or 0):
        return {"no": int(cm.get("chapter_no") or 0),
                "name": str(cm.get("name") or cm.get("chapter_name") or ""),
                "page_label": str(cm.get("page_label") or ""),
                "heading_path": str(cm.get("heading_path") or ""),
                "source": "local_fallback"}
    return {}


def _norm_term_items(raw) -> list:
    out = []
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        term = str(item.get("term") or item.get("name") or "").strip()
        if not term:
            continue
        out.append({"term": term,
                    "en": str(item.get("en") or item.get("english") or "").strip(),
                    "def": str(item.get("def") or item.get("definition") or
                               item.get("summary") or "").strip(),
                    "why": str(item.get("why") or item.get("reason") or "").strip()})
    return out


def _terms_from_artifact(path: Path) -> list:
    data = _load_json(path)
    for key in ("terms", "items", "knowledge_points", "data"):
        if isinstance(data.get(key), list):
            items = _norm_term_items(data[key])
            if items:
                return items
    return []


def _resolve_terms(conn, doc_id: int) -> dict:
    """三步复习第三步的"预习重点名词"（零 AI）。

    定章 → 读 `<教材id>-预习总结-第<章>.json` 的 terms；无则退回
    `<教材id>/<课件id>-名词解释.json`；再退回 `{source:"none",items:[]}`。
    """
    teach = _teach()
    try:
        tb = teach.pick_textbook(conn)
    except Exception:  # noqa: BLE001
        tb = None
    chapter = _resolve_chapter(conn, doc_id)
    base = {"source": "none", "chapter": chapter or {},
            "items": [], "n": 0}
    if chapter.get("no"):
        if tb:
            preview = (config.LOG_DIR / "knowledge" /
                       ("%d-预习总结-第%d章.json" % (int(tb["id"]),
                                                    int(chapter["no"]))))
            items = _terms_from_artifact(preview)
            if items:
                data = _load_json(preview)
                pm = data.get("meta") if isinstance(data.get("meta"), dict) else {}
                return {"source": "preview_chapter",
                        "artifact": str(preview),
                        "chapter": dict(chapter,
                                        page_label=str(pm.get("page_label") or
                                                       chapter.get("page_label") or ""),
                                        name=str(chapter.get("name") or
                                                 pm.get("chapter_name") or "")),
                        "items": items, "n": len(items)}
    # 退路：名词解释产物（教材优先，其次课件）
    for did in ([int(tb["id"])] if tb else []) + [int(doc_id)]:
        p = config.LOG_DIR / "knowledge" / ("%d-名词解释.json" % did)
        if p.exists():
            items = _terms_from_artifact(p)
            if items:
                return {"source": "terms_artifact", "artifact": str(p),
                        "chapter": chapter or {}, "items": items,
                        "n": len(items)}
    return base


def _supplement_meta(doc_id: int) -> dict:
    teach = _teach()
    try:
        data = _load_json(teach.supplement_out_file(doc_id))
    except Exception:  # noqa: BLE001
        return {}
    return data.get("meta") if isinstance(data.get("meta"), dict) else {}


# ---------------------------------------------------------------- 名词挖空（启发式）
def _is_good_term(s: str) -> bool:
    s = (s or "").strip()
    if not (2 <= len(s) <= 24):
        return False
    if s in _STOP_TERMS:
        return False
    if all(ch in _STOP_CHARS or not ch.strip() for ch in s):
        return False
    # 至少 2 个汉字或 1 个英文/数字词才算"名词"
    han = len(re.findall(r"[\u4e00-\u9fff]", s))
    ascii_word = bool(re.search(r"[A-Za-z0-9][A-Za-z0-9\-]{1,}", s))
    return han >= 2 or ascii_word


def _split_phrases(text: str) -> list:
    """按标点切短语（保留原顺序），去首尾序号/括号标点。"""
    out = []
    for seg in re.split(r"[。！？；;：:\n\r\t，,、（）()\[\]【】“”\"'·]+", text or ""):
        seg = re.sub(r"^[\s\d一二三四五六七八九十\.\-、]+", "", seg).strip()
        if seg:
            out.append(seg)
    return out


def _candidate_terms(text: str, kp_name: str, all_names: list, points: list) -> list:
    """按优先级产出候选名词（去重、保持首次出现顺序）。"""
    seen = set()
    out: list = []

    def add(s, prio):
        s = (s or "").strip()
        if not _is_good_term(s) or s in seen:
            return
        if s not in text:                       # 必须能在给定文本里挖出来
            return
        seen.add(s)
        out.append((s, prio))

    if kp_name:
        add(kp_name, 0)
    for nm in all_names:
        add(nm, 1)
    for pt in points:
        for ph in _split_phrases(str(pt)):
            if 2 <= len(ph) <= 24:
                add(ph, 2)
    # 启发式：汉字连续段 2~8 字 / 英文数字词
    for m in re.finditer(r"[\u4e00-\u9fff]{2,8}|[A-Za-z][A-Za-z0-9\-]{2,}", text):
        add(m.group(0), 3)
    out.sort(key=lambda x: (x[1], -len(x[0])))
    return [s for s, _p in out]


def _choose_blanks(text: str, cands: list) -> list:
    """从候选里挑 2~4 个不重叠名词；不足时用长短语/最长汉字段兜底。"""
    chosen: list[tuple[int, int, str]] = []
    occupied = [False] * len(text)

    def take(answer: str) -> bool:
        start = text.find(answer)
        if start < 0:
            return False
        end = start + len(answer)
        if any(occupied[i] for i in range(start, end)):
            return False
        for i in range(start, end):
            occupied[i] = True
        chosen.append((start, end, answer))
        return True

    for c in cands:
        if len(chosen) >= _MAX_BLANKS:
            break
        take(c)
    # 兜底 1：points 里长度≥4 的短语（若还没挖够）
    if len(chosen) < _MIN_BLANKS:
        for m in re.finditer(r"[\u4e00-\u9fff]{4,12}|[A-Za-z0-9\-]{4,}", text):
            if len(chosen) >= _MIN_BLANKS:
                break
            take(m.group(0))
    # 兜底 2：至少保证 1 个空（否则这条 KP 判不了熟悉度）
    if not chosen:
        for m in re.finditer(r"[\u4e00-\u9fff]{2,8}|[A-Za-z0-9\-]{3,}", text):
            take(m.group(0))
            break
    chosen.sort(key=lambda x: x[0])
    return chosen


def _segments(text: str, blanks: list) -> list:
    """把文本切成 `{t:"text"}` / `{t:"blank",i,answer,hint}` 序列。"""
    segs: list = []
    cursor = 0
    for i, (start, end, answer) in enumerate(blanks):
        if start > cursor:
            segs.append({"t": "text", "text": text[cursor:start]})
        segs.append({"t": "blank", "i": i, "answer": answer,
                     "hint": "%s%s（%d 字）" % (answer[:1], _HINT_ELLIPSIS, len(answer))})
        cursor = end
    if cursor < len(text):
        segs.append({"t": "text", "text": text[cursor:]})
    if not segs:
        segs = [{"t": "text", "text": text}]
    return segs


def _make_item(kp: dict, all_names: list) -> dict:
    name = str(kp.get("name") or kp.get("node_name") or "").strip()
    points = [str(p) for p in (kp.get("points") or []) if str(p).strip()]
    summary = str(kp.get("summary") or "").strip()
    text = summary or "；".join(points) or name
    if len(text) < 4:
        text = (name + "。" + text).strip("。")
    if len(text) > 400:                      # 一条 KP 的填空不要拉太长
        text = text[:400].rstrip("，。；、") + "。"
    cands = _candidate_terms(text, name, all_names, points)
    blanks = _choose_blanks(text, cands)
    answers = [b[2] for b in blanks]
    return {"kp_id": str(kp.get("id") or ""), "name": name,
            "segments": _segments(text, blanks), "answers": answers}


def _prune_sessions() -> None:
    now = time.time()
    for sid in [s for s, v in _SELFTEST.items()
                if now - float(v.get("created") or 0) > SESSION_TTL]:
        _SELFTEST.pop(sid, None)


# ---------------------------------------------------------------- ① 自测
def start_selftest(doc_id: int, *, db_path=None, rng=None) -> dict:
    """抽一半知识点、挖名词空 → 返回题面 + session_id（**不写盘、不调 AI**）。

    答案随 `segments` 的 blank 一起返回（前端提交前不要展示）；会话 30 分钟过期。
    """
    doc_id = int(doc_id or 0)
    if doc_id <= 0:
        return {"ok": False, "error": "doc_id 无效", "_code": 400}
    path, kps, meta = _kp_artifact(doc_id)
    if not kps:
        return {"ok": False, "error": "这份课件还没有末级知识点产物（先整理知识点）",
                "_code": 400}
    rng = rng or random.Random()
    n_total = len(kps)
    n_sample = max(1, min(n_total, int(math.ceil(n_total / 2.0))))
    idx = list(range(n_total))
    rng.shuffle(idx)
    all_names = [str(k.get("name") or k.get("node_name") or "").strip() for k in kps]
    prior = _read_selftest(doc_id)
    prior_kps = prior.get("kps") if isinstance(prior.get("kps"), dict) else {}
    items = []
    for i in idx[:n_sample]:
        item = _make_item(kps[i], all_names)
        item["unfamiliar"] = (str(item["kp_id"]) in prior_kps
                              and not prior_kps[str(item["kp_id"])].get("familiar"))
        items.append(item)
    sid = secrets.token_hex(8)
    title = _doc_title_for(doc_id, db_path)
    with _SESS_LOCK:
        _prune_sessions()
        _SELFTEST[sid] = {"doc_id": doc_id, "title": title,
                          "created": time.time(), "n_total": n_total,
                          "n_sample": n_sample, "items": items,
                          "artifact": str(path)}
    return {"ok": True, "doc_id": doc_id, "title": title,
            "session_id": sid, "n_total": n_total, "n_sample": n_sample,
            "ttl_seconds": SESSION_TTL,
            "kps": [{"kp_id": it["kp_id"], "name": it["name"],
                     "unfamiliar": bool(it.get("unfamiliar"))} for it in items],
            "items": [{"kp_id": it["kp_id"], "name": it["name"],
                       "segments": it["segments"]} for it in items],
            "note": "答案随 blank 字段返回，前端提交前请勿展示；全部答对才算熟悉这条 KP"}


def _doc_title_for(doc_id: int, db_path=None) -> str:
    """不传 conn 时的标题：kp_build 的库内标题（只读 SQLite）。"""
    try:
        return _kb().doc_title_v2(int(doc_id), db_path)
    except Exception:  # noqa: BLE001
        return "文档 %d" % int(doc_id)


def _norm_answer(s) -> str:
    """归一化：全半角统一 + 小写 + 去空白/标点（中文虚词差异不额外放宽）。"""
    s = unicodedata.normalize("NFKC", str(s or "")).lower()
    return re.sub(r"[\s\W_]+", "", s, flags=re.UNICODE)


def _user_answer_list(item: dict, answers, flat_cursor: list) -> list:
    """把前端提交的 answers 归到这条 KP：支持 dict{kp_id:[...]} 与扁平 list。"""
    if isinstance(answers, dict):
        v = answers.get(item["kp_id"])
        if v is None:
            v = answers.get(str(item["kp_id"]))
        if v is None:
            return [""] * len(item["answers"])
        if isinstance(v, str):
            return [v]
        return [str(x) for x in (v or [])]
    if isinstance(answers, list):
        n = len(item["answers"])
        start = flat_cursor[0]
        out = [str(x) for x in answers[start:start + n]]
        flat_cursor[0] = start + n
        return out + [""] * max(0, n - len(out))
    return [""] * len(item["answers"])


def submit_selftest(doc_id: int, session_id: str, answers) -> dict:
    """判分并持久化：任一空错 → 该 KP unfamiliar；familiarity=熟悉 KP 数/抽中数。"""
    doc_id = int(doc_id or 0)
    sid = str(session_id or "").strip()
    with _SESS_LOCK:
        _prune_sessions()
        sess = _SELFTEST.get(sid)
        if not sess:
            return {"ok": False,
                    "error": "自测会话不存在或已过期（30 分钟），请重新开始自测",
                    "_code": 400}
        if int(sess.get("doc_id") or 0) != doc_id:
            return {"ok": False, "error": "自测会话与课件不匹配", "_code": 400}
        items = sess["items"]
        title = sess.get("title") or ""
        n_total = int(sess.get("n_total") or 0)
        n_sample = int(sess.get("n_sample") or 0)
    results = []
    flat_cursor = [0]
    for it in items:
        user = _user_answer_list(it, answers, flat_cursor)
        correct_flags = []
        for j, ans in enumerate(it["answers"]):
            u = user[j] if j < len(user) else ""
            correct_flags.append(_norm_answer(u) == _norm_answer(ans) and
                                 bool(_norm_answer(ans)))
        correct = all(correct_flags) and bool(it["answers"])
        results.append({"kp_id": it["kp_id"], "name": it["name"],
                        "familiar": bool(correct),
                        "correct": sum(1 for x in correct_flags if x),
                        "n_blanks": len(it["answers"]),
                        "answers": list(it["answers"]),
                        "user_answers": [user[j] if j < len(user) else ""
                                         for j in range(len(it["answers"]))]})
    n_familiar = sum(1 for r in results if r["familiar"])
    familiarity = round(n_familiar / n_sample, 4) if n_sample else 0.0
    unfamiliar_ids = [r["kp_id"] for r in results if not r["familiar"]]
    generated_at = time.strftime("%Y-%m-%dT%H:%M:%S")
    # v2（2026-10-23）：kps **只保留本次抽中的 KP**，与 meta 的本次计数严格自洽。
    # 不再合并历史会话——否则会出现"familiarity=1.0 但 kps 里还留着上次的 unfamiliar"，
    # 导致 status/flow 与 meta 互相打架（dash-fixer 现场报告的就是这个）。
    current = {r["kp_id"]: {"familiar": r["familiar"], "correct": r["correct"],
                            "n_blanks": r["n_blanks"],
                            "answers": r["answers"],
                            "user_answers": r["user_answers"]}
               for r in results}
    payload = {
        "meta": {"version": REVIEW_VERSION, "doc_id": doc_id, "title": title,
                 "generated_at": generated_at, "created_at": time.time(),
                 "session_id": sid, "n_total": n_total, "n_sample": n_sample,
                 "n_familiar": n_familiar, "n_unfamiliar": len(unfamiliar_ids),
                 "unfamiliar_ids": list(unfamiliar_ids),
                 "familiarity": familiarity, "norm": NORM_VERSION},
        "kps": current,
    }
    _atomic_write_json(payload, selftest_out_file(doc_id))
    with _SESS_LOCK:
        _SELFTEST.pop(sid, None)
    return {"ok": True, "doc_id": doc_id, "familiarity": familiarity,
            "n_sample": n_sample, "n_total": n_total,
            "n_familiar": n_familiar, "n_unfamiliar": len(unfamiliar_ids),
            "unfamiliar_ids": unfamiliar_ids,
            "generated_at": generated_at,
            "results": results}


# ---------------------------------------------------------------- ② 三步复习数据
def _note_meta(n: dict) -> dict:
    return {"file": n.get("file"), "title": n.get("title", ""),
            "chars": int(n.get("chars") or 0),
            "created": float(n.get("created") or 0.0),
            "source_label": n.get("source_label", ""),
            "kp_id": str(n.get("kp_id") or ""),
            "doc_id": int(n.get("doc_id") or 0)}


def build_flow(conn, doc_id: int) -> dict:
    """三步复习数据：kps（按产物顺序）+ notes（按 KP 分组+unbound）+ terms + selftest。"""
    doc_id = int(doc_id or 0)
    if doc_id <= 0:
        return {"ok": False, "error": "doc_id 无效", "_code": 400}
    _path, kps, meta = _kp_artifact(doc_id)
    if not kps:
        return {"ok": False, "error": "这份课件还没有末级知识点产物（先整理知识点）",
                "_code": 400}
    status = selftest_status(doc_id)
    unfamiliar = set(status.get("unfamiliar_ids") or [])
    # KP 顺序 = 产物顺序（node_path 仅作展示字段，不再打乱）
    kp_ids = [str(k.get("id") or "") for k in kps]
    notes = study.notes_for(doc_id=doc_id)
    by_kp: dict = {kid: [] for kid in kp_ids}
    unbound = []
    for n in notes:
        meta_n = _note_meta(n)
        kid = meta_n["kp_id"]
        if kid and kid in by_kp:
            by_kp[kid].append(meta_n)
        else:
            unbound.append(meta_n)
    groups = []
    kp_out = []
    for k in kps:
        kid = str(k.get("id") or "")
        name = str(k.get("name") or k.get("node_name") or "")
        kp_out.append({
            "kp_id": kid, "name": name,
            "summary": str(k.get("summary") or ""),
            "points": [str(p) for p in (k.get("points") or [])],
            "pages": [int(p) for p in (k.get("pages") or [])
                      if str(p).lstrip("-").isdigit()],
            "node_path": [str(x) for x in (k.get("node_path") or [])],
            "note_count": len(by_kp.get(kid) or []),
            "unfamiliar": kid in unfamiliar,
        })
        groups.append({"kp_id": kid, "kp_name": name,
                       "notes": by_kp.get(kid) or []})
    terms = _resolve_terms(conn, doc_id)
    title = _doc_title(conn, doc_id)
    return {
        "ok": True, "doc": {"id": doc_id, "title": title},
        "kps": kp_out,
        "notes": {"groups": groups, "unbound": unbound,
                  "n_notes": len(notes)},
        "terms": terms,
        "selftest": status,
        "stages": [
            {"key": "kps", "title": "按思维导图逐个知识点",
             "n": len(kp_out), "done": sum(1 for x in kp_out if x["unfamiliar"])},
            {"key": "notes", "title": "整份课件笔记",
             "n": len(notes), "grouped": len(notes) - len(unbound)},
            {"key": "terms", "title": "预习重点名词",
             "n": int(terms.get("n") or 0)},
        ],
    }


# ---------------------------------------------------------------- ③ 课件复习列表
def list_courseware_reviews(conn) -> list:
    """列出**有 KP 产物**的课件 + 自测熟悉度 + 名词可用性 + 映射章节（零 AI）。"""
    try:
        rows = conn.execute("SELECT id, title FROM documents ORDER BY id").fetchall()
    except sqlite3.Error:
        return []
    out = []
    for r in rows:
        sid = int(r["id"] if hasattr(r, "keys") else r[0])
        title = str((r["title"] if hasattr(r, "keys") else r[1]) or "").strip()
        try:
            path, kps, meta = _kp_artifact(sid)
        except Exception:  # noqa: BLE001
            continue
        if not kps:
            continue
        status = _selftest_summary(_read_selftest(sid))
        try:
            terms = _resolve_terms(conn, sid)
        except Exception:  # noqa: BLE001
            terms = {"source": "none", "items": [], "n": 0, "chapter": {}}
        chapter = terms.get("chapter") or _resolve_chapter(conn, sid)
        out.append({
            "doc_id": sid,
            "title": title or str(meta.get("deck") or ""),
            "n_kps": len(kps),
            "selftest": {k: status[k] for k in
                         ("exists", "familiarity", "n_sample", "n_unfamiliar",
                          "updated_at")},
            "terms": {"available": bool(terms.get("items")),
                      "n": int(terms.get("n") or 0),
                      "source": str(terms.get("source") or "none")},
            "chapter": {"no": int((chapter or {}).get("no") or 0),
                        "name": str((chapter or {}).get("name") or "")},
        })
    return out
