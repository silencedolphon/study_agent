"""预习名词驱动的课件「名词解释」整理（C-0129，2026-10-24，**零 AI**）。

用户新要求（复习板块改造第③条）："名词整理重做——参考预习页签 key_terms 的高质量做法，
减少无关名词"。本模块**不走**旧"逐段 AI 提炼 + 一次全局筛选"的老路，而是：

  ① 课件 → 教材章：读 supplement `meta.chapter_map`，没有则
     `core/teach.py::map_courseware_chapter(doc_id, allow_ai=False)` 本地兜底（0 成本）；
  ② 读**教材预习产物** `<教材id>-预习总结-第<章>.json` 的 `terms`（预习页签 key_terms 同源，
     已按"必须出自本章材料"筛过一轮）；
  ③ 用**该课件自己的语料**（KP 的 name/points/summary + chunks 原文）做相关度过滤：
     - `term` 原文出现在语料 → 保留（最强证据）；
     - 否则字符 n-gram（2–3 字片段）在语料里的覆盖率 ≥ 0.60 → 保留；
     - 否则本地嵌入（`core/embed.get_embedder()`）`term+def` 与该课件每条 KP 文本的
       max cosine ≥ 0.62 → 保留；
     - 都不满足 → **剔除**（宁缺毋滥，可以为 0 个）。
  ④ 写 `logs/knowledge/<doc>-名词解释.json`（`{meta,terms}`，字段与旧产物兼容），
     `meta.source="preview_key_terms"`；覆盖旧文件前自动 `.bak-<ts>`；`calls=0 / cost=0`。

无预习产物 → `{ok:false,source:"none"}`（**不自动调用旧 AI 管线**，由用户显式选择回退）。
"""
from __future__ import annotations

import json
import math
import os
import re
import shutil
import sqlite3
import time
import unicodedata
from pathlib import Path

from . import config

TERMS_PREVIEW_VERSION = "C-0129-terms-preview-v1"
TERMS_FILTER_VERSION = "C-0129-terms-filter-v1"
NGRAM_MIN_COVERAGE = 0.60          # term 的 2–3 字片段在语料中的覆盖率阈值
EMBED_MIN_COSINE = 0.62            # 本地嵌入 term+def vs KP 文本的 max cosine 阈值
NGRAM_SIZES = (2, 3)
MAX_CORPUS_CHARS = 400_000         # 只防极端大课件；doc46/50 远小于此
TERMS_PROMPT_NAME = "提示词-名词解释.md"


def terms_out_file(doc_id: int, out_path=None) -> Path:
    """课件名词解释产物：`logs/knowledge/<doc_id>-名词解释.json`（与旧产物同路径）。"""
    if out_path:
        return Path(out_path)
    return config.LOG_DIR / "knowledge" / ("%d-名词解释.json" % int(doc_id))


# ---------------------------------------------------------------- 基础 IO
def _load_json(path: Path) -> dict:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _atomic_write_json(data: dict, out: Path) -> None:
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, out)


def _backup(out: Path) -> str:
    if not Path(out).exists():
        return ""
    bak = Path(out).with_name(Path(out).name + ".bak-" + time.strftime("%Y%m%d-%H%M%S"))
    try:
        shutil.copy2(out, bak)
        return str(bak)
    except OSError:
        return ""


