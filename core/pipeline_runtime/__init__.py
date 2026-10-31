"""P0-A pipeline runtime：冻结的 RunContext（一次任务快照）+ 最小 telemetry。

只写 scope：foundation + `textbook_kp` 试点。默认 `PIPELINE_RUNTIME=1`；
`PIPELINE_RUNTIME=0` 时调用方走旧全局路径（不保证隔离，也不写 telemetry）。
"""
from __future__ import annotations

from .context import (RunContext, make_context, runtime_enabled,
                      runtime_status)
from . import telemetry

__all__ = ["RunContext", "make_context", "runtime_enabled", "runtime_status",
           "telemetry"]
