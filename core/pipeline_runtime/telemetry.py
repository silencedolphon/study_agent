"""最小阶段 telemetry（P0-A / S02）：JSONL 只记计数/耗时/错误类别。

**不记录**：密钥、消息正文/教材正文、模型推理内容、API 响应原文。
- 默认路径 `config.LOG_DIR/runtime/telemetry.jsonl`（可用 `RUNTIME_TELEMETRY_FILE` 覆盖，测试用）。
- 总开关：`PIPELINE_RUNTIME=0` 或 `RUNTIME_TELEMETRY=0` → 不写（旧 meta 不变）。
- 无 `run_context` 不写（只有接入 RunContext 的管线才产生记录）。
- 未知 usage：token 字段写 `null`，**不写 0**（0 是"确实用了 0"，会误导）。
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

from .. import config
from . import context as runtime_context

_LOCK = threading.RLock()

# 允许落盘的字段（白名单；其它参数一律不写）
_FIELDS = ("ts", "run_id", "pipeline", "subject", "doc_id", "stage", "status",
           "work_ms", "cache_hit", "calls", "input_tokens", "output_tokens",
           "reasoning_tokens", "finish_reason", "error_class")


def enabled() -> bool:
    """两个开关都开才写：PIPELINE_RUNTIME（默认1） + RUNTIME_TELEMETRY（默认1）。"""
    if not runtime_context.runtime_enabled():
        return False
    return str(os.environ.get("RUNTIME_TELEMETRY", "1")).strip().lower() not in (
        "0", "false", "off", "no", "disabled")


def telemetry_path() -> Path:
    override = str(os.environ.get("RUNTIME_TELEMETRY_FILE", "") or "").strip()
    if override:
        return Path(override)
    return Path(config.LOG_DIR) / "runtime" / "telemetry.jsonl"


def _token(value):
    """未知/缺失 → None；有值 → int。**不把未知记成 0。**"""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def usage_tokens(usage) -> dict:
    """从现有 usage 口径取 token（prompt/completion/reasoning），缺失保持 None。"""
    u = usage if isinstance(usage, dict) else {}
    reasoning = None
    for holder in (u.get("completion_tokens_details"),
                   u.get("output_tokens_details"), u):
        if isinstance(holder, dict) and holder.get("reasoning_tokens") is not None:
            reasoning = holder.get("reasoning_tokens")
            break
    return {"input_tokens": _token(u.get("prompt_tokens")),
            "output_tokens": _token(u.get("completion_tokens")),
            "reasoning_tokens": _token(reasoning)}


def record(stage: str, *, run_context=None, status: str = "ok",
           work_ms=None, cache_hit: bool = False, calls: int = 0,
           input_tokens=None, output_tokens=None, reasoning_tokens=None,
           finish_reason: str = "", error_class: str = "") -> dict | None:
    """追加一条阶段记录；返回写入的 dict（被开关/无 context 关闭时返回 None）。

    调用方只需要给**计数**：不要传 messages/正文/密钥；本函数也只按白名单写字段。
    """
    if run_context is None or not enabled() or not str(stage or "").strip():
        return None
    rec = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "run_id": str(getattr(run_context, "run_id", "") or ""),
        "pipeline": str(getattr(run_context, "pipeline", "") or ""),
        "subject": str(getattr(run_context, "subject_id", "") or ""),
        "doc_id": int(getattr(run_context, "doc_id", 0) or 0),
        "stage": str(stage).strip(),
        "status": str(status or "ok"),
        "work_ms": (None if work_ms is None else round(float(work_ms), 1)),
        "cache_hit": bool(cache_hit),
        "calls": int(calls or 0),
        "input_tokens": _token(input_tokens),
        "output_tokens": _token(output_tokens),
        "reasoning_tokens": _token(reasoning_tokens),
        "finish_reason": str(finish_reason or "")[:40],
        "error_class": str(error_class or "")[:120],
    }
    rec = {k: rec.get(k) for k in _FIELDS}       # 白名单兜底
    path = telemetry_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(rec, ensure_ascii=False) + "\n"
        with _LOCK:
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(line)
    except OSError:
        return None                                   # 写不了不拖垮主流程
    return rec


def read_records(path=None) -> list[dict]:
    """测试/报告用：读回 JSONL（坏行跳过）。"""
    p = Path(path) if path else telemetry_path()
    out = []
    try:
        lines = p.read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for line in lines:
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict):
            out.append(rec)
    return out