def _open_db(db_path=None) -> sqlite3.Connection:
    p = Path(db_path) if db_path else Path(config.DB_PATH)
    conn = sqlite3.connect("file:" + p.as_posix() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _kb():
    from . import kp_build
    return kp_build


def _teach():
    from . import teach
    return teach


# ---------------------------------------------------------------- 课件语料
def _kp_artifact(doc_id: int):
    kb = _kb()
    path = kb.out_file(doc_id)
    doc = kb._load_prev(path)
    kps = [k for k in (doc.get("knowledge_points") or []) if isinstance(k, dict)]
    meta = doc.get("meta") if isinstance(doc.get("meta"), dict) else {}
    return path, kps, meta


def _courseware_texts(doc_id: int, kps: list, conn) -> list:
    """课件语料：每条 KP 一段文本 + chunks 原文（全部只读）。

    返回 `[{kp_id,kp_name,text}]`：前 N 条是 KP（供 first_kp 对齐），末尾 chunk 语料
    只并进"全局命中"判断（不参与 first_kp 关联）。
    """
    out = []
    for kp in kps:
        parts = [str(kp.get("name") or kp.get("node_name") or "")]
        parts += [str(p) for p in (kp.get("points") or [])]
        if kp.get("summary"):
            parts.append(str(kp["summary"]))
        txt = "。".join(p for p in parts if p).strip()
        if txt:
            out.append({"kp_id": str(kp.get("id") or ""),
                        "kp_name": str(kp.get("name") or kp.get("node_name") or ""),
                        "text": txt})
    try:
        rows = conn.execute("SELECT id, text FROM chunks WHERE doc_id=? ORDER BY ord",
                            (int(doc_id),)).fetchall()
    except sqlite3.Error:
        rows = []
    for r in rows:
        txt = str(r["text"] if hasattr(r, "keys") else r[1] or "").strip()
        if txt:
            out.append({"kp_id": "", "kp_name": "",
                        "text": txt, "chunk_id": int(r["id"] if hasattr(r, "keys") else r[0])})
    return out


# ---------------------------------------------------------------- 章 / 预习产物
def _resolve_chapter(conn, doc_id: int) -> dict:
    """课件→教材章：优先 supplement chapter_map，没有则本地兜底（只读，不写缓存）。"""
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
                "page_label": str(cm.get("page_label") or ""), "source": "supplement_meta"}
    try:
        cache = config.LOG_DIR / "knowledge" / "_terms_map_never_written.json"
        cm = teach.map_courseware_chapter(doc_id, allow_ai=False,
                                          out_path=cache, log=lambda *a, **k: None)
    except Exception:  # noqa: BLE001
        return {}
    if int((cm or {}).get("chapter_no") or 0):
        return {"no": int(cm.get("chapter_no") or 0),
                "name": str(cm.get("name") or cm.get("chapter_name") or ""),
                "page_label": str(cm.get("page_label") or ""), "source": "local_fallback"}
    return {}


def _preview_artifact_path(tb: dict, chapter: dict) -> Path:
    """教材预习产物的路径（旧口径逐字不变：`<tb_id>-预习总结-第N章.json`）。"""
    return (config.LOG_DIR / "knowledge" /
            ("%d-预习总结-第%d章.json" % (int(tb["id"]), int(chapter.get("no") or 0))))


def _preview_artifact(doc_id: int, chapter: dict, conn=None, db_path=None) -> tuple:
    """读教材预习产物 → `(path, terms)`（S08B：只读 JSON 的进程内缓存）。

    缓存键 = `doc_id + 章 + 预习 artifact sha256 + data_revision`：
      · 只缓存**已存在且能解析出候选词**的 JSON（旧 artifact 缺失/损坏 → 逐字走旧路径，
        不缓存空结果，这样文件后来修好/新增时会因 sha 变化自然失效）；
      · revision 缺失/0 → 不缓存（旧库安全回退）；
      · 返回 deepcopy，调用方改 terms 不会污染缓存；
      · `SHARED_MATERIAL_CACHE=0` → 直接走旧路径（不读不写缓存）。
    真实读取/归一化在 `_preview_artifact_uncached()`（旧实现逐字保留）。
    """
    teach = _teach()
    if not teach.shared_material_cache_enabled():
        return _preview_artifact_uncached(doc_id, chapter, conn=conn,
                                          db_path=db_path)
    own = conn is None
    conn = conn or _open_db(db_path)
    try:
        try:
            tb = teach.pick_textbook(conn)
            rev = teach.shared_material_data_revision(conn)
        except Exception:  # noqa: BLE001  （缓存前置条件拿不到就按旧路径）
            tb, rev = None, 0
        if not tb or rev <= 0:
            return _preview_artifact_uncached(doc_id, chapter, conn=conn,
                                              db_path=db_path)
        path = _preview_artifact_path(tb, chapter)
        sha = teach.shared_material_file_sha24(path)
        key = ("terms_preview", int(doc_id), int(tb["id"]),
               int(chapter.get("no") or 0), str(chapter.get("name") or ""),
               int(rev), sha)
        if sha:
            hit = teach.shared_material_cache_get(key)
            if hit is not None:
                return hit
        result = _preview_artifact_uncached(doc_id, chapter, conn=conn,
                                            db_path=db_path)
        if sha and result[1]:
            teach.shared_material_cache_put(key, result)
        else:
            teach.shared_material_cache_note("skip")
        return result
    finally:
        if own:
            conn.close()


