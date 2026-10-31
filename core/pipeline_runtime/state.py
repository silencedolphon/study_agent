"""P0-B / S03+S04：remote_unknown 判定 + 细粒度 single-flight 文件锁。

设计底线：
- **发送后结果未知**（超时/连接中断/服务端没回包）→ `remote_unknown`：只允许一次请求，
  上层重试循环必须立即放弃；**不得自动重发**（可能已扣费/已产生副作用）。
- **发送前错误**（无密钥/参数/KeyError）不是 unknown，可按普通失败处理。
- **HTTPError（服务端有响应）**不是 unknown（能明确知道失败）；429/5xx 仍可按现有策略重试。
- 锁文件只放 token/pid/创建时间/键信息；**不放密钥/正文**。
"""
from __future__ import annotations

import errno
import hashlib
import http.client
import json
import os
import socket
import threading
import time
import urllib.error
import uuid
from contextlib import contextmanager
from pathlib import Path

from .. import config

# ---------------- 状态常量 ----------------
PLANNED = "planned"
RUNNING = "running"
DONE = "done"
FAILED = "failed"
PARTIAL = "partial"
CANCELLED = "cancelled"
REMOTE_UNKNOWN = "remote_unknown"

RUN_STATES = (PLANNED, RUNNING, DONE, FAILED, PARTIAL, CANCELLED, REMOTE_UNKNOWN)


class RemoteUnknownError(RuntimeError):
    """发送后结果未知：**禁止自动重发**（调用方只能把错误交回给用户决定）。"""

    remote_unknown = True

    def __init__(self, message: str, *, cause: BaseException | None = None):
        super().__init__(message)
        self.remote_unknown = True
        self.cause = cause
        self.original_class = type(cause).__name__ if cause is not None else ""
        self.finish_reason = str(getattr(cause, "finish_reason", "") or "")
        self.usage = getattr(cause, "usage", None)


class SingleFlightBusy(RuntimeError):
    """同键已有执行者；调用方必须明确报错，不得并行再发付费请求。"""

    def __init__(self, message: str, info: dict | None = None):
        super().__init__(message)
        self.info = info or {}


# ---------------- unknown 判定 ----------------
_NETWORK_NAMES = {
    "RemoteDisconnected", "ConnectionResetError", "ConnectionAbortedError",
    "ConnectionRefusedError", "BrokenPipeError", "TimeoutError", "timeout",
    "IncompleteRead", "IncompleteReadError", "ChunkedEncodingError",
    "ProtocolError", "SSLError", "URLError", "HTTPException",
}


def is_remote_unknown(exc: BaseException | None) -> bool:
    """异常是否带 unknown 标记（含 cause 链）。"""
    seen = set()
    cur = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        if (isinstance(cur, RemoteUnknownError)
                or getattr(cur, "remote_unknown", False) is True):
            return True
        cur = cur.__cause__ or cur.__context__
    return False


def request_may_have_been_sent(exc: BaseException | None) -> bool:
    """保守判定"请求可能已经发出"。

    - RemoteUnknownError → True；
    - urllib HTTPError（有服务端响应）→ False；
    - URLError / RemoteDisconnected / socket.timeout / 超时 / 连接中断 → True；
    - KeyError/TypeError/ValueError/无密钥 ChatError 等发送前错误 → False。
    """
    if exc is None:
        return False
    seen = set()
    cur = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        if (isinstance(cur, RemoteUnknownError)
                or getattr(cur, "remote_unknown", False) is True):
            return True
        if isinstance(cur, urllib.error.HTTPError):
            return False
        if isinstance(cur, urllib.error.URLError):
            return True
        if isinstance(cur, (socket.timeout, TimeoutError, ConnectionError,
                           http.client.RemoteDisconnected, http.client.HTTPException)):
            return True
        name = type(cur).__name__
        if name in _NETWORK_NAMES:
            return True
        # 发送前错误：参数/无密钥/编程错误 —— 明确不是 unknown
        if isinstance(cur, (KeyError, TypeError, ValueError, AttributeError,
                            AssertionError, PermissionError, FileNotFoundError)):
            return False
        cur = cur.__cause__ or cur.__context__
    return False


def is_unknown_failure(exc: BaseException | None) -> bool:
    """上层重试循环用：显式 unknown 或"可能已发送"的网络异常，都禁止自动重试。"""
    return bool(is_remote_unknown(exc) or request_may_have_been_sent(exc))


# ---------------- config fingerprint ----------------
def config_fingerprint(values) -> str:
    """把会影响输出/成本的配置折叠成短指纹（不写具体值到锁文件以外的日志）。"""
    try:
        raw = json.dumps(values, ensure_ascii=False, sort_keys=True, default=str)
    except (TypeError, ValueError):
        raw = repr(values)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


# ---------------- SingleFlight ----------------
def default_lock_dir() -> Path:
    override = str(os.environ.get("PIPELINE_LOCK_DIR", "") or "").strip()
    if override:
        return Path(override)
    return Path(config.LOG_DIR) / "runtime" / "locks"


