"""Safely initialize an empty study-data directory from the published snapshot.

Existing data is never overwritten. Downloads and extraction stream to disk.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import shutil
import stat
import tempfile
import urllib.request
import zipfile

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SEED_URL = ('https://github.com/silencedolphon/study_agent/releases/download/'
            'learning-data-20261110/study-agent-learning-data-20261110.zip')
SEED_SHA256 = 'f3a32fe489223d5fb795caa596ca57ec198208fddd42bd06163948fc47d3ccc8'
SEED_SIZE = 1402654621
MAX_EXTRACTED_SIZE = 4 * 1024 ** 3
MAX_FILE_SIZE = 1024 ** 3
MAX_ENTRIES = 100_000
DOWNLOAD_CHUNK = 16 * 1024 * 1024


def data_home() -> Path:
    value = os.environ.get('KB_HOME', '').strip()
    return Path(value).expanduser().absolute() if value else PROJECT_ROOT / '.study_data'


def has_existing_local_library() -> bool:
    if (PROJECT_ROOT / 'index' / 'study.db').is_file():
        return True
    subjects = PROJECT_ROOT / 'subjects'
    return subjects.is_dir() and any(subjects.iterdir())


def download(url: str, destination: Path, digest: str, expected_size: int) -> None:
    h = hashlib.sha256()
    total = 0
    if expected_size <= 0:
        raise ValueError('Snapshot size must be positive')
    print('Downloading 0%', flush=True)
    request = urllib.request.Request(url, headers={'User-Agent': 'study-agent-seed/1', 'Accept-Encoding': 'identity'})
    with urllib.request.urlopen(request, timeout=120) as response, destination.open('xb') as out:
        if response.status != 200:
            raise ValueError('Unexpected download status')
        length = response.headers.get('Content-Length')
        if length is not None and (not length.isdigit() or int(length) != expected_size):
            raise ValueError('Incorrect snapshot download Content-Length')
        next_progress = DOWNLOAD_CHUNK
        while chunk := response.read(1024 * 1024):
            total += len(chunk)
            if total > expected_size:
                raise ValueError('Download exceeds the expected size')
            out.write(chunk)
            h.update(chunk)
            if total >= next_progress and total < expected_size:
                print(f'Downloaded {total * 100 // expected_size}%', flush=True)
                next_progress = total + DOWNLOAD_CHUNK
    if total != expected_size or h.hexdigest() != digest:
        raise ValueError('Downloaded snapshot size or checksum is incorrect')
    print('Downloaded 100%', flush=True)


def extract_verified(archive: Path, dest: Path) -> None:
    """Validate every member before writing, then extract without following links."""
    dest.mkdir(parents=True, exist_ok=False)
    root = dest.resolve()
    with zipfile.ZipFile(archive) as z:
        entries = z.infolist()
        if len(entries) > MAX_ENTRIES or sum(i.file_size for i in entries) > MAX_EXTRACTED_SIZE:
            raise ValueError('Snapshot archive exceeds extraction limits')
        seen: set[str] = set()
        targets = []
        for info in entries:
            name = info.filename.replace('\\', '/')
            path = PurePosixPath(name)
            windows = PureWindowsPath(name)
            mode = info.external_attr >> 16
            kind = stat.S_IFMT(mode)
            parts = name.rstrip('/').split('/')
            if (not name or '\x00' in name or path.is_absolute() or windows.drive
                    or any(part in ('', '.', '..') or ':' in part for part in parts)
                    or kind not in (0, stat.S_IFREG, stat.S_IFDIR)
                    or info.flag_bits & 1 or info.file_size > MAX_FILE_SIZE):
                raise ValueError('Unsafe archive member')
            key = name.rstrip('/').casefold()
            if key in seen:
                raise ValueError('Duplicate archive member')
            seen.add(key)
            target = root.joinpath(*path.parts)
            if not target.resolve().is_relative_to(root):
                raise ValueError('Archive member escapes extraction root')
            targets.append((info, target))
        for info, target in targets:
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with z.open(info, 'r') as src, target.open('xb') as out:
                    shutil.copyfileobj(src, out, length=1024 * 1024)


def prepare(target: Path | None = None) -> bool:
    target = (target or data_home()).absolute()
    if target.is_symlink() or (target.exists() and not target.is_dir()):
        raise ValueError('Data target must be a directory, not a link or file')
    if target.exists() and any(target.iterdir()):
        if not (target / 'index' / 'study.db').is_file():
            raise ValueError(f'Non-empty data directory has no library database; refusing to initialize: {target}')
        if (target / '.study-data-root.json').is_file():
            from import_library_snapshot import ensure_relocated
            if ensure_relocated(target):
                print('Rebound the existing imported library to its current path.', flush=True)
        print(f'Existing data at {target}; preserving it and skipping initialization.', flush=True)
        return False
    # Do not initialize as a side effect of running from an existing local library.
    if target == PROJECT_ROOT / '.study_data' and has_existing_local_library():
        print('Existing local library found; preserving it and skipping initialization.', flush=True)
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.study-seed-', dir=target.parent) as work:
        work = Path(work)
        archive = work / 'snapshot.zip'
        extracted = work / 'snapshot'
        print('Downloading the published learning-data snapshot...', flush=True)
        download(SEED_URL, archive, SEED_SHA256, SEED_SIZE)
        print('Checksum verified; extracting and importing...', flush=True)
        extract_verified(archive, extracted)
        from import_library_snapshot import import_snapshot
        count = import_snapshot(extracted, target)
        print(f'Learning-data initialization completed: {count}', flush=True)
    return True


if __name__ == '__main__':
    prepare()
