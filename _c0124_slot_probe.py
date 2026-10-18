# -*- coding: utf-8 -*-
"""C-0124 打桩探针 ①：闸门槽位获取/释放成对性（零花费，不调模型）。

验的是 task-1 的**第一嫌疑**：`core/pipeline_pool.py::_model_slot`（C-0108/C-0111/C-0120
反复改过的那块）取槽/还槽是否成对、异常路径是否泄漏、两种协议分派是否都通。

判据：
  1. `kp_build.model_slot` 是**上下文管理器**（带 @contextlib.contextmanager），
     不是裸生成器 —— 这是 C-0108/C-0111 反复漂移的那个点；
  2. `pipeline_pool._model_slot` 走的是 `with` 分支（`__enter__/__exit__` 都在）；
  3. 正常退出后 `inflight == 0`（槽位归还）；
  4. **异常路径**（生成器内抛错）后 `inflight == 0`；
  5. 串行/并发各 200 次取槽后 `inflight == 0` 且 `acquired == 期望次数`；
  6. `getattr(_model_slot, '__wrapped__')` 存在 ⇒ 它自己是 contextmanager 包装过的。

任何一条不满足 → 退出码 1。
"""
import os
import sys
import threading
import traceback
import contextlib
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, r"D:\study_agent")

failures = []
checks = []


def check(name, ok, detail=""):
    checks.append((name, bool(ok), detail))
    if not ok:
        failures.append(name)
    print("%-58s %s %s" % (name, "PASS" if ok else "**FAIL**", detail))


def main():
    from core import kp_build
    from core import pipeline_pool

    # ---- 判据 1：协议形态 ----
    slot_obj = kp_build.model_slot("probe")
    check("kp_build.model_slot 返回上下文管理器（非裸生成器）",
          hasattr(slot_obj, "__enter__") and hasattr(slot_obj, "__exit__"),
          "type=%s" % type(slot_obj).__name__)

    # ---- 判据 2：pipeline_pool._model_slot 两种协议分派 ----
    pp_slot = pipeline_pool._model_slot("probe")
    check("pipeline_pool._model_slot 返回上下文管理器",
          hasattr(pp_slot, "__enter__") and hasattr(pp_slot, "__exit__"),
          "type=%s" % type(pp_slot).__name__)

    # ---- 判据 6：垫片本身被 contextmanager 包装 ----
    check("pipeline_pool._model_slot 带 @contextlib.contextmanager",
          getattr(pipeline_pool._model_slot, "__wrapped__", None) is not None)

    # ---- 判据 3：正常路径归还 ----
    kp_build.configure_model_gate(4)
    with pipeline_pool._model_slot("probe"):
        st_in = kp_build.model_gate_stats()
        check("持槽期间 inflight == 1", st_in["inflight"] == 1, "inflight=%d" % st_in["inflight"])
    st_out = kp_build.model_gate_stats()
    check("正常退出后 inflight 归还为 0", st_out["inflight"] == 0, "inflight=%d" % st_out["inflight"])

    # ---- 判据 4：异常路径归还 ----
    kp_build.configure_model_gate(4)
    try:
        with pipeline_pool._model_slot("probe"):
            raise RuntimeError("故意炸")
    except RuntimeError:
        pass
    st_exc = kp_build.model_gate_stats()
    check("异常退出后 inflight 归还为 0", st_exc["inflight"] == 0,
          "inflight=%d" % st_exc["inflight"])

    # ---- 判据 4b：kp_build.model_slot 自身直接使用（不走垫片）的异常路径 ----
    kp_build.configure_model_gate(4)
    try:
        with kp_build.model_slot("probe"):
            raise RuntimeError("故意炸2")
    except RuntimeError:
        pass
    check("kp_build.model_slot 异常退出后 inflight == 0",
          kp_build.model_gate_stats()["inflight"] == 0,
          "inflight=%d" % kp_build.model_gate_stats()["inflight"])

    # ---- 判据 5：并发 200 次取槽，不允许泄漏 ----
    kp_build.configure_model_gate(4)
    N = 200
    peak_seen = []

    def one():
        with pipeline_pool._model_slot("probe"):
            time_sleep = 0.002
            import time as _t
            _t.sleep(time_sleep)
        return 1

    with ThreadPoolExecutor(max_workers=8) as ex:
        list(ex.map(lambda _i: one(), range(N)))
    st = kp_build.model_gate_stats()
    check("并发 %d 次取槽后 inflight 归零" % N, st["inflight"] == 0,
          "inflight=%d" % st["inflight"])
    check("acquired 计数 == %d" % N, st["acquired"] == N, "acquired=%d" % st["acquired"])
    check("peak_inflight 未超闸门值 4", st["peak_inflight"] <= 4,
          "peak=%d limit=%d" % (st["peak_inflight"], st["limit"]))

    # ---- 判据 5b：闸门值 1 时串行不泄漏（最严格的成对性）----
    kp_build.configure_model_gate(1)
    for _ in range(50):
        with kp_build.model_slot("probe"):
            pass
    st1 = kp_build.model_gate_stats()
    check("闸门值=1 串行 50 次后 inflight == 0", st1["inflight"] == 0,
          "inflight=%d acquired=%d" % (st1["inflight"], st1["acquired"]))

    # ---- 判据 7：ModelSlotTimeout 路径也要归还（抢不到时不能占槽）----
    kp_build.configure_model_gate(1)
    held = threading.Event()
    release = threading.Event()

    def hog():
        with kp_build.model_slot("hog"):
            held.set()
            release.wait(10)

    th = threading.Thread(target=hog, daemon=True)
    th.start()
    held.wait(5)
    try:
        with kp_build.model_slot("probe", timeout=0.4):
            check("抢槽超时应抛 ModelSlotTimeout", False, "竟然抢到了")
    except kp_build.ModelSlotTimeout:
        check("抢槽超时应抛 ModelSlotTimeout", True, "ModelSlotTimeout")
    except Exception as e:
        check("抢槽超时应抛 ModelSlotTimeout", False, "%s: %s" % (type(e).__name__, e))
    release.set()
    th.join(5)
    st2 = kp_build.model_gate_stats()
    check("超时失败后 inflight 归零", st2["inflight"] == 0, "inflight=%d" % st2["inflight"])

    print("\n==== 汇总：%d/%d 通过 ====" % (len(checks) - len(failures), len(checks)))
    if failures:
        print("失败项：")
        for f in failures:
            print("  - " + f)
        return 1
    print("结论：槽位获取/释放**成对**，无泄漏；两种协议分派均可用。")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(2)