def _preview_artifact_uncached(doc_id: int, chapter: dict, conn=None,
                               db_path=None) -> tuple:
    """读教材预习产物 → `(path, terms)`；返回 terms 为 `_clean_terms` 同形字典列表。

    旧实现逐字保留（S08B 缓存未命中/关闭时走这里）：只读 JSON + 归一化，不写产物。
    """
    teach = _teach()
    own = conn is None
    conn = conn or _open_db(db_path)
    try:
        tb = teach.pick_textbook(conn)
    except Exception:  # noqa: BLE001
        tb = None
    finally:
        if own:
            conn.close()
    if not tb:
        return Path(""), [], {}
    p = _preview_artifact_path(tb, chapter)
    data = _load_json(p)
    raw = data.get("terms") if isinstance(data.get("terms"), list) else []
    items, seen = [], set()
    for r in raw:
        if isinstance(r, str):
            r = {"term": r}
        if not isinstance(r, dict):
            continue
        term = str(r.get("term") or r.get("name") or "").strip()
        if not term:
            continue
        key = _norm_term_key(term)
        if key in seen:
            continue
        seen.add(key)
        items.append({"term": term,
                      "en": str(r.get("en") or "").strip(),
                      "def": str(r.get("def") or r.get("定义") or "").strip(),
                      "why": str(r.get("why") or r.get("重要性") or "").strip()})
    pm = data.get("meta") if isinstance(data.get("meta"), dict) else {}
    return p, items, {"textbook_doc_id": int(tb["id"]),
                      "textbook_title": str(tb.get("title") or ""),
                      "preview_meta": pm}


def clear_preview_artifact_cache() -> None:
    """清空 S08B 共享材料缓存（名词侧入口；与教材章候选共用同一进程缓存）。"""
    try:
        _teach().clear_shared_material_cache()
    except Exception:  # noqa: BLE001  （清理失败不影响业务）
        pass


# ---------------------------------------------------------------- 相关度
def _norm(s: str) -> str:
    """归一化：NFKC + 小写 + 去所有空白（专有名词比对用）。"""
    s = unicodedata.normalize("NFKC", str(s or "")).lower()
    return re.sub(r"\s+", "", s)


def _norm_term_key(s: str) -> str:
    return re.sub(r"\s+", "", str(s or "")).casefold()


def _ngrams(term: str) -> set:
    """term 的 2–3 字片段（中文按字符切；英文/数字按 2–3 字符切）。"""
    t = _norm(term)
    out: set = set()
    for n in NGRAM_SIZES:
        if len(t) >= n:
            for i in range(len(t) - n + 1):
                out.add(t[i:i + n])
    return out


def _ngram_coverage(term: str, corpus_norm: str) -> float:
    grams = _ngrams(term)
    if not grams:
        return 0.0
    hit = sum(1 for g in grams if g in corpus_norm)
    return hit / len(grams)


def _embed_scores(term_text: str, kp_texts: list) -> list:
    """`term+def` 与该课件每条 KP 文本的 cosine（本地 embedder；模型不可用返回 []）。

    单独抽出便于测试 monkeypatch（测试**不加载模型**）。
    """
    if not kp_texts:
        return []
    try:
        from . import embed as embed_mod
        eng = embed_mod.get_embedder()
        q = eng.embed_query(term_text)
        docs = eng.embed_documents(kp_texts)
        return [float((docs[i] * q).sum()) for i in range(len(kp_texts))]
    except Exception:  # noqa: BLE001
        return []


