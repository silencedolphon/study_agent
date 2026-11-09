"""Safely import a trusted study-agent snapshot into a NEW, empty data root.

The snapshot is not modified. This utility does not configure the application to
use the destination; the caller must provide a compatible separate data root.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import json
import os
from pathlib import Path, PureWindowsPath
import shutil
import sqlite3
import tempfile

DATA_DIRS = ("raw", "index", "logs", "notes", "subjects")
PATH_FIELDS = {"path", "prompt_source", "prompt_path", "sections_artifact", "source", "source_pptx", "kp_artifact", "preview_artifact"}
LAYOUT = {"RAW_DIR": "raw", "NOTES_DIR": "notes", "INDEX_DIR": "index", "DB_PATH": "index/study.db", "LOG_DIR": "logs"}
MARKER = '.study-data-root.json'


def ensure_no_links(root: Path) -> None:
    if root.is_symlink():
        raise ValueError("snapshot root is a link")
    for parent, dirs, files in os.walk(root, followlinks=False):
        for name in dirs + files:
            p = Path(parent, name)
            if p.is_symlink() or getattr(p.lstat(), 'st_file_attributes', 0) & 1024:
                raise ValueError(f"snapshot contains a link: {p.relative_to(root)}")


def source_rel(raw: str) -> Path:
    p = PureWindowsPath(raw)
    if not raw or p.is_absolute() or p.drive or '..' in p.parts or not p.parts:
        raise ValueError('unsafe relative document path')
    return Path(*p.parts)


def convert_ref(text: str, stage: Path, target: Path) -> str:
    """Only known original roots; only references to existing snapshot files."""
    p = PureWindowsPath(text)
    if not p.is_absolute() or p.drive.lower() not in ('d:', 'g:'):
        return text
    parts = p.parts
    if len(parts) < 3 or parts[1].lower() != 'study_agent' or '..' in parts:
        return text
    rel = Path(*parts[2:])
    resolved = (stage / rel).resolve()
    if not resolved.is_relative_to(stage.resolve()) or not resolved.is_file():
        return text
    return str(target / rel)


def rewrite_json(obj, stage: Path, target: Path):
    if isinstance(obj, dict):
        return {k: convert_ref(v, stage, target) if k in PATH_FIELDS and isinstance(v, str)
                else rewrite_json(v, stage, target) for k, v in obj.items()}
    if isinstance(obj, list):
        return [rewrite_json(x, stage, target) for x in obj]
    return obj


def ensure_relocated(target: Path) -> bool:
    """Rebind previously imported data after moving its root; never create a DB."""
    target = target.absolute()
    marker = target / MARKER
    if not marker.is_file() or marker.is_symlink():
        raise ValueError('data-root marker is missing; refusing to change an existing library')
    ensure_no_links(target)
    meta = json.loads(marker.read_text(encoding='utf-8'))
    if meta.get('version') != 1 or not isinstance(meta.get('root'), str):
        raise ValueError('invalid data-root marker')
    old = Path(meta['root'])
    if not old.is_absolute():
        raise ValueError('invalid former data-root path')
    if old == target:
        return False
    libraries = [(target / 'index' / 'study.db', Path('raw'))]
    subjects = target / 'subjects'
    if subjects.is_dir():
        libraries.extend((p / 'index' / 'study.db', Path('subjects') / p.name / 'raw')
                         for p in subjects.iterdir() if p.is_dir())
    found = 0
    for db_path, raw_rel in libraries:
        if not db_path.is_file():
            continue
        with closing(sqlite3.connect(db_path)) as conn, conn:
            updates = []
            for doc_id, path in conn.execute('SELECT id, path FROM documents'):
                rel = source_rel(str(path or ''))
                actual = (target / rel).resolve()
                if not rel.is_relative_to(raw_rel) or not actual.is_relative_to(target.resolve()) or not actual.is_file():
                    raise ValueError(f'unsafe or missing document: {rel}')
                updates.append((rel.as_posix(), str(target / rel), doc_id))
            conn.executemany('UPDATE documents SET path=?, abs_path=? WHERE id=?', updates)
            if conn.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise ValueError('SQLite integrity check failed during rebind')
            found += 1
    if not found:
        raise ValueError('no library databases present')
    def convert(obj):
        if isinstance(obj, list):
            return [convert(v) for v in obj]
        if not isinstance(obj, dict):
            return obj
        out = {}
        for key, value in obj.items():
            if key in PATH_FIELDS and isinstance(value, str):
                try:
                    rel = Path(value).relative_to(old)
                    path = (target / rel).resolve()
                    if path.is_relative_to(target.resolve()) and path.is_file():
                        value = str(target / rel)
                except ValueError:
                    pass
            out[key] = convert(value)
        return out
    for root in (target / 'logs', subjects):
        if not root.is_dir():
            continue
        for p in root.rglob('*.json'):
            if p.name == 'subject.json':
                data = json.loads(p.read_text(encoding='utf-8-sig'))
                data['layout'] = {k: str(target / 'subjects' / p.parent.name / v) for k, v in LAYOUT.items()}
                p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
                continue
            before = json.loads(p.read_text(encoding='utf-8-sig'))
            after = convert(before)
            if before != after:
                p.write_text(json.dumps(after, ensure_ascii=False, indent=2), encoding='utf-8')
    temp_marker = target / (MARKER + '.tmp')
    temp_marker.write_text(json.dumps({'version': 1, 'root': str(target)}, ensure_ascii=False), encoding='utf-8')
    os.replace(temp_marker, marker)
    return True


def import_snapshot(source: Path, target: Path) -> tuple[int, int, int]:
    source = source.absolute()
    target = target.absolute()
    if not source.is_dir() or source.is_symlink() or target.is_symlink():
        raise ValueError('invalid source or destination')
    if target.exists() and (not target.is_dir() or any(target.iterdir())):
        raise ValueError('destination must not exist or must be empty')
    if not target.parent.is_dir():
        raise ValueError('destination parent does not exist')
    src_resolved, dst_resolved = source.resolve(), target.resolve()
    if src_resolved.is_relative_to(dst_resolved) or dst_resolved.is_relative_to(src_resolved):
        raise ValueError('source and destination must not overlap')
    ensure_no_links(source)
    databases = documents = rewritten = 0
    with tempfile.TemporaryDirectory(prefix='.study-import-', dir=target.parent) as temp:
        stage = Path(temp) / 'data'
        stage.mkdir()
        for name in DATA_DIRS:
            if (source / name).is_dir():
                shutil.copytree(source / name, stage / name)
        libs = [(stage / 'index' / 'study.db', Path('raw'))]
        subjects = stage / 'subjects'
        if subjects.is_dir():
            for sub in sorted(subjects.iterdir()):
                if not sub.is_dir():
                    continue
                meta = sub / 'subject.json'
                if meta.is_file():
                    data = json.loads(meta.read_text(encoding='utf-8-sig'))
                    data['layout'] = {k: str(target / 'subjects' / sub.name / v) for k, v in LAYOUT.items()}
                    for d in ('raw', 'notes', 'index', 'logs'):
                        (sub / d).mkdir(exist_ok=True)
                    meta.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
                libs.append((sub / 'index' / 'study.db', Path('subjects') / sub.name / 'raw'))
        for db_path, raw_rel in libs:
            if not db_path.is_file():
                continue
            with closing(sqlite3.connect(db_path)) as conn, conn:
                if conn.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                    raise ValueError('SQLite integrity check failed')
                updates = []
                for doc_id, path in conn.execute('SELECT id, path FROM documents'):
                    rel = source_rel(str(path or ''))
                    actual = (stage / rel).resolve()
                    if not rel.is_relative_to(raw_rel) or not actual.is_relative_to(stage.resolve()) or not actual.is_file():
                        raise ValueError(f'document missing or outside allowed subject: {rel}')
                    updates.append((rel.as_posix(), str(target / rel), doc_id))
                conn.executemany('UPDATE documents SET path=?, abs_path=? WHERE id=?', updates)
                if conn.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                    raise ValueError('SQLite integrity check failed after update')
                databases += 1
                documents += len(updates)
        if not databases:
            raise ValueError('no databases found')
        for category in (stage / 'logs', stage / 'subjects'):
            if not category.is_dir():
                continue
            for p in category.rglob('*.json'):
                if p.name == 'subject.json':
                    continue
                old = p.read_text(encoding='utf-8-sig')
                try:
                    obj = json.loads(old)
                except json.JSONDecodeError as e:
                    raise ValueError(f'invalid JSON: {p.relative_to(stage)}') from e
                new_obj = rewrite_json(obj, stage, target)
                if new_obj != obj:
                    p.write_text(json.dumps(new_obj, ensure_ascii=False, indent=2), encoding='utf-8')
                    rewritten += 1
        (stage / MARKER).write_text(json.dumps({'version': 1, 'root': str(target)}, ensure_ascii=False), encoding='utf-8')
        if target.exists():
            target.rmdir()
        os.replace(stage, target)
    return databases, documents, rewritten


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('snapshot', type=Path)
    parser.add_argument('target', type=Path)
    args = parser.parse_args()
    print('Imported databases, documents, rewritten JSON:', import_snapshot(args.snapshot, args.target))
