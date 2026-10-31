"""Opt-in, in-process adapter boundary for model and future agent backends.

Adapters are registered and bound by trusted application code.  Nothing is
discovered, launched, or enabled automatically, and credentials are never
included in AgentRequest.  With no binding the existing model path is used.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from threading import RLock
from typing import Any, Iterator, Protocol


PIPELINES = frozenset({
    "chat", "deck_chat", "ingest_vision", "ingest_outline", "structures",
    "courseware_kp", "terms", "preview", "textbook_kp", "textbook_teach",
})


@dataclass(frozen=True)
class AgentRequest:
    pipeline: str
    messages: list[dict[str, Any]]
    model: str
    max_tokens: int
    thinking: bool
    reasoning_effort: str | None
    temperature: float
    timeout: int
    stream: bool = False
    response_format: dict[str, Any] | None = None


class AgentAdapter(Protocol):
    def complete(self, request: AgentRequest) -> dict[str, Any]: ...


_pipeline: ContextVar[str] = ContextVar("study_agent_pipeline", default="chat")
_lock = RLock()
_adapters: dict[str, AgentAdapter] = {}
_routes: dict[str, str] = {}


@contextmanager
def pipeline_scope(pipeline: str) -> Iterator[None]:
    if pipeline not in PIPELINES:
        raise ValueError("未知管线: " + str(pipeline))
    token = _pipeline.set(pipeline)
    try:
        yield
    finally:
        _pipeline.reset(token)


def current_pipeline() -> str:
    return _pipeline.get()


def register_adapter(name: str, adapter: AgentAdapter) -> None:
    if not name or not callable(getattr(adapter, "complete", None)):
        raise ValueError("适配器必须有名称和 complete 方法")
    with _lock:
        _adapters[name] = adapter


def bind_adapter(pipeline: str, name: str | None) -> None:
    """Bind a registered adapter to one pipeline, or restore the default."""
    if pipeline not in PIPELINES:
        raise ValueError("未知管线: " + str(pipeline))
    with _lock:
        if name is None:
            _routes.pop(pipeline, None)
        elif name not in _adapters:
            raise ValueError("适配器尚未注册: " + str(name))
        else:
            _routes[pipeline] = name


def _adapter(pipeline: str) -> AgentAdapter | None:
    with _lock:
        name = _routes.get(pipeline)
        return _adapters.get(name) if name else None


def has_adapter(pipeline: str | None = None) -> bool:
    return _adapter(pipeline or current_pipeline()) is not None


def complete(request: AgentRequest) -> dict[str, Any] | None:
    """Return None only when no adapter is bound; adapter errors propagate."""
    adapter = _adapter(request.pipeline)
    if adapter is None:
        return None
    result = adapter.complete(request)
    if not isinstance(result, dict) or not isinstance(result.get("content"), str):
        raise ValueError("适配器返回格式不正确：需要 content 字符串")
    return {"content": result["content"], "model": result.get("model") or request.model,
            "usage": result.get("usage") or {}, "elapsed": result.get("elapsed") or 0.0,
            "finish_reason": result.get("finish_reason") or ""}


def stream(request: AgentRequest):
    """Return an event iterator, or None when no adapter is bound.

    A non-streaming adapter is supported by emitting its complete response as
    one delta.  A streaming adapter yields (delta|usage|done, value) pairs.
    """
    adapter = _adapter(request.pipeline)
    if adapter is None:
        return None
    fn = getattr(adapter, "stream", None)
    if callable(fn):
        return fn(request)

    def once():
        result = complete(request)
        if result is None:
            raise RuntimeError("适配器在调用中被取消绑定")
        yield "delta", result["content"]
        yield "usage", result["usage"]
        yield "done", None
    return once()
