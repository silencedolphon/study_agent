"""Filter textbook-chapter terms against their manually linked courseware KPs.

This is an explicit, local-only outlet filter. It reads the terms from the linked
textbook knowledge-point summary, scores each definition against the courseware's
dedicated KP vector index, and replaces only its own textbook-derived rows in the
courseware terms artifact after backing up the previous file.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import time
from pathlib import Path

from . import config, deck_chapter, english_terms, kp_build, kp_vector, terms_store


DEFAULT_THRESHOLD = 0.62
PIPELINE_VERSION = "SA-LAB-TEXTBOOK-TERMS-FILTER-01"


def _load_json(path: Path) -> dict:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _atomic_write(data: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp-" + str(os.getpid()))
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def _backup(path: Path) -> str:
    if not path.exists():
        return ""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = path.with_name(path.name + ".bak-" + stamp)
    suffix = 1
    while backup.exists():
        backup = path.with_name(path.name + ".bak-" + stamp + "-" + str(suffix))
        suffix += 1
    shutil.copy2(path, backup)
    return str(backup)


def _output_path(doc_id: int) -> Path:
    return Path(kp_build.terms_out_file(int(doc_id)))


def _clean_candidates(raw_terms) -> list[dict]:
    if not isinstance(raw_terms, list):
        return []
    out, seen = [], set()
    for raw in raw_terms:
        if not isinstance(raw, dict):
            continue
        term = str(raw.get("term") or raw.get("name") or "").strip()[:200]
        definition = str(raw.get("definition") or raw.get("def")
                         or raw.get("summary") or "").strip()[:4000]
        if not term or not definition:
            continue
        key = re.sub(r"\s+", "", term).casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append({"term": term,
                    "en": str(raw.get("en") or raw.get("english") or "").strip()[:200],
                    "definition": definition,
                    "source_ids": raw.get("source_ids") if isinstance(raw.get("source_ids"), list) else []})
    return out


def _context(conn, doc_id: int) -> dict:
    try:
        doc_id = int(doc_id or 0)
    except (TypeError, ValueError):
        raise ValueError("doc_id 必须是整数") from None
    if doc_id <= 0:
        raise ValueError("缺少课件 doc_id")

    row = conn.execute("SELECT title,role FROM documents WHERE id=?", (doc_id,)).fetchone()
    if not row or str(row["role"] or "") != "courseware":
        raise ValueError("请选择已入库的课件")

    relation = deck_chapter.get(conn, doc_id)
    binding = relation.get("binding") if isinstance(relation, dict) else None
    if not isinstance(binding, dict):
        raise ValueError("这份课件还没有关联教材章节；请先在知识点窗口里选择对应教材章节")
    if relation.get("summary_stale"):
        raise ValueError("关联教材章节的知识点总结已过期，请先重新整理该章再筛选名词")
    candidates = _clean_candidates(relation.get("terms"))
    if not candidates:
        raise ValueError("关联章节没有可筛选的教材名词解释（教材知识点总结尚未完成或本章没有名词解释）")

    kp_path = Path(kp_build.out_file(doc_id))
    kp_artifact = _load_json(kp_path)
    kps = [item for item in (kp_artifact.get("knowledge_points") or [])
           if isinstance(item, dict)]
    if not kps:
        raise ValueError("这份课件还没有知识点总结产物，请先整理课件知识点")
    return {"doc_id": doc_id, "title": str(row["title"] or ""),
            "binding": binding, "candidates": candidates,
            "kp_path": kp_path, "kps": kps,
            "summary_stale": bool(relation.get("summary_stale"))}


def options(conn, doc_id: int) -> dict:
    """Read-only UI state for the separately launched terms filter."""
    context = _context(conn, doc_id)
    binding = context["binding"]
    return {"ok": True, "doc_id": context["doc_id"],
            "courseware_title": context["title"],
            "binding": binding,
            "n_candidates": len(context["candidates"]),
            "n_courseware_kps": len(context["kps"]),
            "summary_stale": context["summary_stale"],
            "threshold": DEFAULT_THRESHOLD,
            "method": "dedicated_kp_vector_max_cosine",
            "calls": 0, "cost_cny": 0.0}


def filter_terms(conn, doc_id: int, threshold=DEFAULT_THRESHOLD, *, log=None) -> dict:
    """Keep terms whose highest courseware-KP cosine is strictly above threshold."""
    try:
        threshold = float(threshold)
    except (TypeError, ValueError):
        raise ValueError("关联度阈值必须是 0 到 1 之间的数字") from None
    if not 0.0 <= threshold <= 1.0:
        raise ValueError("关联度阈值必须在 0 到 1 之间")

    context = _context(conn, doc_id)
    did = context["doc_id"]
    kp_path = context["kp_path"]
    # Keep the dedicated vector table aligned with the selected courseware's
    # current summary. Existing vectors are reused by sync_artifact.
    sync = kp_vector.sync_artifact(did, kp_path)
    if sync.get("status") != "ok" or int(sync.get("indexed") or 0) <= 0:
        raise ValueError("该课件的知识点向量库没有可评分的知识点")

    candidates = context["candidates"]
    queries = ["\n".join(part for part in (item["term"], item["en"],
                                              item["definition"]) if part)
               for item in candidates]
    nearest = kp_vector.search_many(queries, top_k=1, doc_id=did)
    artifact_kps = context["kps"]
    by_uid = {}
    for kp in artifact_kps:
        uid = str(kp.get("id") or kp.get("uid") or "").strip()
        if uid:
            by_uid[uid] = kp
    by_name = {}
    for kp in artifact_kps:
        name = str(kp.get("name") or kp.get("title") or "").strip()
        if name:
            by_name.setdefault(name, kp)

    selected, dropped = [], []
    for item, hits in zip(candidates, nearest):
        hit = hits[0] if hits else {}
        score = float(hit.get("score") or 0.0) if hit else -1.0
        kp = by_uid.get(str(hit.get("kp_uid") or "")) or by_name.get(str(hit.get("name") or ""))
        kp_id = str((kp or {}).get("id") or (kp or {}).get("uid") or "").strip()
        kp_name = str((kp or {}).get("name") or (kp or {}).get("title")
                      or hit.get("name") or "").strip()
        page_values = hit.get("pages") or (kp or {}).get("pages") or []
        if not isinstance(page_values, list):
            page_values = [page_values]
        page_numbers = []
        for value in page_values:
            try:
                page = int(value)
            except (TypeError, ValueError):
                continue
            if page > 0:
                page_numbers.append(page)
        first_page = min(page_numbers) if page_numbers else 0
        if score > threshold:
            selected.append({
                "term": item["term"], "en": english_terms.segment_english(item["en"]),
                "en_source": "textbook_chapter",
                "def": item["definition"], "why": "教材章节知识点名词；通过课件知识点关联度筛选",
                "first_page": first_page, "first_kp_id": kp_id, "first_kp_name": kp_name,
                "kp_ids": [kp_id] if kp_id else [], "n_occurrences": 1,
                "relevance_score": round(score, 6),
                "relevance_kp_id": kp_id, "relevance_kp_name": kp_name,
                "source": "textbook_chapter", "source_ids": item["source_ids"],
            })
        else:
            dropped.append({"term": item["term"], "score": round(score, 6),
                            "kp_id": kp_id, "kp_name": kp_name})

    binding = context["binding"]
    generated_at = time.strftime("%Y-%m-%dT%H:%M:%S")
    output = _output_path(did)
    meta = {
        "pipeline": "教材章节名词 → 关联课件知识点专属向量库 → 余弦阈值筛选（本地零 AI）",
        "pipeline_version": PIPELINE_VERSION,
        "source": "textbook_chapter_kp",
        "doc_id": did,
        "courseware_title": context["title"],
        "textbook_doc_id": int(binding.get("book_id") or 0),
        "textbook_title": str(binding.get("book_title") or ""),
        "chapter": int(binding.get("chapter") or 0),
        "chapter_name": str(binding.get("chapter_name") or ""),
        "generated_at": generated_at,
        "created_at": time.time(),
        "n_candidates": len(candidates), "n_selected": len(selected),
        "n_dropped": len(dropped),
        "filter": {"method": "dedicated_kp_vector_max_cosine",
                   "threshold": threshold, "operator": ">",
                   "rule": "仅保留与该课件任一知识点的最高余弦相似度严格大于阈值的教材名词",
                   "vector_model": str(config.EMBED_MODEL_REPO)},
        "dropped_terms": dropped,
        "usage_total": {"calls": 0, "tokens_in": 0, "tokens_out": 0},
        "cost_cny": 0.0,
        "status": "done" if selected else "no_relevant_terms",
    }
    backup = _backup(output)
    if backup:
        meta["prev_backup"] = backup
    terms_store.replace_source(output, "textbook_chapter", selected, meta,
                               legacy_meta_sources=("textbook_chapter_kp", "textbook_chapter"))
    result = {"ok": True, "status": meta["status"], "doc_id": did,
              "binding": binding, "threshold": threshold,
              "n_candidates": len(candidates), "n_selected": len(selected),
              "n_dropped": len(dropped), "selected": selected,
              "dropped": dropped, "out_path": str(output), "backup": backup,
              "vector_sync": sync, "calls": 0, "cost_cny": 0.0}
    if log:
        log("教材名词关联筛选：doc#%d 第%d章 · 候选 %d → 保留 %d / 舍弃 %d（阈值 > %.2f）"
            % (did, int(binding.get("chapter") or 0), len(candidates),
               len(selected), len(dropped), threshold))
    return result
