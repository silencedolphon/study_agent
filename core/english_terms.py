"""Display helpers for English names emitted without reliable word boundaries."""
from __future__ import annotations

import re

try:
    import wordninja
except ImportError:  # Keep the dashboard available if dependencies were not installed yet.
    wordninja = None


_LATIN_RUN = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)?")
_SPACE = re.compile(r"\s+")


def _split_run(value: str) -> str:
    # Keep acronyms, mixed-case identifiers (e.g. Cmax), and short words intact.
    if (len(value) < 8 or value.isupper()
            or any(ch.isupper() for ch in value[1:])
            or wordninja is None):
        return value
    parts = wordninja.split(value.lower())
    if len(parts) < 2 or "".join(parts).casefold() != value.casefold():
        return value
    return " ".join(parts)


def segment_english(value) -> str:
    """Insert likely English word boundaries without rewriting source artifacts."""
    text = _SPACE.sub(" ", str(value or "")).strip()
    if not text:
        return ""
    return _LATIN_RUN.sub(lambda match: _split_run(match.group(0)), text)