def _kp_relevance(term: dict, kps: list, corpus_norm: str) -> dict:
    """给一条候选词算相关度：exact / ngram / embed + first_kp。

    `kps` = `_courseware_texts()` 前段（有 kp_id 的条目）。
    """
    norm_term = _norm(term["term"])
    def_text = ("%s：%s" % (term["term"], term.get("def") or "")).strip("：")
    exact_global = bool(norm_term) and norm_term in corpus_norm
    cov_global = _ngram_coverage(term["term"], corpus_norm)
    embed_used = False
    embed_vec: list = []
    if not exact_global and cov_global < NGRAM_MIN_COVERAGE:
        embed_vec = _embed_scores(def_text, [k["text"] for k in kps])
        embed_used = bool(embed_vec)
    max_embed = max(embed_vec) if embed_vec else 0.0
    per_kp: list = []
    for i, kp in enumerate(kps):
        kp_norm = _norm(kp["text"])
        exact = bool(norm_term) and norm_term in kp_norm
        cov = _ngram_coverage(term["term"], kp_norm)
        emb = float(embed_vec[i]) if i < len(embed_vec) else 0.0
        score = (3.0 if exact else 0.0) + cov + max(0.0, emb) * 2.0
        per_kp.append({"kp_id": kp["kp_id"], "kp_name": kp["kp_name"],
                       "exact": exact, "ngram": round(cov, 3),
                       "embed": round(emb, 4), "score": round(score, 4)})
    best = max(per_kp, key=lambda x: x["score"]) if per_kp else {}
    if best and best["score"] <= 0:
        best = {}
    return {
        "exact": exact_global, "ngram": round(cov_global, 3),
        "embed": round(max_embed, 4), "embed_used": embed_used,
        "keep": bool(exact_global or cov_global >= NGRAM_MIN_COVERAGE
                     or max_embed >= EMBED_MIN_COSINE),
        "first": best, "per_kp": per_kp,
    }


