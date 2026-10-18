# -*- coding: utf-8 -*-
"""卡 C-0074「三步上手」实测：照《使用说明.md》第二、三步真的走一遍。

自造一份**通用内容**的小 PDF（不含任何真实资料）→ 放进 raw\\ → 调看板本地
接口入库 → 用唯一暗号检索，并检查命中是否带出处。

全程只走看板本地接口，`no_figures=True` → **不调任何模型、¥0**。

用法：
    <python> tools\\probe_c0074_three_steps.py --base http://127.0.0.1:8792
              --raw <干净副本的 raw 目录> [--json <结果 JSON>]

⚠ 只对**干净副本**的 raw 目录操作（用 --raw 指定），绝不碰真实库。
"""
from __future__ import annotations
