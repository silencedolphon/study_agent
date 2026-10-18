"""卡 C-0083：把看板跑在**临时数据根**上的启动器（验收专用，看板自身不引用它）。

为什么必须有它，而不是直接 `python tools\\dashboard.py --port N`：
  `subjects.restore()` 会按 `<项目根>\\logs\\subjects.json` 的 `current`
  把 config 的 5 条路径**重绑到真实学科库**。C-0083 的验收要真的往
  `raw\\` 里写文件、还要试重名改名，绝不能落在真库上。
  这里把「注册表位置」和「学科根」两处都指到临时目录，于是：
      restore() 读不到注册表 → 回 default 学科
      → `_DEFAULT_PATHS` = 本进程 import config 时的初值 = 上面设的 KB_* 环境变量
      → raw/notes/index/logs 全部落在临时目录里。

⚠ 只在验收脚本里用；它**不改任何真源文件**，也不写项目根的 logs\\。
用法：
    python tools\\c0083_tmp_launch.py <临时数据根> <端口>
"""
from __future__ import annotations

import os
import runpy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    if len(sys.argv) < 3:
        print("用法: python tools\\c0083_tmp_launch.py <临时数据根> <端口>")
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
    import core.subjects as subjects_mod   # 此时 config 已按上面的 env 取好初值

    # 注册表与学科根都挪到临时目录：restore() 找不到注册表 → default 学科
    # → paths_of("default") 返回 `_DEFAULT_PATHS`，正是临时那套路径。
    subjects_mod.registry_path = lambda: tmp / "logs" / "subjects.json"   # type: ignore[assignment]
    subjects_mod.subjects_root = lambda: tmp / "subjects"                 # type: ignore[assignment]

    # 真实的看板入口，一行都不改：跟人在命令行敲是同一份代码。
    sys.argv = ["dashboard.py", "--host", "127.0.0.1", "--port", port, "--no-open"]
    runpy.run_path(str(ROOT / "tools" / "dashboard.py"), run_name="__main__")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
