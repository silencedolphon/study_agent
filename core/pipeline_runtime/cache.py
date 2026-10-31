"""P2-A：版本化 stage cache（runs/steps）+ 崩溃恢复/原子切换基础。

只存**指纹/引用/计数/状态**，不存正文/密钥；WAL + 原子事务。
`STAGE_CACHE=0` 时全部 no-op（完全不建文件、不走旧路径分支）。
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from .. import config

CACHE_VERSION = "stage-cache-v1"
CACHE_SCHEMA_VERSION = 1
DEFAULT_STALE_AFTER = 3600.0

_SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS runs (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    run_key           TEXT NOT NULL UNIQUE,
    run_id            TEXT NOT NULL UNIQUE,
    pipeline          TEXT NOT NULL,
    subject           TEXT,
    doc_id            INTEGER NOT NULL DEFAULT 0,
    config_fingerprint TEXT,
    status            TEXT NOT NULL,
    error_class       TEXT,
    created_at        REAL NOT NULL,
    updated_at        REAL NOT NULL,
    heartbeat_at      REAL,
    finished_at       REAL
);
CREATE TABLE IF NOT EXISTS steps (
    run_id            TEXT NOT NULL,
    stage             TEXT NOT NULL,
    step_key          TEXT NOT NULL,
    status            TEXT NOT NULL,
    cache_version     TEXT,
    config_fingerprint TEXT,
    input_fingerprint TEXT,
    output_ref        TEXT,
    counts_json       TEXT,
    error_class       TEXT,
    input_fp          TEXT,
    version           TEXT,
    cache_hit         INTEGER NOT NULL DEFAULT 0,
    work_ms           REAL,
    created_at        REAL NOT NULL,
    updated_at        REAL NOT NULL,
    PRIMARY KEY (run_id, stage, step_key)
);
CREATE INDEX IF NOT EXISTS idx_runs_status ON runs(status);
"""

_ALLOWED_RUN_STATUS = ("running", "done", "partial", "failed", "cancelled",
                       "remote_unknown", "stale")


def enabled() -> bool:
    return str(os.environ.get("STAGE_CACHE", "1")).strip().lower() not in (
        "0", "false", "off", "no")


def cache_path() -> Path:
    override = str(os.environ.get("STAGE_CACHE_FILE", "") or "").strip()
    if override:
        return Path(override)
    return Path(config.LOG_DIR) / "runtime" / "stage_cache.sqlite"


def _now() -> float:
    return time.time()


def _connect() -> sqlite3.Connection:
    path = cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    last = None
    for _attempt in range(20):
        conn = None
        try:
            conn = sqlite3.connect(str(path), timeout=30)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA busy_timeout=30000")
            conn.executescript(_SCHEMA)
            _migrate(conn)
            conn.execute("PRAGMA synchronous=NORMAL")
            return conn
        except sqlite3.OperationalError as e:
            last = e
            if conn is not None:
                try:
                    conn.close()
                except Exception:  # noqa: BLE001
                    pass
            time.sleep(0.05 * (_attempt + 1))
    raise last  # type: ignore[misc]


def _run_key(pipeline: str, subject: str, doc_id: int, config_fingerprint: str) -> str:
    raw = "|".join(["run-v1", str(pipeline), str(subject or ""), str(int(doc_id or 0)),
                    str(config_fingerprint or "")])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def begin_run(*, pipeline: str, subject: str = "", doc_id: int = 0,
              config_fingerprint: str = "", run_id: str | None = None,
              status: str = "running") -> dict:
    """同一 run_key 并发只允许一个 run（INSERT OR IGNORE + 唯一键）。

    返回 {"ok", "run_id", "created", "status"}；`STAGE_CACHE=0` 返回 disabled。
    """
    if not enabled():
        return {"ok": True, "enabled": False, "run_id": "", "created": False}
    key = _run_key(pipeline, subject, doc_id, config_fingerprint)
    rid = run_id or ("sc-" + uuid.uuid4().hex[:16])
    now = _now()
    conn = _connect()
    try:
        with conn:
            cur = conn.execute(
                "INSERT OR IGNORE INTO runs(run_key, run_id, pipeline, subject, doc_id, "
                "config_fingerprint, status, created_at, updated_at, heartbeat_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (key, rid, str(pipeline), str(subject or ""), int(doc_id or 0),
                 str(config_fingerprint or ""), status, now, now, now))
            row = conn.execute("SELECT run_id, status FROM runs WHERE run_key=?",
                               (key,)).fetchone()
        created = bool(cur.rowcount)
        return {"ok": True, "enabled": True, "created": created,
                "run_id": str(row["run_id"]), "status": str(row["status"])}
    finally:
        conn.close()


def heartbeat(run_id: str) -> bool:
    if not enabled() or not run_id:
        return False
    conn = _connect()
    try:
        with conn:
            cur = conn.execute("UPDATE runs SET heartbeat_at=?, updated_at=? WHERE run_id=?",
                               (_now(), _now(), str(run_id)))
        return bool(cur.rowcount)
    finally:
        conn.close()


