"""Stable, versioned locations for document page images."""
from __future__ import annotations

import re
from pathlib import Path


def directory(log_dir, doc_id, content_hash) -> Path | None:
    """A document's source-version directory, or None for an invalid identity."""
    try:
        did = int(doc_id)
    except (TypeError, ValueError):
        return None
    digest = str(content_hash or "").strip().lower()
    if did <= 0 or not re.fullmatch(r"[0-9a-f]{32,64}", digest):
        return None
    return Path(log_dir) / "figure_desc" / ("doc-%d-%s" % (did, digest[:32]))


def document_directories(log_dir, doc_id) -> list[Path]:
    """Existing versioned directories for one document, excluding lookalike names."""
    try:
        did = int(doc_id)
    except (TypeError, ValueError):
        return []
    if did <= 0:
        return []
    root = Path(log_dir) / "figure_desc"
    rule = re.compile(r"^doc-%d-[0-9a-f]{32}$" % did)
    try:
        return sorted(p for p in root.glob("doc-%d-*" % did)
                      if p.is_dir() and rule.fullmatch(p.name))
    except OSError:
        return []
