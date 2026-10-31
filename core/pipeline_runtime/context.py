"""冻结的运行上下文（P0-A / S01）。

**一个任务入口只读一次设置**，之后线程/线程池/重试都显式传这个不可变快照，
不再从 `threading.local` / 全局 `config` 现读——多学科/多管线并行时就不会串设置。

约定：
- `max_tokens = 0` 表示**不发送 max_tokens**（无显式上限）。
- `credential_ref` 只存**引用**（pipeline 名），绝不存密钥正文；发送时才按它去
  `config.pipeline_key(...)` 解析，取不到再走原凭据链。
- `PIPELINE_RUNTIME=0` → `runtime_enabled()` 为 False；调用方保持旧全局路径。
"""
from __future__ import annotations

import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from .. import config

_PIPELINE_MODEL_KEYS = {
    "chat": "CHAT_MODEL",
    "kp": "KP_BUILD_MODEL",
    "courseware_kp": "KP_BUILD_MODEL",
    "terms": "KP_BUILD_MODEL",
    "preview": "PREVIEW_MODEL",
    "deck": "DECK_CHAT_MODEL",
    "deck_chat": "DECK_CHAT_MODEL",
    "ingest": "INGEST_MODEL",
    "ingest_vision": "DEEPSEEK_VISION_MODEL",
    "ingest_outline": "INGEST_MODEL",
    "structures": "DEEPSEEK_VISION_MODEL",
    "textbook_kp": "TEXTBOOK_KP_MODEL",
    "textbook_teach": "KP_BUILD_MODEL",
}
_PIPELINE_BASE_KEYS = {
    "chat": "CHAT_BASE_URL",
    "kp": "KP_BUILD_BASE_URL",
    "courseware_kp": "KP_BUILD_BASE_URL",
    "terms": "KP_BUILD_BASE_URL",
    "preview": "PREVIEW_BASE_URL",
    "deck": "DECK_CHAT_BASE_URL",
    "deck_chat": "DECK_CHAT_BASE_URL",
    "ingest": "DEEPSEEK_BASE_URL",
    "ingest_vision": "DEEPSEEK_BASE_URL",
    "ingest_outline": "DEEPSEEK_BASE_URL",
    "structures": "DEEPSEEK_BASE_URL",
    "textbook_kp": "TEXTBOOK_KP_BASE_URL",
    "textbook_teach": "KP_BUILD_BASE_URL",
}
_PIPELINE_THINKING_KEYS = {
    "chat": "CHAT_THINKING",
    "kp": "KP_BUILD_THINKING",
    "courseware_kp": "KP_BUILD_THINKING",
    "terms": "KP_BUILD_THINKING",
    "preview": "PREVIEW_THINKING",
    "deck": "DECK_CHAT_THINKING",
    "deck_chat": "DECK_CHAT_THINKING",
    "ingest": "FIGURE_THINKING",
    "ingest_vision": "FIGURE_THINKING",
    "ingest_outline": "OUTLINE_THINKING",
    "structures": "FIGURE_THINKING",
    "textbook_kp": "TEXTBOOK_KP_THINKING",
    "textbook_teach": "KP_BUILD_THINKING",
}
_PIPELINE_EFFORT_KEYS = {
    "chat": "CHAT_REASONING_EFFORT",
    "kp": "KP_BUILD_REASONING_EFFORT",
    "courseware_kp": "KP_BUILD_REASONING_EFFORT",
    "terms": "KP_BUILD_REASONING_EFFORT",
    "preview": "PREVIEW_REASONING_EFFORT",
    "deck": "DECK_CHAT_REASONING_EFFORT",
    "deck_chat": "DECK_CHAT_REASONING_EFFORT",
    "ingest": "FIGURE_REASONING_EFFORT",
    "ingest_vision": "FIGURE_REASONING_EFFORT",
    "ingest_outline": "OUTLINE_REASONING_EFFORT",
    "structures": "FIGURE_REASONING_EFFORT",
    "textbook_kp": "TEXTBOOK_KP_REASONING_EFFORT",
    "textbook_teach": "KP_BUILD_REASONING_EFFORT",
}

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-flash"


# 细粒度管线名 → 设置中心管线名（**凭据引用**专用）。
#
# 背景（GPT 独立验收 3C）：`RunContext` 原来直接把细粒度 pipeline 名写进
# `credential_ref`（如 courseware_kp），而 `config.pipeline_key()` 只认设置中心
# 的管线名（chat/kp/ingest/deck/preview/textbook_kp）；找不到就回退
# `chat.load_api_key()`，后者可能拿到"最近激活管线"的密钥 —— 不同管线配了
# 不同 key 时会用错钥匙。这里显式给出映射；**只在 context/凭据引用层用**，
# 不改设置中心 API、不影响 `pipeline` 字段本身（审计仍看得到细粒度管线名）。
CREDENTIAL_ALIASES: dict[str, str] = {
    "courseware_kp": "kp",
    "textbook_teach": "kp",
    "terms": "kp",
    "ingest_vision": "ingest",
    "structures": "ingest",
    "ingest_outline": "ingest",
    "deck_chat": "deck",
}


