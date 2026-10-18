"""卡 C-0084：把看板跑在**临时数据根**上的启动器（验收专用，看板自身不引用它）。

与 `tools/c0083_tmp_launch.py` 同一个套路，多一件事：**测试钩子**。

为什么必须有它：`subjects.restore()` 会按 `<项目根>\\logs\\subjects.json` 的
`current` 把 config 的 5 条路径重绑到**真实学科库**。C-0084 验的是**不可逆的真删**，
绝不能落在真库上。这里把「注册表位置」和「学科根」两处都指到临时目录，于是：
    restore() 读不到注册表 → 回 default 学科
    → `_DEFAULT_PATHS` = 本进程 import config 时的初值 = 上面设的 KB_* 环境变量
    → raw/notes/index/logs 全部落在临时目录里。

测试钩子（仅在 `C0084_TEST_HOOK=1` 时注册，生产路径零改动）：
    POST /api/_test/running {"label": "入库"}  → 让 `_subject_running()` 报"正在跑"
    POST /api/_test/running {"label": ""}      → 复位

它只是**操作真实的状态对象**（`core/progress.begin/reset`），不是另写一条捷径：
`_subject_running()`（dashboard 里那段守卫）读的就是它，于是"有任务在跑时拒绝删除"
这条纪律能按真实代码路径验，不必真的去跑一次几十分钟的入库。

用法：
    python tools\\c0084_tmp_launch.py <临时数据根> <端口>
"""
from __future__ import annotations

import json
import os
import runpy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _json_of(obj) -> bytes:
    return json.dumps(obj, ensure_ascii=False).encode("utf-8")


def main() -> int:
    if len(sys.argv) < 3:
        print("用法: python tools\\c0084_tmp_launch.py <临时数据根> <端口>")
        return 2
    tmp = Path(sys.argv[1]).resolve()
    port = str(int(sys.argv[2]))
    for sub in ("raw", "notes", "index", "logs"):
        (tmp / sub).mkdir(parents=True, exist_ok=True)

    # 必须在 import core.config 之前设好：config 的路径常量是模块级快照。
    os.environ["KB_RAW"] = str(tmp / "raw")
    os.environ["KB_NOTES"] = str(tmp / "notes")
    os.environ["KB_INDEX"] = str(tmp / "index")
    os.environ["KB_DB"] = str(tmp / "index" / "study.db")
    os.environ["KB_LOG"] = str(tmp / "logs")

    sys.path.insert(0, str(ROOT))
    import core.config as cfg            # 此时 config 已按上面的 env 取好初值
    import core.subjects as subjects_mod

    # ⚠ 必须把 `config.ROOT` 也指到临时数据根，否则**测不准**：
    #   `_norm_doc_path()` 靠 `RAW_DIR.relative_to(ROOT)` 算出"raw 相对项目根的前缀"，
    #   再把 `documents.path`（`raw\药理\x.pdf`）剥成相对 raw 的形态。
    #   数据根若留在项目根之外，这个 relative_to 会抛 ValueError → 前缀退化成 ""
    #   → 路径原样返回 → `_safe_raw_path` 拼成 `raw\raw\...` → **删原文件静默不生效**。
    #   真库（默认学科）下 ROOT 与 RAW_DIR 是父子，所以生产路径本来就是对的；
    #   这里把 ROOT 一并挪过去，就是让临时环境与真实环境在这一点上完全同构。
    #   `config.ROOT` 只被 `_norm_doc_path` / `_rel_display` / `share` 那几处读，
    #   都不参与启动逻辑，挪了不影响看板起不来。
    cfg.ROOT = tmp

    # 注册表与学科根都挪到临时目录：restore() 找不到注册表 → default 学科
    # → paths_of("default") 返回 `_DEFAULT_PATHS`，正是临时那套路径。
    subjects_mod.registry_path = lambda: tmp / "logs" / "subjects.json"   # type: ignore[assignment]
    subjects_mod.subjects_root = lambda: tmp / "subjects"                 # type: ignore[assignment]

    if os.environ.get("C0084_TEST_HOOK") == "1":
        import core.progress as progress_mod

        def _hook(payload, conn):     # noqa: ANN001, ARG001
            label = str(payload.get("label") or "")
            if label:
                progress_mod.begin(label=label, files_total=1)
                progress_mod.stage("分析结构")
            else:
                progress_mod.reset()
            import core.config as cfg
            return {"ok": True,
                    "snapshot_active": bool(progress_mod.snapshot().get("active")),
                    "paths": {"ROOT": str(cfg.ROOT), "RAW_DIR": str(cfg.RAW_DIR),
                              "DB": str(cfg.DB_PATH), "LOG_DIR": str(cfg.LOG_DIR)}}

        def _arm_hook(g: dict) -> None:
            """把钩子挂进 `TEST_HOOKS`（`dashboard.py` 在 `__main__` 段回调它）。

            那一刻模块全局已经稳了（`TEST_HOOKS` 那行早就执行过），钩子要等到
            **第一个请求**才被查表命中——所以挂在这里不会影响看板自己的启动逻辑。
            钩子顺带回一份**看板自己看到的路径**，验收脚本可以先核对"它到底在操作
            哪个数据根"再动手——真删这类用例，这一点必须先钉死。
            """
            g.setdefault("TEST_HOOKS", {})["/api/_test/running"] = _hook
            g.setdefault("TEST_HOOKS", {})["/api/_test/paths"] = (
                lambda payload, conn: {"paths": {          # noqa: ANN001, ARG005
                    "ROOT": str(__import__("core.config", fromlist=["x"]).ROOT),
                    "RAW_DIR": str(__import__("core.config", fromlist=["x"]).RAW_DIR),
                    "DB": str(__import__("core.config", fromlist=["x"]).DB_PATH),
                    "LOG_DIR": str(__import__("core.config", fromlist=["x"]).LOG_DIR)}})

        sys.argv = ["dashboard.py", "--host", "127.0.0.1", "--port", port, "--no-open"]
        # 真实入口一行都不改：钩子挂在 `TEST_HOOKS` 这个**扩展点**上（默认空表）。
        runpy.run_path(str(ROOT / "tools" / "dashboard.py"), run_name="__main__",
                       init_globals={"__c0084_pre__": _arm_hook})
    else:
        sys.argv = ["dashboard.py", "--host", "127.0.0.1", "--port", port, "--no-open"]
        runpy.run_path(str(ROOT / "tools" / "dashboard.py"), run_name="__main__")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
