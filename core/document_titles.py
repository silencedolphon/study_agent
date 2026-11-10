"""Display repair for legacy courseware with duplicated embedded metadata titles."""
from collections import Counter
import json
import re
import sqlite3
from pathlib import PurePosixPath


def matches_legacy_root(name, original_title, artifact_deck, *, require_deck=False):
    """Match only a known inherited title (possibly with a generated page suffix)."""
    name = str(name or '').strip()
    old = str(original_title or '').strip()
    deck = str(artifact_deck or '').strip()
    if not name or not old or (require_deck and deck != old) or (deck and deck != old):
        return False
    if name == old:
        return True
    if deck != old or not name.startswith(old):
        return False
    suffix = name[len(old):]
    return bool(re.fullmatch(r'[（(]\s*共\s*\d+\s*(?:页\s*)?[）)]?\s*', suffix))


def display_titles(conn):
    """Return corrected display titles only when duplicate metadata proves the issue.

    Does not modify the library or old artifacts. A manually renamed title is
    protected by its marker. Unique or ambiguous names remain unchanged.
    """
    try:
        rows = [dict(row) for row in conn.execute(
            "SELECT id, title, path, kind, extra FROM documents")]
    except sqlite3.Error:
        return {}
    counts = Counter(str(r.get('title') or '').strip() for r in rows)
    stems = {r['id']: PurePosixPath(str(r.get('path') or '').replace('\\', '/')).stem.strip()
             for r in rows}
    stem_counts = Counter(s.casefold() for s in stems.values() if s)
    result = {}
    for row in rows:
        title = str(row.get('title') or '').strip()
        stem = stems[row['id']]
        if counts[title] < 2 or not title or not stem or stem_counts[stem.casefold()] != 1:
            continue
        if str(row.get('kind') or '').lower() not in ('pdf', 'ppt', 'pptx'):
            continue
        try:
            meta = json.loads(row.get('extra') or '{}')
        except (ValueError, TypeError):
            continue
        if not isinstance(meta, dict) or meta.get('title_manually_renamed'):
            continue
        if str(meta.get('title') or '').strip() != title:
            continue
        result[int(row['id'])] = stem
    return result
