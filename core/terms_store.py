"""Shared atomic storage helpers for independently produced courseware terms."""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path


_LOCKS_GUARD = threading.Lock()
_LOCKS: dict[str, threading.RLock] = {}


def _lock(path: Path) -> threading.RLock:
    key = str(Path(path).resolve()).casefold()
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, threading.RLock())


def read(path: str | Path) -> dict:
    """Read a term artifact; missing files are an empty artifact, malformed files fail."""
    target = Path(path)
    if not target.is_file():
        return {"meta": {}, "terms": []}
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"名词解释产物无法读取，未覆盖原文件：{exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("terms", []), list):
        raise ValueError("名词解释产物格式异常，未覆盖原文件")
    data["meta"] = data.get("meta") if isinstance(data.get("meta"), dict) else {}
    data["terms"] = [x for x in data.get("terms", []) if isinstance(x, dict)]
    return data


def write(path: str | Path, data: dict) -> None:
    """Atomically replace an artifact without creating a backup."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    lock = _lock(target)
    with lock:
        tmp = target.with_name(target.name + f".tmp-{os.getpid()}-{threading.get_ident()}")
        try:
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, target)
        finally:
            if tmp.exists():
                tmp.unlink()


def mutate(path: str | Path, transform) -> dict:
    """Read, transform, and atomically write under the artifact's process lock."""
    target = Path(path)
    with _lock(target):
        current = read(target)
        updated = transform(current)
        if not isinstance(updated, dict):
            raise ValueError("名词产物更新函数没有返回对象")
        write(target, updated)
        return updated


def replace_source(path: str | Path, source: str, rows: list[dict],
                   meta_updates: dict | None = None, *, legacy_meta_sources=()) -> dict:
    """Replace one pipeline's rows while retaining terms created by other pipelines."""
    target = Path(path)
    lock = _lock(target)
    with lock:
        current = read(target)
        old_meta = current.get("meta") or {}
        legacy_sources = {str(x) for x in legacy_meta_sources}
        old_source = str(old_meta.get("source") or "")
        kept = []
        for row in current.get("terms") or []:
            row_source = str(row.get("source") or "")
            if row_source == source or (not row_source and old_source in legacy_sources):
                continue
            kept.append(row)
        # A user-edited manual row is authoritative for that term. A later
        # pipeline refresh must not create a duplicate beside the manual row.
        manual_keys = {"".join(str(row.get("term") or "").split()).casefold()
                       for row in kept if str(row.get("source") or "") == "manual"}
        new_rows = [dict(row, source=source) for row in rows
                    if "".join(str(row.get("term") or "").split()).casefold() not in manual_keys]
        combined = kept + new_rows
        meta = dict(old_meta)
        meta.update(meta_updates or {})
        source_meta = dict(old_meta.get("source_meta") or {})
        source_meta[source] = dict(meta_updates or {})
        meta["source_meta"] = source_meta
        meta["terms_override"] = True
        data = {**current, "meta": meta, "terms": combined}
        write(target, data)
        return data