def finish_run(run_id: str, status: str = "done", error_class: str = "") -> bool:
    if not enabled() or not run_id:
        return False
    status = str(status or "failed")
    if status not in _ALLOWED_RUN_STATUS:
        status = "failed"
    conn = _connect()
    try:
        with conn:
            cur = conn.execute(
                "UPDATE runs SET status=?, error_class=?, updated_at=?, heartbeat_at=?, "
                "finished_at=? WHERE run_id=?",
                (status, str(error_class or "")[:120], _now(), _now(), _now(),
                 str(run_id)))
        return bool(cur.rowcount)
    finally:
        conn.close()


def get_run(run_id: str) -> dict | None:
    if not run_id or not enabled():
        return None
    conn = _connect()
    try:
        row = conn.execute("SELECT * FROM runs WHERE run_id=?", (str(run_id),)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _step_key_hash(step_key: str) -> str:
    return hashlib.sha256(str(step_key).encode("utf-8")).hexdigest()[:32]


def put_step(run_id: str, stage: str, step_key: str, *, config_fingerprint: str = "",
             input_fingerprint: str = "", output_ref: str = "",
             counts: dict | None = None, status: str = "done",
             cache_hit: bool = False, work_ms: float | None = None) -> bool:
    """写一条 stage 记录；counts 只保留数值项（正文/密钥不会落盘）。"""
    if not enabled() or not run_id:
        return False
    safe_counts = {}
    for k, v in (counts or {}).items():
        if isinstance(v, bool):
            safe_counts[str(k)[:40]] = int(v)
        elif isinstance(v, (int, float)):
            safe_counts[str(k)[:40]] = v
    conn = _connect()
    try:
        now = _now()
        with conn:
            conn.execute(
                "INSERT INTO steps(run_id, stage, step_key, status, cache_version, "
                "config_fingerprint, input_fingerprint, output_ref, counts_json, "
                "error_class, input_fp, version, cache_hit, work_ms, "
                "created_at, updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(run_id, stage, step_key) DO UPDATE SET "
                "status=excluded.status, cache_version=excluded.cache_version, "
                "config_fingerprint=excluded.config_fingerprint, "
                "input_fingerprint=excluded.input_fingerprint, "
                "output_ref=excluded.output_ref, counts_json=excluded.counts_json, "
                "error_class=excluded.error_class, input_fp=excluded.input_fp, "
                "version=excluded.version, cache_hit=excluded.cache_hit, "
                "work_ms=excluded.work_ms, updated_at=excluded.updated_at",
                (str(run_id), str(stage), _step_key_hash(step_key), str(status),
                 CACHE_VERSION, str(config_fingerprint or ""),
                 str(input_fingerprint or ""), str(output_ref or ""),
                 json.dumps(safe_counts, ensure_ascii=False), "",
                 str(input_fingerprint or ""), CACHE_VERSION,
                 1 if cache_hit else 0,
                 (float(work_ms) if work_ms is not None else None), now, now))
        return True
    finally:
        conn.close()


def get_step(run_id: str, stage: str, step_key: str, *, config_fingerprint: str = "",
             input_fingerprint: str = "") -> dict | None:
    """命中条件：done + cache_version 一致 + 配置/输入指纹一致 + 输出引用完整。

    旧 cache 无版本/字段 → miss（**不自动付费重跑**，由调用方决定）。
    """
    if not enabled() or not run_id:
        return None
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT * FROM steps WHERE run_id=? AND stage=? AND step_key=?",
            (str(run_id), str(stage), _step_key_hash(step_key))).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    if str(row["status"]) != "done":
        return None
    if str(row["cache_version"] or "") != CACHE_VERSION:
        return None
    if str(row["config_fingerprint"] or "") != str(config_fingerprint or ""):
        return None
    if str(row["input_fingerprint"] or "") != str(input_fingerprint or ""):
        return None
    ref = str(row["output_ref"] or "")
    if ref and not verify_output(ref):
        return None
    out = dict(row)
    try:
        out["counts"] = json.loads(out.get("counts_json") or "{}")
    except ValueError:
        out["counts"] = {}
    return out


def mark_stale_runs(stale_after: float | None = None, *, status: str = "stale") -> int:
    """running 且心跳超时的 run 标记 stale；done/partial/unknown 一律不动。"""
    if not enabled():
        return 0
    cutoff = _now() - float(stale_after if stale_after is not None else
                            os.environ.get("STAGE_CACHE_STALE_SEC", DEFAULT_STALE_AFTER))
    conn = _connect()
    try:
        with conn:
            cur = conn.execute(
                "UPDATE runs SET status=?, updated_at=? "
                "WHERE status='running' AND COALESCE(heartbeat_at, updated_at) < ?",
                (str(status), _now(), cutoff))
        return int(cur.rowcount or 0)
    finally:
        conn.close()