def canonical_credential_ref(pipeline: str) -> str:
    """把细粒度管线名映射成 `config.pipeline_key()` 认识的设置中心管线名。

    - 已是设置中心管线名（chat/kp/ingest/deck/preview/textbook_kp）→ 原样返回；
    - 未知名字 → 原样返回（保留旧行为，后续仍可回退凭据链）；
    - 只做字符串映射，**不读密钥、不读最近管线**。
    """
    name = str(pipeline or "").strip()
    return CREDENTIAL_ALIASES.get(name, name)


def runtime_enabled() -> bool:
    """`PIPELINE_RUNTIME=0/false/off/no` 关闭；其它（含未设置）默认开。"""
    return str(os.environ.get("PIPELINE_RUNTIME", "1")).strip().lower() not in (
        "0", "false", "off", "no", "disabled")


def runtime_status() -> dict:
    """回退/联调用的说明：旧路径**不保证隔离**，测试不能算隔离通过。"""
    return {"enabled": runtime_enabled(),
            "mode": "run_context" if runtime_enabled() else "legacy_global",
            "isolation": bool(runtime_enabled())}


def _field_default(key: str, fallback):
    try:
        return config._FIELDS_BY_KEY[key]["default"]
    except Exception:  # noqa: BLE001
        return fallback


def _setting(key: str, fallback):
    try:
        value = config.value_of(key)
    except Exception:  # noqa: BLE001
        return fallback
    return fallback if value is None else value


def _current_subject() -> str:
    try:
        from .. import subjects
        return str(subjects.current() or "default")
    except Exception:  # noqa: BLE001
        return "default"


@dataclass(frozen=True)
class RunContext:
    run_id: str
    pipeline: str
    subject_id: str
    db_path: Path
    log_dir: Path
    model: str
    api_base: str
    thinking: bool
    reasoning_effort: str
    max_tokens: int
    prompt_version: str = ""
    schema_version: str = ""
    credential_ref: str = ""
    doc_id: int = 0

    def public_dict(self) -> dict:
        """可安全落日志/报告的字段（**没有密钥**）。"""
        return {
            "run_id": self.run_id, "pipeline": self.pipeline,
            "subject_id": self.subject_id, "db_path": str(self.db_path),
            "log_dir": str(self.log_dir), "model": self.model,
            "api_base": self.api_base, "thinking": bool(self.thinking),
            "reasoning_effort": self.reasoning_effort,
            "max_tokens": int(self.max_tokens),
            "prompt_version": self.prompt_version,
            "schema_version": self.schema_version,
            "credential_ref": self.credential_ref,
            "doc_id": int(self.doc_id),
        }


def make_context(pipeline: str, subject=None, doc_id: int = 0) -> RunContext:
    """在任务入口读一次**实际设置**做快照；之后配置再改也不影响这个 context。

    - 模型/端点：按管线自己的设置键（没有则 deepseek-flash / 官方端点）。
    - thinking/effort：按管线自己的设置键（没有就 false / ""）。
    - max_tokens：`config.token_cap(pipeline)`（0 = 不发送）。
    - 密钥：只存 `credential_ref = canonical_credential_ref(pipeline)`（设置中心管线名），
      不存正文；不在此读/写"最近激活管线"。
    """
    pipeline = str(pipeline or "chat").strip() or "chat"
    model_key = _PIPELINE_MODEL_KEYS.get(pipeline, "")
    base_key = _PIPELINE_BASE_KEYS.get(pipeline, "")
    think_key = _PIPELINE_THINKING_KEYS.get(pipeline, "")
    effort_key = _PIPELINE_EFFORT_KEYS.get(pipeline, "")

    model = str(_setting(model_key, _field_default(model_key, DEFAULT_MODEL))
                or DEFAULT_MODEL).strip() if model_key else DEFAULT_MODEL
    api_base = str(_setting(base_key, _field_default(base_key, DEFAULT_BASE_URL))
                   or DEFAULT_BASE_URL).strip() if base_key else DEFAULT_BASE_URL
    if think_key:
        raw_think = _setting(think_key, _field_default(think_key, 0))
        try:
            thinking = bool(int(raw_think))
        except (TypeError, ValueError):
            thinking = bool(raw_think)
    else:
        thinking = False
    effort = str(_setting(effort_key, _field_default(effort_key, "")) or "").strip() \
        if effort_key else ""
    try:
        cap = int(config.token_cap(pipeline) or 0)
    except Exception:  # noqa: BLE001
        cap = 0
    max_tokens = cap if cap > 0 else 0

    return RunContext(
        run_id="%s-%s-%s" % (pipeline, time.strftime("%Y%m%dT%H%M%S"),
                             uuid.uuid4().hex[:8]),
        pipeline=pipeline,
        subject_id=str(subject if subject is not None else _current_subject()),
        db_path=Path(config.DB_PATH),
        log_dir=Path(config.LOG_DIR),
        model=model or DEFAULT_MODEL,
        api_base=api_base or DEFAULT_BASE_URL,
        thinking=bool(thinking),
        reasoning_effort=effort,
        max_tokens=max_tokens,
        prompt_version=str(os.environ.get("PIPELINE_PROMPT_VERSION", "") or ""),
        schema_version=str(os.environ.get("PIPELINE_SCHEMA_VERSION", "") or ""),
        credential_ref=canonical_credential_ref(pipeline),
        doc_id=int(doc_id or 0),
    )
