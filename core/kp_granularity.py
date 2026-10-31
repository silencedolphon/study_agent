"""S14 节点粒度实验：只读模拟「同父相邻小节点合并」方案。

- **不修改 tasks、不落盘、不调模型**；只返回合并方案供实验/报告。
- `KP_MERGE_SMALL_NODES` 默认 0；即使为 1，本轮也**不接入真实 build**
  （返回 `applied=False` 明确说明），避免改变现有知识点产物。
"""
from __future__ import annotations

import copy
import os

ENV_KEY = "KP_MERGE_SMALL_NODES"
DEFAULT_SMALL_PAGES = 2
DEFAULT_SMALL_CHARS = 3000
DEFAULT_MAX_PAGES = 6
DEFAULT_MAX_CHARS = 8000


def merge_experiment_enabled() -> bool:
    """默认 0：实验关闭。"""
    return str(os.environ.get(ENV_KEY, "0")).strip().lower() in (
        "1", "true", "on", "yes")


def _parent(node_path) -> tuple:
    path = list(node_path or [])
    return tuple(path[:-1])


def plan_small_node_merge(tasks, *, max_pages: int = DEFAULT_SMALL_PAGES,
                          max_chars: int = DEFAULT_SMALL_CHARS,
                          enabled: bool = False,
                          hard_max_pages: int = DEFAULT_MAX_PAGES,
                          hard_max_chars: int = DEFAULT_MAX_CHARS) -> dict:
    """只读计划：把**同一父节点下相邻**的小节点（小页数/小字符）尝试合并。

    返回 `{"enabled","groups","n_groups","merged_nodes","applied":False,...}`。
    `tasks` 不会被修改（内部 deepcopy 只用于读取）；关闭时返回空计划。
    """
    enabled = bool(enabled) or merge_experiment_enabled()
    input_tasks = list(tasks or [])
    result = {
        "enabled": bool(enabled), "applied": False,
        "reason": "experiment only; not wired into build_v2 (S14)",
        "input_tasks": len(input_tasks), "groups": [], "n_groups": 0,
        "merged_nodes": 0, "max_pages": int(max_pages),
        "max_chars": int(max_chars), "hard_max_pages": int(hard_max_pages),
        "hard_max_chars": int(hard_max_chars), "warnings": [],
        "saved_calls": 0, "page_coverage_unchanged": True,
    }
    if not enabled:
        result["warnings"].append("KP_MERGE_SMALL_NODES=0（默认关闭）")
        return result
    snapshot = copy.deepcopy(input_tasks)
    groups = []
    current = []
    current_pages: set = set()
    current_chars = 0
    current_parent = None

    def flush():
        nonlocal current, current_pages, current_chars
        if len(current) >= 2:
            groups.append({
                "parent_path": list(current_parent or ()),
                "kp_ids": [str(t.get("kp_id")) for t in current],
                "node_ids": [t.get("node_id") for t in current],
                "node_names": [t.get("node_name") for t in current],
                "pages": sorted(current_pages),
                "n_pages": len(current_pages),
                "chars": int(current_chars),
                "before_nodes": len(current), "after_nodes": 1,
                "saved_calls": len(current) - 1,
                "pages_before": sorted(current_pages),
                "pages_after": sorted(current_pages),
                "page_coverage_unchanged": True,
                "risk_flags": ["experimental", "not_wired_into_build"],
                "reason": "同父相邻小节点合并模拟（只读实验）",
            })
        current, current_pages, current_chars = [], set(), 0

    for task in input_tasks:
        if not isinstance(task, dict):
            flush()
            current_parent = None
            continue
        parent = _parent(task.get("node_path"))
        pages = {int(p) for p in (task.get("pages") or [])
                 if str(p).isdigit()}
        chars = int(task.get("material_chars_planned") or 0)
        small = len(pages) <= max_pages and chars <= max_chars
        fits = (current and parent == current_parent
                and len(current_pages | pages) <= hard_max_pages
                and current_chars + chars <= hard_max_chars)
        if small and current and fits:
            current.append(task)
            current_pages |= pages
            current_chars += chars
            continue
        flush()
        current_parent = parent
        if small:
            current, current_pages, current_chars = [task], set(pages), chars
        else:
            current_parent = None
    flush()
    result["groups"] = groups
    result["n_groups"] = len(groups)
    result["merged_nodes"] = sum(len(g["kp_ids"]) for g in groups)
    result["saved_calls"] = sum(g["saved_calls"] for g in groups)
    # 只读保证：输入未被修改（若调用方在遍历中改过，这里也能如实反映）
    result["input_unchanged"] = (snapshot == list(tasks or []))
    if not groups:
        result["warnings"].append("没有满足条件的同父相邻小节点")
    return result
