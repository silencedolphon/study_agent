"""复习流程后端（C-0127，2026-10-23）：复习前自测 + 三步复习数据。

用户需求（2026-10-23）：
  1) 复习前自测（v3，2026-10-24 改）：随机抽课件**一半**知识点，逐个展示
     "从次一级节点到该 KP 本身"的**结构路径**（可展开看总结），用户点
     「已掌握/未掌握」→ 得该课件熟悉度（持久化，供遗忘曲线月历/进度悬浮窗用）。
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
| `GET /api/review/selftest?doc_id=` | `start_selftest(doc_id, db_path=None)`（**不需要 conn**，锁外） | `{ok,doc_id,title,session_id,n_total,n_sample,ttl_seconds,kps[],items:[{kp_id,name,node_path[],branch:[{name,level}],summary,unfamiliar}],note}`（v3 **无 segments/answers**） |
| `POST /api/review/selftest/submit` | `submit_selftest(doc_id,session_id,results)`（**锁外**，要写 `logs/review/`；`results={kp_id:bool}`，True=已掌握） | `{ok,familiarity,n_sample,n_total,n_mastered,n_unmastered,unfamiliar_ids,results[]}` |
| `GET /api/review/selftest/status?doc_id=` | `selftest_status(doc_id)`（**不需要 conn**，锁外） | `{ok,exists,familiarity,n_sample,n_total,n_mastered,n_unmastered,unfamiliar_ids,updated_at,legacy,kps:{kp_id:{mastered}}}` |

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
import secrets
import sqlite3
import threading
import time
from pathlib import Path

from . import config
from . import study

REVIEW_VERSION = "C-0128-review-v3"
REVIEW_SUFFIX = "-自测.json"
SESSION_TTL = 1800            # 自测会话 30 分钟（进程内存，过期需重新开始）


# 进程内会话：session_id -> {doc_id,title,created,items:[{kp_id,name,segments,answers}],...}
_SELFTEST: dict[str, dict] = {}
_SESS_LOCK = threading.RLock()


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
    """持久化结果 → **自洽**摘要：一切计数都从 `kps` 现算（v3 口径）。

    v3（2026-10-24）起 `kps` 是 `{kp_id:{mastered:bool}}`，只含**本次抽样**；
    旧 v1/v2 文件里可能是 `familiar`（挖空时代）→ 统一按 mastered 推算并标 `legacy`。
    `familiarity == n_mastered/n_sample`、`n_unmastered == len(unfamiliar_ids)` 恒成立。
    """
    if not data:
        return {"exists": False, "familiarity": 0.0, "n_sample": 0,
                "n_mastered": 0, "n_unmastered": 0, "n_familiar": 0,
                "n_unfamiliar": 0, "n_total": 0, "updated_at": "",
                "unfamiliar_ids": [], "legacy": False}
    meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
    kps = data.get("kps") if isinstance(data.get("kps"), dict) else {}
    unmastered = []
    for kid, v in kps.items():
        v = v if isinstance(v, dict) else {}
        if "mastered" in v:
            m = bool(v.get("mastered"))
        else:                                   # v1/v2 legacy
            m = bool(v.get("familiar"))
        if not m:
            unmastered.append(str(kid))
    n_sample = len(kps)
    n_mastered = n_sample - len(unmastered)
    return {
        "exists": True,
        "familiarity": round(n_mastered / n_sample, 4) if n_sample else 0.0,
        "n_sample": n_sample,
        "n_mastered": n_mastered,
        # 过渡别名：前端旧字段（n_familiar/n_unfamiliar）仍可用，含义同 mastered
        "n_familiar": n_mastered,
        "n_unmastered": len(unmastered),
        "n_unfamiliar": len(unmastered),
        "n_total": int(meta.get("n_total") or 0),
        "updated_at": str(meta.get("generated_at") or ""),
        "unfamiliar_ids": unmastered,
        "legacy": str(meta.get("version") or "") != REVIEW_VERSION,
    }


def selftest_status(doc_id: int) -> dict:
    """读已持久化的自测结果；从未测过 → `{ok:true,exists:false,...}`（v3）。"""
    data = _read_selftest(doc_id)
    summary = _selftest_summary(data)
    kps = data.get("kps") if isinstance(data.get("kps"), dict) else {}
    out_kps = {}
    for k, v in kps.items():
        v = v if isinstance(v, dict) else {}
        m = bool(v.get("mastered", v.get("familiar", False)))
        out_kps[str(k)] = {"mastered": m, "familiar": m}   # familiar 过渡别名
    return {"ok": True, "doc_id": int(doc_id), **summary, "kps": out_kps}


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


# ---------------------------------------------------------------- 结构路径题面（v3）
def _branch_of(kp: dict) -> list:
    """从"次一级节点"到该 KP 本身的结构路径：`[{name, level}]`。

    `node_path` 是 KP 产物里从课程/章节根到叶子的路径（例：
    `["3. 具有神经节阻断…（共", "胆碱受体阻断药分类", "受体选择性与作用部位"]`）。
    展示时跳过最顶层根（课件/章标题），至少包含父级→自身；叶子不是 KP 名称时补上名称。
    """
    name = str(kp.get("name") or kp.get("node_name") or "").strip()
    path = [str(x).strip() for x in (kp.get("node_path") or []) if str(x).strip()]
    # len>=3 时跳过最顶层课程/章根；len==2 时两者就是"父级→自身"，都保留
    nodes = path[1:] if len(path) >= 3 else list(path)
    if not nodes:
        nodes = [name]
    elif not name or nodes[-1] != name:
        nodes = nodes + [name]
    return [{"name": n, "level": i + 1} for i, n in enumerate(nodes)]


def _make_item(kp: dict) -> dict:
    """v3 自测题面：结构路径 + 名称 + 可展开的总结（不再挖空/不返回答案）。"""
    name = str(kp.get("name") or kp.get("node_name") or "").strip()
    return {"kp_id": str(kp.get("id") or ""), "name": name,
            "node_path": [str(x) for x in (kp.get("node_path") or [])],
            "branch": _branch_of(kp),
            "summary": str(kp.get("summary") or "").strip()}


def _prune_sessions() -> None:
    now = time.time()
    for sid in [s for s, v in _SELFTEST.items()
                if now - float(v.get("created") or 0) > SESSION_TTL]:
        _SELFTEST.pop(sid, None)

def _scope():
    return (str(Path(config.DB_PATH).resolve()), str(Path(config.LOG_DIR).resolve()))

def _exclusions_file(doc_id):
    return review_dir() / ('%d-自测排除.json' % int(doc_id))

def _exclusions(doc_id, path=None):
    value = _load_json(path or _exclusions_file(doc_id)).get('ids') or []
    return {str(x) for x in value} if isinstance(value, list) else set()

def update_selftest_exclusion(doc_id, session_id, kp_id='', restore=False):
    """排除仅影响本课件未来自测；复习、导图和原产物保持不变。"""
    with _SESS_LOCK:
        _prune_sessions()
        sess = _SELFTEST.get(str(session_id))
        if not sess or sess['doc_id'] != int(doc_id) or sess.get('scope') != _scope():
            return {'ok': False, 'error': '自测会话已失效或学科已切换，请重新开始', '_code': 400}
        exclusion_path = Path(sess['exclusion_path'])
        removed = _exclusions(doc_id, exclusion_path)
        if restore:
            removed.clear()
        else:
            kp_id = str(kp_id)
            if kp_id not in sess['kp_ids']:
                return {'ok': False, 'error': '知识点不在本次自测中', '_code': 400}
            removed.add(kp_id)
        _atomic_write_json({'ids': sorted(removed)}, exclusion_path)
        eligible = [it for it in sess['pool'] if it['kp_id'] not in removed]
        current = [kid for kid in sess['kp_ids'] if kid not in removed]
        target = min((max(1, math.ceil(len(eligible) / 2)) if eligible else 0)
                     if restore else sess['target'], len(eligible))
        for item in eligible:
            if len(current) >= target:
                break
            if item['kp_id'] not in current:
                current.append(item['kp_id'])
        sess['kp_ids'] = current
        if restore:
            sess['target'] = target
        sess['n_total'] = len(eligible)
        sess['n_sample'] = len(current)
        lookup = {it['kp_id']: it for it in eligible}
        return {'ok': True, 'items': [lookup[k] for k in current],
                'n_total': len(eligible), 'n_sample': len(current), 'n_excluded': len(removed)}


# ---------------------------------------------------------------- ① 自测
def start_selftest(doc_id: int, *, db_path=None, rng=None) -> dict:
    """抽一半知识点 → 每个展示**结构路径 + 名称 + 总结**（v3：不挖空/不返回答案）。

    会话在进程内存（TTL 30min），只存本次抽中的 kp_id；开始不写盘、不调 AI。
    返回 items 里 `branch` 至少含父级→自身，`summary` 供展开阅读；
    前端对每条点「已掌握/未掌握」后，把 `{kp_id: bool}` 提交给 submit。
    """
    doc_id = int(doc_id or 0)
    if doc_id <= 0:
        return {"ok": False, "error": "doc_id 无效", "_code": 400}
    path, kps, meta = _kp_artifact(doc_id)
    if not kps:
        return {"ok": False, "error": "这份课件还没有末级知识点产物（先整理知识点）",
                "_code": 400}
    rng = rng or random.Random()
    excluded = _exclusions(doc_id)
    n_total = sum(str(kp.get('id') or '') not in excluded for kp in kps)
    n_sample = max(1, min(n_total, int(math.ceil(n_total / 2.0)))) if n_total else 0
    idx = list(range(len(kps)))
    rng.shuffle(idx)
    prior = _read_selftest(doc_id)
    prior_kps = prior.get("kps") if isinstance(prior.get("kps"), dict) else {}
    prior_mastered = {}
    for kid, v in prior_kps.items():
        v = v if isinstance(v, dict) else {}
        prior_mastered[str(kid)] = bool(v.get("mastered", v.get("familiar", False)))
    items = []
    for i in idx:
        item = _make_item(kps[i])
        item["unfamiliar"] = (str(item["kp_id"]) in prior_mastered
                              and not prior_mastered[str(item["kp_id"])])
        items.append(item)
    pool = items
    items = [it for it in pool if it['kp_id'] not in excluded][:n_sample]
    sid = secrets.token_hex(8)
    title = _doc_title_for(doc_id, db_path)
    with _SESS_LOCK:
        _prune_sessions()
        _SELFTEST[sid] = {"doc_id": doc_id, "title": title,
                          "created": time.time(), "n_total": n_total,
                          "n_sample": n_sample,
                          "kp_ids": [it["kp_id"] for it in items],
                          "scope": _scope(), "pool": pool, "target": n_sample,
                          "output_path": str(selftest_out_file(doc_id).resolve()),
                          "exclusion_path": str(_exclusions_file(doc_id).resolve()),
                          "artifact": str(path)}
    return {"ok": True, "doc_id": doc_id, "title": title,
            "session_id": sid, "n_total": n_total, "n_sample": n_sample,
            "ttl_seconds": SESSION_TTL,
            "kps": [{"kp_id": it["kp_id"], "name": it["name"],
                     "unfamiliar": bool(it.get("unfamiliar"))} for it in items],
            "items": items, "n_excluded": len(excluded),
            "note": "逐个看结构路径与总结，点「已掌握/未掌握」；提交后熟悉度=已掌握/抽样"}


def _doc_title_for(doc_id: int, db_path=None) -> str:
    """不传 conn 时的标题：kp_build 的库内标题（只读 SQLite）。"""
    try:
        return _kb().doc_title_v2(int(doc_id), db_path)
    except Exception:  # noqa: BLE001
        return "文档 %d" % int(doc_id)


def _as_mastered(v) -> bool:
    """把前端 results 的值转 bool：True/False、'true'/'false'、1/0 都认。"""
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    s = str(v).strip().lower()
    return s in ("1", "true", "yes", "y", "mastered", "已掌握", "掌握")


def submit_selftest(doc_id: int, session_id: str, results) -> dict:
    # 保持会话校验、结果落盘和移除会话的原子性；不阻塞全局看板锁。
    with _SESS_LOCK:
        return _submit_selftest_locked(doc_id, session_id, results)


def _submit_selftest_locked(doc_id: int, session_id: str, results) -> dict:
    """提交「已掌握/未掌握」并持久化（v3）：`results={kp_id: bool}`。

    - `mastered=True` 表示已掌握；**抽样里没提交的 KP 按未掌握算**（不假装会）。
    - `familiarity = n_mastered / n_sample`；只写本次抽中的 KP（不合并历史）。
    - 兼容 `results` 为「已掌握 kp_id 列表」。
    """
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
        if sess.get('scope') != _scope():
            return {"ok": False, "error": "学科已切换，请重新开始自测", "_code": 400}
        if not sess.get('kp_ids'):
            return {"ok": False, "error": "没有可提交的知识点，请先恢复排除项", "_code": 400}
        if set(sess['kp_ids']) & _exclusions(doc_id, Path(sess['exclusion_path'])):
            return {"ok": False, "error": "其他窗口已调整排除项，请重新开始自测", "_code": 400}
        kp_ids = list(sess.get("kp_ids") or [])
        title = sess.get("title") or ""
        n_total = int(sess.get("n_total") or 0)
        n_sample = int(sess.get("n_sample") or 0)
        output_path = Path(sess['output_path'])
    if isinstance(results, dict):
        picked = {str(k): _as_mastered(v) for k, v in results.items()}
    elif isinstance(results, (list, tuple, set)):
        picked = {str(k): True for k in results}
    else:
        return {"ok": False, "error": "results 必须是 {kp_id: true/false} 或 id 数组",
                "_code": 400}
    out_results = []
    for kid in kp_ids:
        mastered = bool(picked.get(str(kid), False))
        out_results.append({"kp_id": str(kid), "name": "", "mastered": mastered,
                            "familiar": mastered})   # familiar 过渡别名
    n_mastered = sum(1 for r in out_results if r["mastered"])
    familiarity = round(n_mastered / n_sample, 4) if n_sample else 0.0
    unfamiliar_ids = [r["kp_id"] for r in out_results if not r["mastered"]]
    generated_at = time.strftime("%Y-%m-%dT%H:%M:%S")
    # v3：kps 只保留本次抽样（{kp_id:{mastered}}），与 meta 的本次计数严格自洽。
    current = {r["kp_id"]: {"mastered": r["mastered"]} for r in out_results}
    payload = {
        "meta": {"version": REVIEW_VERSION, "doc_id": doc_id, "title": title,
                 "generated_at": generated_at, "created_at": time.time(),
                 "session_id": sid, "n_total": n_total, "n_sample": n_sample,
                 "n_mastered": n_mastered,
                 "n_unmastered": len(unfamiliar_ids),
                 "n_familiar": n_mastered,             # 过渡别名
                 "n_unfamiliar": len(unfamiliar_ids),
                 "unfamiliar_ids": list(unfamiliar_ids),
                 "familiarity": familiarity,
                 "rule": "mastered/n_sample"},
        "kps": current,
    }
    _atomic_write_json(payload, output_path)
    with _SESS_LOCK:
        _SELFTEST.pop(sid, None)
    return {"ok": True, "doc_id": doc_id, "familiarity": familiarity,
            "n_sample": n_sample, "n_total": n_total,
            "n_mastered": n_mastered, "n_unmastered": len(unfamiliar_ids),
            "n_familiar": n_mastered, "n_unfamiliar": len(unfamiliar_ids),
            "unfamiliar_ids": unfamiliar_ids, "generated_at": generated_at,
            "results": out_results}


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
    has_selftest = bool(status.get("exists"))
    mastered_map = {str(k): bool((v or {}).get("mastered"))
                    for k, v in (status.get("kps") or {}).items()}
    # 只标"本次抽样且未掌握"的 KP；没抽中的不假装未掌握（无标记）
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
            "mastered": bool(mastered_map.get(kid, False)) if has_selftest else False,
            "unfamiliar": bool(kid in unfamiliar),
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
             "n": len(kp_out), "done": sum(1 for x in kp_out if x["mastered"])},
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
                         ("exists", "familiarity", "n_sample", "n_mastered",
                          "n_unmastered", "updated_at", "legacy")},
            "terms": {"available": bool(terms.get("items")),
                      "n": int(terms.get("n") or 0),
                      "source": str(terms.get("source") or "none")},
            "chapter": {"no": int((chapter or {}).get("no") or 0),
                        "name": str((chapter or {}).get("name") or "")},
        })
    return out
