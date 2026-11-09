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
    "courseware_kp", "terms", "preview", "textbook_kp", "textbook_teach", "knowledge_links",
})


@dataclass(frozen=True)
class AgentCapabilities:
    """Agent adapter 的能力声明（**没有声明 = 旧兼容模式**）。"""

    pipelines: frozenset[str] | None = None
    stream: bool = False
    images: bool = False
    json: bool = False
    usage: bool = True

    def supports_pipeline(self, pipeline: str) -> bool:
        return self.pipelines is None or str(pipeline) in self.pipelines


def _request_has_images(messages) -> bool:
    def walk(obj) -> bool:
        if isinstance(obj, dict):
            t = str(obj.get("type") or "").lower()
            if t in ("image_url", "input_image", "image"):
                return True
            if "image_url" in obj or "image" in obj:
                return True
            return any(walk(v) for v in obj.values())
        if isinstance(obj, (list, tuple)):
            return any(walk(v) for v in obj)
        return False
    return walk(messages)


def adapter_capabilities(adapter):
    """返回 `(AgentCapabilities, declared)`；旧适配器 declared=False 保持兼容。"""
    fn = getattr(adapter, "capabilities", None)
    if not callable(fn):
        return AgentCapabilities(), False
    raw = fn()
    if isinstance(raw, AgentCapabilities):
        return raw, True
    if isinstance(raw, dict):
        pipelines = raw.get("pipelines", None)
        if pipelines is not None:
            pipelines = frozenset(str(x) for x in pipelines) or None
        return AgentCapabilities(
            pipelines=pipelines, stream=bool(raw.get("stream", False)),
            images=bool(raw.get("images", False)),
            json=bool(raw.get("json", False)),
            usage=bool(raw.get("usage", True))), True
    raise ValueError("adapter.capabilities() 必须返回 AgentCapabilities 或 dict")


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
            adapter = _adapters[name]
            caps, _declared = adapter_capabilities(adapter)
            if not caps.supports_pipeline(pipeline):
                raise ValueError(
                    "适配器 %s 声明不支持管线 %s；拒绝绑定，不会静默 fallback 到原模型"
                    % (name, pipeline))
            _routes[pipeline] = name


def _adapter(pipeline: str) -> AgentAdapter | None:
    with _lock:
        name = _routes.get(pipeline)
        return _adapters.get(name) if name else None


def has_adapter(pipeline: str | None = None) -> bool:
    return _adapter(pipeline or current_pipeline()) is not None


def _validate_request(adapter, request: AgentRequest) -> AgentCapabilities:
    """绑定后再次校验：管线/图片/JSON；不支持就明确报错（不 fallback）。"""
    caps, declared = adapter_capabilities(adapter)
    if not caps.supports_pipeline(request.pipeline):
        raise ValueError("适配器不支持管线 %s；拒绝调用，不会 fallback 原模型"
                         % request.pipeline)
    if declared and not caps.images and _request_has_images(request.messages):
        raise ValueError("适配器声明 images=False：拒绝图片请求，"
                         "不会把图片/正文发出去，也不会 fallback 原模型")
    if declared and request.response_format is not None and not caps.json:
        raise ValueError("适配器声明 json=False：拒绝 JSON 输出请求（response_format）")
    return caps


def complete(request: AgentRequest) -> dict[str, Any] | None:
    """Return None only when no adapter is bound; adapter errors propagate."""
    adapter = _adapter(request.pipeline)
    if adapter is None:
        return None
    _validate_request(adapter, request)
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
    _validate_request(adapter, request)
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