def retry_allowed(status: str) -> bool:
    """崩溃恢复：done 不重跑；remote_unknown 禁止自动重发；仅 failed/stale 可重试。"""
    return str(status or "") in ("failed", "stale")


# ---------------- 预备 → 校验 → 切换（原子输出） ----------------
def prepare_output(path: Path) -> Path:
    """生成同目录临时文件（不碰目标文件）；调用方写完后 verify/switch。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path.with_name(path.name + ".tmp-" + uuid.uuid4().hex[:8])


def verify_output(path: Path, *, allow_tmp: bool = False) -> bool:
    """输出引用完整性：存在、非空、默认拒绝临时半写文件。"""
    try:
        p = Path(path)
        if not allow_tmp and ".tmp-" in p.name:
            return False
        return p.is_file() and p.stat().st_size > 0
    except OSError:
        return False


def switch_output(tmp: Path, final: Path) -> bool:
    """校验后原子切换（os.replace）；失败不动旧目标。"""
    tmp, final = Path(tmp), Path(final)
    if not verify_output(tmp, allow_tmp=True):
        return False
    try:
        os.replace(tmp, final)
        return True
    except OSError:
        return False


@contextmanager
def staged_output(path: Path):
    """`with staged_output(p) as tmp:` 写 tmp；退出时校验→切换；异常删除半写。"""
    tmp = prepare_output(path)
    try:
        yield tmp
        if not switch_output(tmp, path):
            raise RuntimeError("输出校验失败，未切换")
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def write_atomic(path: Path, data: bytes | str) -> bool:
    path = Path(path)
    with staged_output(path) as tmp:
        if isinstance(data, str):
            tmp.write_text(data, encoding="utf-8")
        else:
            tmp.write_bytes(data)
    return True


def _migrate(conn: sqlite3.Connection) -> None:
    """给旧库补列（只增不删）；失败忽略（新库已含）。"""
    wanted = {
        "runs": {"finished_at": "REAL"},
        "steps": {"input_fp": "TEXT", "version": "TEXT",
                  "cache_hit": "INTEGER NOT NULL DEFAULT 0", "work_ms": "REAL"},
    }
    for table, cols in wanted.items():
        have = {r[1] for r in conn.execute("PRAGMA table_info(%s)" % table)}
        for name, typ in cols.items():
            if name not in have:
                try:
                    conn.execute("ALTER TABLE %s ADD COLUMN %s %s" % (table, name, typ))
                except sqlite3.Error:
                    pass
    conn.commit()


def cache_key(pipeline: str, stage: str, subject: str = "", doc_id: int = 0,
              object: str = "", version: str = CACHE_VERSION,
              config_fp: str = "", input_fp: str = "") -> str:
    """稳定结构化键（供报告/测试核对；不存正文）。"""
    raw = "|".join(["stage-v1", str(pipeline), str(stage), str(subject or ""),
                    str(int(doc_id or 0)), str(object or ""), str(version),
                    str(config_fp or ""), str(input_fp or "")])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def new_run(*, pipeline: str, subject: str = "", doc_id: int = 0,
            config_fp: str = "", config_fingerprint: str = "",
            run_id: str | None = None, status: str = "running") -> dict:
    """任务契约名 `new_run`（等价 begin_run）；config_fp/config_fingerprint 二选一。"""
    return begin_run(pipeline=pipeline, subject=subject, doc_id=doc_id,
                     config_fingerprint=config_fingerprint or config_fp,
                     run_id=run_id, status=status)


def prune(max_age_sec: float = 30 * 24 * 3600) -> int:
    """清理过旧的已结束 run + 其 steps；running/未知状态保留（交给 stale 恢复）。"""
    if not enabled():
        return 0
    cutoff = _now() - float(max_age_sec)
    conn = _connect()
    try:
        with conn:
            rows = conn.execute(
                "SELECT run_id FROM runs WHERE status NOT IN ('running') "
                "AND updated_at < ?", (cutoff,)).fetchall()
            ids = [r[0] for r in rows]
            for rid in ids:
                conn.execute("DELETE FROM steps WHERE run_id=?", (rid,))
                conn.execute("DELETE FROM runs WHERE run_id=?", (rid,))
        return len(ids)
    finally:
        conn.close()


def close() -> None:
    """兼容 API：本模块每次调用独立连接，无进程级连接需要关闭。"""
    return None


def stats() -> dict:
    """诊断/报告用计数（不读正文）。"""
    if not enabled():
        return {"enabled": False}
    conn = _connect()
    try:
        runs = {r["status"]: int(r["n"]) for r in conn.execute(
            "SELECT status, COUNT(*) AS n FROM runs GROUP BY status")}
        steps = int(conn.execute("SELECT COUNT(*) FROM steps").fetchone()[0])
        return {"enabled": True, "runs": runs, "steps": steps,
                "path": str(cache_path())}
    finally:
        conn.close()