# ---------------------------------------------------------------- 主入口
def build_terms_from_preview(doc_id: int, *, db_path=None, out_path=None,
                             force: bool = False, conn=None, log=print) -> dict:
    """**零 AI**：用预习 key_terms 生成课件名词解释（相关度过滤，宁缺毋滥）。

    返回 `{ok,status,source,n_candidates,n_selected,n_dropped,terms,out_path,
    backup,chapter,embed_used,elapsed_s,calls:0,cost_cny:0,...}`。
    """
    started = time.perf_counter()
    doc_id = int(doc_id or 0)
    if doc_id <= 0:
        return {"ok": False, "error": "doc_id 无效", "_code": 400}
    own = conn is None
    conn = conn or _open_db(db_path)
    try:
        kp_path, kps, kp_meta = _kp_artifact(doc_id)
        if not kps:
            return {"ok": False, "source": "none",
                    "error": "这份课件还没有末级知识点产物（先整理知识点）"}
        chapter = _resolve_chapter(conn, doc_id)
        if not chapter:
            return {"ok": False, "source": "none",
                    "error": "无法定位课件对应的教材章节（补做教材补充或检查教材章映射）"}
        preview_path, candidates, tb_info = _preview_artifact(
            doc_id, chapter, conn=conn, db_path=db_path)
        if not candidates:
            return {"ok": False, "source": "none", "chapter": chapter,
                    "preview_artifact": str(preview_path) if preview_path else "",
                    "hint": ("教材第%d章还没有预习名词产物；先做该章预习（或旧名词解释管线）"
                             "再来重做名词" % int(chapter.get("no") or 0)),
                    "error": "没有可用的预习 key_terms，未生成（也不会自动调 AI）"}
        out = terms_out_file(doc_id, out_path)
        preview_sha = ""
        try:
            preview_sha = _sha256(preview_path.read_bytes())
        except OSError:
            preview_sha = ""
        # 幂等：同版本/同预习产物/同章节且非 force → skipped（0 写）
        if out.exists() and not force:
            prev = _load_json(out)
            pm = prev.get("meta") if isinstance(prev.get("meta"), dict) else {}
            if (str(pm.get("pipeline_version") or "") == TERMS_PREVIEW_VERSION
                    and str(pm.get("preview_sha256") or "") == preview_sha
                    and int((pm.get("chapter") or {}).get("no") or 0) == int(chapter["no"])):
                return {"ok": True, "status": "skipped", "source": "preview_key_terms",
                        "n_candidates": int(pm.get("n_candidates") or 0),
                        "n_selected": int(pm.get("n_selected") or 0),
                        "out_path": str(out), "chapter": chapter,
                        "calls": 0, "cost_cny": 0.0,
                        "elapsed_s": round(time.perf_counter() - started, 3)}

        corpus = _courseware_texts(doc_id, kps, conn)
        kp_texts = [c for c in corpus if c.get("kp_id")]
        corpus_norm = _norm("".join(c["text"] for c in corpus))[:MAX_CORPUS_CHARS]
        selected, dropped = [], []
        embed_used_count = 0
        for term in candidates:
            rel = _kp_relevance(term, kp_texts, corpus_norm)
            if not rel["keep"]:
                dropped.append({"term": term["term"],
                                "exact": rel["exact"], "ngram": rel["ngram"],
                                "embed": rel["embed"]})
                continue
            if rel["embed_used"]:
                embed_used_count += 1
            first = rel["first"] or {}
            kp_ids = [x["kp_id"] for x in rel["per_kp"]
                      if x["exact"] or x["ngram"] > 0]
            if first and first["kp_id"] and first["kp_id"] not in kp_ids:
                kp_ids = [first["kp_id"]] + kp_ids
            selected.append({
                "term": term["term"], "en": term.get("en") or "",
                "en_source": "", "def": term.get("def") or "",
                "why": term.get("why") or "",
                "first_page": 0,
                "first_kp_id": first.get("kp_id") or "",
                "first_kp_name": first.get("kp_name") or "",
                "kp_ids": kp_ids,
                "n_occurrences": max(1, len(kp_ids)),
                "evidence": [],
                "seg_id": "", "seg_name": "",
                "page_from_model": 0,
                "source": "preview_chapter",
                "match": ("exact" if rel["exact"] else
                          ("ngram" if rel["ngram"] >= NGRAM_MIN_COVERAGE else "embed")),
                "match_detail": {"exact": rel["exact"], "ngram": rel["ngram"],
                                 "embed": rel["embed"]},
            })
        generated_at = time.strftime("%Y-%m-%dT%H:%M:%S")
        meta = {
            "pipeline": ("C-0129 预习名词驱动的课件名词解释（core/terms_preview.py）："
                         "课件→教材章→读预习 key_terms→用课件 KP 语料+chunks 做相关度过滤"
                         "（原文命中 / n-gram 覆盖率 / 本地嵌入）→ 只保留相关词；**零 AI**"),
            "pipeline_version": TERMS_PREVIEW_VERSION,
            "source": "preview_key_terms",
            "filter_version": TERMS_FILTER_VERSION,
            "doc_id": doc_id,
            "deck": str(kp_meta.get("deck") or kp_meta.get("chapter") or ""),
            "chapter": dict(chapter),
            "textbook_doc_id": int(tb_info.get("textbook_doc_id") or 0),
            "textbook_title": str(tb_info.get("textbook_title") or ""),
            "preview_artifact": str(preview_path),
            "preview_sha256": preview_sha,
            "generated_at": generated_at,
            "created_at": time.time(),
            "n_candidates": len(candidates),
            "n_selected": len(selected),
            "n_dropped": len(dropped),
            "filter": {"version": TERMS_FILTER_VERSION,
                       "ngram_min_coverage": NGRAM_MIN_COVERAGE,
                       "embed_min_cosine": EMBED_MIN_COSINE,
                       "ngram_sizes": list(NGRAM_SIZES),
                       "corpus": "KP(name/points/summary)+chunks",
                       "rule": "原文命中 OR n-gram覆盖≥%.2f OR 本地embed≥%.2f"
                               % (NGRAM_MIN_COVERAGE, EMBED_MIN_COSINE)},
            "embed": {"used": embed_used_count > 0,
                      "n_terms_used_embed": embed_used_count,
                      "model": str(config.MODEL_DIR), "min_cosine": EMBED_MIN_COSINE},
            "dropped_terms": [d["term"] for d in dropped],
            "terms_fields": ["term", "en", "en_source", "def", "first_page",
                             "first_kp_id", "first_kp_name", "kp_ids",
                             "n_occurrences", "evidence", "seg_id", "seg_name",
                             "page_from_model"],
            "usage_total": {"calls": 0, "tokens_in": 0, "tokens_out": 0},
            "cost_cny": 0.0,
            "price_rule": "0 AI 调用（纯本地过滤）",
            "status": "done" if selected else "no_relevant_terms",
        }
        backup = _backup(out)
        if backup:
            meta["prev_backup"] = backup
        _atomic_write_json({"meta": meta, "terms": selected}, out)
        if log:
            log("名词整理（预习名词）：doc#%d 第%d章 · 候选 %d → 保留 %d / 剔除 %d"
                "（0 AI，%.3fs）" % (doc_id, int(chapter["no"]), len(candidates),
                                    len(selected), len(dropped),
                                    time.perf_counter() - started))
        return {"ok": True, "status": meta["status"], "source": "preview_key_terms",
                "doc_id": doc_id, "chapter": chapter,
                "n_candidates": len(candidates), "n_selected": len(selected),
                "n_dropped": len(dropped),
                "selected": [{"term": t["term"], "first_kp_id": t["first_kp_id"],
                              "match": t["match"]} for t in selected],
                "dropped": [d["term"] for d in dropped],
                "out_path": str(out), "backup": backup,
                "embed_used": embed_used_count > 0,
                "calls": 0, "cost_cny": 0.0,
                "elapsed_s": round(time.perf_counter() - started, 3)}
    finally:
        if own and conn is not None:
            conn.close()


