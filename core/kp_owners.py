"""S16 第一步：把课件知识点的页面预约/释放从 kp_build 抽出（行为逐字不变）。

只搬两个纯函数，不搬 `build_v2`/`gen_kp_v2` 等大函数；`kp_build.claim_pages/
release_pages` 仍可访问（导入别名），旧调用方零改动。
"""
from __future__ import annotations

import contextlib


def claim_pages(claimed, claimed_lock, kp_id, pages):
    """预约 `pages`：返回**本次实际预约到的页**。

已被**别人**占着的页不抢、也不报错——交给 `gen_kp_v2()` 的仲裁如实记进
`pages_conflict_dropped`（先到先得这条规则本身没变，只是"先到"从
"先返回"改成"先预约"）。
"""
    got = []
    cm = claimed_lock if claimed_lock is not None else contextlib.nullcontext()
    with cm:
        for _p in (pages if pages else []):
            try:
                _n = int(_p)
            except (TypeError, ValueError):
                continue
            _cur = claimed.get(_n)
            if _cur and str(_cur) != str(kp_id):
                continue
            claimed[_n] = str(kp_id)
            got.append(_n)
    return got


def release_pages(claimed, claimed_lock, kp_id, pages):
    """回滚预约：把 `pages` 里**还挂在自己名下**的页撤回来。

两处必用（否则覆盖率的"未覆盖原因"会失真——页被占着，却没有知识点收录它）：
  1. 任务**失败**（调用异常）→ 全部预约回滚；
  2. 任务成功但某页**最终没收录**（模型显式排除 / 越界 / 被抢）→ 只回滚那些页。
"""
    cm = claimed_lock if claimed_lock is not None else contextlib.nullcontext()
    with cm:
        for _p in (pages or []):
            try:
                _n = int(_p)
            except (TypeError, ValueError):
                continue
            if str(claimed.get(_n) or "") == str(kp_id):
                claimed.pop(_n, None)
