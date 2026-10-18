"""卡 C-0085：把看板跑在**临时数据根**上的启动器（验收专用，看板自身不引用它）。

与 `tools/c0084_tmp_launch.py` 同一个套路（那套已经实践过），差别只有一处：
本卡要验的是**重命名**——它会改 raw 里的真实文件名、改 `documents.path`、
搬 `logs/figure_desc` 下的目录，所以同样**绝不能落在真库上**。

为什么必须有它：`subjects.restore()` 会按 `<项目根>\\logs\\subjects.json` 的 `current`
把 config 的 5 条路径重绑到**真实学科库**。这里把「注册表位置」与「学科根」两处都
指到临时目录，于是夹具里那份注册表（current = 夹具学科）生效，且 5 条路径全部落在
临时数据根里。

测试钩子（仅在 `C0085_TEST_HOOK=1` 时注册，生产路径零改动）：
    POST /api/_test/running {"label": "入库"}  → 让 `_subject_running()` 报"正在跑"
    POST /api/_test/running {"label": ""}      → 复位
    POST /api/_test/paths                      → 回一份**看板自己看到的**路径

钩子操作的是**真实的状态对象**（`core/progress.begin/reset`），不是另写一条捷径：
`_subject_running()`（dashboard 里那段守卫）读的就是它，于是"有任务在跑时拒绝重命名"
这条纪律能按真实代码路径验，不必真的去跑一次几十分钟的入库。

用法：
    python tools\\c0085_tmp_launch.py <临时数据根> <端口>
"""
from __future__ import annotations

import os
import runpy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    if len(sys.argv) < 3:
        print("用法: python tools\\c0085_tmp_launch.py <临时数据根> <端口>")
        return 2
    tmp = Path(sys.argv[1]).resolve()
    port = str(int(sys.argv[2]))
    for sub in ("raw", "notes", "index", "logs", "subjects"):
        (tmp / sub).mkdir(parents=True, exist_ok=True)

    # 必须在 import core.config 之前设好：config 的路径常量是模块级快照。
    os.environ["KB_RAW"] = str(tmp / "raw")
    os.environ["KB_NOTES"] = str(tmp / "notes")
    os.environ["KB_INDEX"] = str(tmp / "index")
    os.environ["KB_DB"] = str(tmp / "index" / "study.db")
    os.environ["KB_LOG"] = str(tmp / "logs")

    sys.path.insert(0, str(ROOT))
    import core.config as cfg                      # noqa: E402
    import core.subjects as subjects_mod           # noqa: E402

    # ⚠ 必须把 `config.ROOT` 也指到临时数据根（与 C-0084 同一个理由）：
    #   `_norm_doc_path()` 靠 `RAW_DIR.relative_to(ROOT)` 算"raw 相对项目根的前缀"，
    #   再把 `documents.path`（`raw\05. Med Chem.pptx`）剥成相对 raw 的形态；
    #   本卡改名时算新 path 也要走这条口径，ROOT 不挪就会得到与真库不同构的结果。
    cfg.ROOT = tmp

    subjects_mod.registry_path = lambda: tmp / "logs" / "subjects.json"   # type: ignore[assignment]
    subjects_mod.subjects_root = lambda: tmp / "subjects"                 # type: ignore[assignment]

    if os.environ.get("C0085_TEST_HOOK") == "1":
        import core.progress as progress_mod

        def _hook(payload, conn):     # noqa: ANN001, ARG001
            label = str(payload.get("label") or "")
            if label:
                progress_mod.begin(label=label, files_total=1)
                progress_mod.stage("分析结构")
            else:
                progress_mod.reset()
            import core.config as c
            return {"ok": True,
                    "snapshot_active": bool(progress_mod.snapshot().get("active")),
                    "paths": {"ROOT": str(c.ROOT), "RAW_DIR": str(c.RAW_DIR),
                              "DB": str(c.DB_PATH), "LOG_DIR": str(c.LOG_DIR)}}

        def _arm_hook(g: dict) -> None:
            """把钩子挂进 `TEST_HOOKS`（`dashboard.py` 在 `__main__` 段回调它）。"""
            g.setdefault("TEST_HOOKS", {})["/api/_test/running"] = _hook
            g.setdefault("TEST_HOOKS", {})["/api/_test/paths"] = (
                lambda payload, conn: {"paths": {          # noqa: ANN001, ARG005
                    "ROOT": str(__import__("core.config", fromlist=["x"]).ROOT),
                    "RAW_DIR": str(__import__("core.config", fromlist=["x"]).RAW_DIR),
                    "DB": str(__import__("core.config", fromlist=["x"]).DB_PATH),
                    "LOG_DIR": str(__import__("core.config", fromlist=["x"]).LOG_DIR)}})

        sys.argv = ["dashboard.py", "--host", "127.0.0.1", "--port", port, "--no-open"]
        runpy.run_path(str(ROOT / "tools" / "dashboard.py"), run_name="__main__",
                       init_globals={"__c0084_pre__": _arm_hook})
    else:
        sys.argv = ["dashboard.py", "--host", "127.0.0.1", "--port", port, "--no-open"]
        runpy.run_path(str(ROOT / "tools" / "dashboard.py"), run_name="__main__")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