def _sha256(raw: bytes) -> str:
    import hashlib
    return hashlib.sha256(raw).hexdigest()


def terms_preview_status(doc_id: int, *, db_path=None, out_path=None,
                         conn=None) -> dict:
    """名词整理（预习方案）状态：预习名词有无/章/候选数/当前产物是否本方案/可重做。0 调用。"""
    doc_id = int(doc_id or 0)
    if doc_id <= 0:
        return {"ok": False, "error": "doc_id 无效", "_code": 400}
    own = conn is None
    conn = conn or _open_db(db_path)
    try:
        chapter = _resolve_chapter(conn, doc_id)
        preview_path, candidates, tb_info = _preview_artifact(
            doc_id, chapter, conn=conn, db_path=db_path)
        out = terms_out_file(doc_id, out_path)
        data = _load_json(out) if out.exists() else {}
        meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
        is_current = str(meta.get("pipeline_version") or "") == TERMS_PREVIEW_VERSION
        try:
            preview_mtime = preview_path.stat().st_mtime if preview_path.exists() else 0.0
        except OSError:
            preview_mtime = 0.0
        preview_sha = ""
        try:
            preview_sha = _sha256(preview_path.read_bytes())
        except (OSError, FileNotFoundError):
            preview_sha = ""
        can_rebuild = bool(candidates) and (
            not is_current
            or str(meta.get("preview_sha256") or "") != preview_sha)
        return {"ok": True, "doc_id": doc_id,
                "source": "preview_key_terms" if candidates else "none",
                "chapter": chapter,
                "preview": {"exists": preview_path.exists(),
                            "artifact": str(preview_path) if preview_path else "",
                            "n_candidates": len(candidates),
                            "textbook_doc_id": int(tb_info.get("textbook_doc_id") or 0)},
                "current": {"exists": out.exists(), "is_current": is_current,
                            "source": str(meta.get("source") or ""),
                            "n_terms": len(data.get("terms") or []) if data else 0,
                            "generated_at": str(meta.get("generated_at") or ""),
                            "path": str(out)},
                "can_rebuild": can_rebuild,
                "calls": 0, "cost_cny": 0.0}
    finally:
        if own and conn is not None:
            conn.close()