class SingleFlight:
    """按细粒度键的文件锁（跨线程 + 跨进程）。

    键：`pipeline + subject + doc_id + stage + object + config_fingerprint`；
    锁文件放在 `PIPELINE_LOCK_DIR`（默认 `config.LOG_DIR/runtime/locks`），
    原子 `O_CREAT|O_EXCL` 创建；过期锁可回收；只释放自己 token 的锁。
    `force` 不抢正在执行者的锁（不得并行），只允许回收已完成/过期锁。
    """

    def __init__(self, lock_dir=None, stale_after: float | None = None):
        self.lock_dir = Path(lock_dir) if lock_dir else default_lock_dir()
        if stale_after is None:
            try:
                stale_after = float(os.environ.get("PIPELINE_LOCK_TTL", "43200"))
            except (TypeError, ValueError):
                stale_after = 43200.0
        self.stale_after = max(1.0, float(stale_after))

    # -- 键 --
    @staticmethod
    def make_key(*, pipeline: str, subject: str = "", doc_id: int = 0,
                 stage: str = "", object: str = "",
                 config_fingerprint: str = "") -> str:
        raw = "|".join(str(x) for x in (
            "sf1", pipeline, subject, int(doc_id or 0), stage, object,
            config_fingerprint))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]

    def lock_path(self, key: str) -> Path:
        return self.lock_dir / ("%s.lock" % key)

    # -- 读/过期 --
    def read(self, key: str) -> dict:
        try:
            raw = self.lock_path(key).read_text(encoding="utf-8")
            data = json.loads(raw)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _is_stale(self, info: dict, now: float | None = None) -> bool:
        if not info:
            return False                        # 读不出来先不抢（保守）
        try:
            created = float(info.get("created_at") or 0.0)
        except (TypeError, ValueError):
            return False
        return (time.time() if now is None else now) - created > self.stale_after

    def _try_reclaim(self, path: Path, info: dict, *, force: bool,
                     now: float) -> bool:
        """只有过期/已完成/失败（残留）的锁可回收；**执行中的锁绝不抢**。"""
        status = str((info or {}).get("status") or RUNNING)
        stale = self._is_stale(info, now)
        reclaimable = stale or status in (DONE, FAILED, CANCELLED, PARTIAL)
        if not reclaimable:
            return False
        if not force and not stale:
            return False                        # 非 force 不动"已完成"残留？允许回收旧残留更安全
        try:
            path.unlink()
            return True
        except OSError:
            return False

    # -- 获取 --
    def try_acquire(self, *, pipeline: str, subject: str = "", doc_id: int = 0,
                    stage: str = "", object: str = "", config_fingerprint: str = "",
                    force: bool = False, ttl: float | None = None,
                    status: str = RUNNING) -> dict:
        key = self.make_key(pipeline=pipeline, subject=subject, doc_id=doc_id,
                            stage=stage, object=object,
                            config_fingerprint=config_fingerprint)
        path = self.lock_path(key)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            return {"ok": False, "busy": False, "key": key, "path": str(path),
                    "error": "无法创建锁目录：%s" % e, "holder": {}}
        now = time.time()
        for _attempt in range(2):
            payload = {
                "token": uuid.uuid4().hex,
                "pid": os.getpid(),
                "created_at": now,
                "status": status,
                "key": key,
                "pipeline": str(pipeline), "subject": str(subject),
                "doc_id": int(doc_id or 0), "stage": str(stage),
                "object": str(object),
                "config_fingerprint": str(config_fingerprint),
            }
            try:
                fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                holder = self.read(key)
                if self._try_reclaim(path, holder, force=force, now=now):
                    continue
                return {"ok": False, "busy": True, "key": key, "path": str(path),
                        "holder": holder,
                        "error": ("同一对象正在执行中（%s / %s / %s）；"
                                  "请等它完成，或稍后再试"
                                  % (pipeline, stage, object))}
            except OSError as e:
                return {"ok": False, "busy": False, "key": key, "path": str(path),
                        "error": "创建锁失败：%s" % e, "holder": {}}
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    json.dump(payload, fh, ensure_ascii=False)
            except OSError:
                try:
                    path.unlink()
                except OSError:
                    pass
                return {"ok": False, "busy": False, "key": key, "path": str(path),
                        "error": "写锁文件失败", "holder": {}}
            return {"ok": True, "busy": False, "held": True, "key": key,
                    "token": payload["token"], "path": str(path),
                    "created_at": now, "handle": payload}
        return {"ok": False, "busy": True, "key": key, "path": str(path),
                "error": "锁竞争失败，请稍后重试", "holder": self.read(key)}

    # -- 释放 --
    def release(self, handle) -> bool:
        """只释放**自己 token** 的锁；被别人接管/已释放 → False（不误删）。"""
        if not isinstance(handle, dict):
            return False
        path = Path(str(handle.get("path") or ""))
        token = str(handle.get("token") or "")
        if not path.name or not token:
            return False
        info = {}
        try:
            info = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            info = {}
        if isinstance(info, dict) and info.get("token") != token:
            return False
        try:
            path.unlink()
            return True
        except FileNotFoundError:
            return False
        except OSError:
            return False

    @contextmanager
    def hold(self, **kwargs):
        got = self.try_acquire(**kwargs)
        if not got.get("ok"):
            raise SingleFlightBusy(str(got.get("error") or "single-flight busy"),
                                   got)
        try:
            yield got
        finally:
            self.release(got)

    # -- 查询（报告/测试） --
    def info(self, *, pipeline: str, subject: str = "", doc_id: int = 0,
             stage: str = "", object: str = "",
             config_fingerprint: str = "") -> dict:
        key = self.make_key(pipeline=pipeline, subject=subject, doc_id=doc_id,
                            stage=stage, object=object,
                            config_fingerprint=config_fingerprint)
        return {"key": key, "path": str(self.lock_path(key)),
                "holder": self.read(key)}
