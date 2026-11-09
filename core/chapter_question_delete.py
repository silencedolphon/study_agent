"""按章节查看并安全删除产物中用户选择的回忆题。"""
import hashlib
import json
import os
import shutil
import time
import uuid
from pathlib import Path
from . import config, preview, textbook_kp, subjects


def handle(conn, payload):
    if not payload.get('expected_subject') or payload['expected_subject'] != subjects.current():
        return {'error': '学科已切换，请刷新页面后重新选择题目'}
    try:
        did, ch = int(payload.get('doc_id') or 0), int(payload.get('chapter') or 0)
        mode = payload.get('mode')
        if did <= 0 or ch <= 0 or mode not in ('preview', 'textbook'):
            raise ValueError()
    except (TypeError, ValueError):
        return {'error': '教材、章节或类型无效'}
    row = conn.execute('SELECT role FROM documents WHERE id=?', (did,)).fetchone()
    if not row or row[0] != 'textbook':
        return {'error': '找不到当前学科中的教材'}
    root = (Path(config.LOG_DIR) / 'knowledge').resolve()
    path = textbook_kp._paths(did, ch)[0] if mode == 'textbook' else preview.preview_out_file_per_chapter(did, ch)
    if mode == 'preview' and not path.exists():
        legacy = preview.legacy_preview_out_file(did)
        if legacy.is_symlink():
            return {'error': '产物路径无效'}
        if legacy.exists():
            try:
                raw = json.loads(legacy.read_text(encoding='utf-8'))
                if int((raw.get('meta') or {}).get('chapter_no') or 0) == ch:
                    path = legacy
            except (OSError, ValueError, TypeError, AttributeError):
                pass
    if path.is_symlink() or path.resolve().parent != root or not path.is_file():
        return {'error': '找不到该章结果或产物路径无效'}
    try:
        original = path.read_bytes()
        doc = json.loads(original.decode('utf-8'))
        owners = doc.get('points' if mode == 'textbook' else 'subsections') or []
        if not isinstance(owners, list):
            raise ValueError('章节格式错误')
        items = []
        for oi, owner in enumerate(owners):
            if not isinstance(owner, dict):
                continue
            key = 'questions' if mode == 'textbook' else ('self_check' if 'self_check' in owner else 'questions')
            questions = owner.get(key) or []
            if not isinstance(questions, list):
                continue
            for qi, q in enumerate(questions):
                text = (q.get('question') or q.get('q') or '') if isinstance(q, dict) else str(q)
                items.append({'id': f'{oi}:{qi}', 'group': str(owner.get('title') or owner.get('name') or f'第{oi+1}节'), 'question': str(text)})
        revision = hashlib.sha256(original).hexdigest()
        scope = hashlib.sha256(str(Path(config.DB_PATH).resolve()).encode('utf-8')).hexdigest()
        if not payload.get('delete'):
            return {'ok': True, 'revision': revision, 'scope': scope, 'items': items}
        if payload.get('confirm') is not True or payload.get('revision') != revision or payload.get('scope') != scope:
            return {'error': '未确认、学科或章节已变化，请重新选择'}
        requested = payload.get('ids')
        if not isinstance(requested, list) or not requested or len(requested) > 1000:
            return {'error': '请选择 1 至 1000 道题目'}
        selected = set(map(str, requested))
        if len(selected) != len(requested) or not selected.issubset({x['id'] for x in items}):
            return {'error': '题目选择已过期，请重新选择'}
        for oi, owner in enumerate(owners):
            if not isinstance(owner, dict):
                continue
            key = 'questions' if mode == 'textbook' else ('self_check' if 'self_check' in owner else 'questions')
            qs = owner.get(key)
            if not isinstance(qs, list):
                continue
            remaining = [q for qi, q in enumerate(qs) if f'{oi}:{qi}' not in selected]
            if mode == 'textbook' and qs and not remaining:
                return {'error': '每个教材知识点至少需要保留一道回忆题；请调整选择'}
            owner[key] = remaining
        backup = path.with_name(path.name + '.before-question-delete-' + time.strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:8] + '.bak')
        shutil.copy2(path, backup)
        tmp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
        try:
            tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding='utf-8')
            os.replace(tmp, path)
        finally:
            if tmp.exists():
                tmp.unlink()
        return {'ok': True, 'deleted': len(selected), 'backup': str(backup)}
    except (ValueError, TypeError, OSError, AttributeError) as exc:
        return {'error': '处理章节题目失败：' + str(exc)}
