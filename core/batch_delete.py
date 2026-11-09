"""受限路径内的笔记与教材章节产物批量删除。"""
import json
from pathlib import Path
from . import config, preview, textbook_kp


def knowledge_points(conn, payload):
    """删除当前学科数据库中选中的知识点；关联复习记录级联删除，错题保留。"""
    raw = payload.get('ids')
    if not isinstance(raw, list) or not raw or len(raw) > 500:
        return {'error': '请选择 1 至 500 条知识点'}
    try:
        ids = list(dict.fromkeys(int(x) for x in raw if not isinstance(x, bool)))
        if len(ids) != len(set(map(str, raw))) or any(x <= 0 for x in ids):
            raise ValueError()
    except (ValueError, TypeError):
        return {'error': '知识点编号无效'}
    if payload.get('confirm') is not True:
        return {'error': '请先确认删除'}
    if not conn.execute('PRAGMA foreign_keys').fetchone()[0]:
        return {'error': '数据库外键保护未开启，已拒绝删除'}
    placeholders = ','.join('?' for _ in ids)
    try:
        existing = [int(r[0]) for r in conn.execute(
            f'SELECT id FROM knowledge_points WHERE id IN ({placeholders})', ids)]
        if existing:
            conn.execute(f'DELETE FROM knowledge_points WHERE id IN ({placeholders})', ids)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return {'ok': True, 'deleted': len(existing), 'missing': len(ids) - len(existing)}


def _delete(paths, dry_run):
    deleted, missing, errors = [], [], []
    for path in dict.fromkeys(paths):
        try:
            if not path.exists():
                missing.append(path.name)
            elif dry_run:
                deleted.append(path.name)
            else:
                path.unlink()
                deleted.append(path.name)
        except OSError as exc:
            errors.append({"file": path.name, "error": str(exc)})
    return {"ok": not errors, "dry_run": dry_run, "deleted": deleted,
            "missing": missing, "errors": errors}


def notes(payload):
    names = payload.get("files")
    if not isinstance(names, list) or not names or len(names) > 1000:
        return {"error": "请选择 1 至 1000 篇笔记"}
    root = Path(config.NOTES_DIR).resolve()
    paths = []
    for name in names:
        if not isinstance(name, str) or not name or Path(name).name != name or not name.lower().endswith('.md'):
            return {"error": "笔记文件名无效"}
        path = root / name
        if path.is_symlink() or path.resolve().parent != root:
            return {"error": "笔记路径无效"}
        paths.append(path)
    dry = bool(payload.get("dry_run"))
    if not dry and payload.get("confirm") is not True:
        return {"error": "请先确认删除"}
    return _delete(paths, dry)


def chapters(conn, payload):
    try:
        did = int(payload.get("doc_id") or 0)
        chapters = payload.get("chapters")
        if not isinstance(chapters, list) or not chapters or len(chapters) > 1000:
            raise ValueError()
        chapters = list(dict.fromkeys(int(x) for x in chapters))
        if did <= 0 or any(x <= 0 or x > 100000 for x in chapters):
            raise ValueError()
    except (ValueError, TypeError):
        return {"error": "教材或章节无效"}
    row = conn.execute("SELECT role FROM documents WHERE id=?", (did,)).fetchone()
    if not row or row[0] != 'textbook':
        return {"error": "找不到教材"}
    kind = payload.get("kind")
    if kind not in ('preview', 'textbook'):
        return {"error": "产物类型无效"}
    dry = bool(payload.get("dry_run"))
    if not dry and payload.get("confirm") is not True:
        return {"error": "请先确认删除"}
    paths = []
    for ch in chapters:
        if kind == 'textbook':
            paths.extend(textbook_kp._paths(did, ch))
        else:
            paths.append(preview.preview_out_file_per_chapter(did, ch))
            legacy = preview.legacy_preview_out_file(did)
            if legacy.is_symlink():
                return {"error": "产物路径无效"}
            if legacy.exists():
                try:
                    old = json.loads(legacy.read_text(encoding='utf-8'))
                    if int((old.get('meta') or {}).get('chapter_no') or 0) == ch:
                        paths.append(legacy)
                except (OSError, ValueError, TypeError, AttributeError):
                    pass
    root = (Path(config.LOG_DIR) / 'knowledge').resolve()
    if any(p.is_symlink() or p.resolve().parent != root for p in paths):
        return {"error": "产物路径无效"}
    return _delete(paths, dry)
