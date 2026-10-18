# -*- coding: utf-8 -*-
"""卡 C-0100：把看板跑在**临时数据根**上的启动器（验收专用，看板自身不引用它）。

为什么必须有它：`subjects.restore()` 会按 `<项目根>\\logs\\subjects.json` 的 `current`
把 config 的 5 条路径重绑到**真实学科库**。本卡要真浏览器验证预习面板，而预习产物会写到
`logs\\knowledge\\<doc_id>-预习总结.json`——**绝不能**落在真 logs / 真库上。
这里把「注册表位置」与「学科根」两处都指到临时目录，于是：

    restore() 读不到注册表 → 回 default 学科
    → `_DEFAULT_PATHS` = 本进程 import config 时的初值 = 上面设的 KB_* 环境变量
    → raw/notes/index/logs 全部落在临时目录里

手法与 `tools\\c0083_tmp_launch.py` / `tools\\c0084_tmp_launch.py` 完全同源（那两张卡的
启动器也是这么隔离的）；**真库只以 `mode=ro` 读**，本启动器不写它一个字节。

用法：
    python tools\\c0100_tmp_launch.py <临时数据根> <端口>
"""
from __future__ import annotations

import json
import os
import runpy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    if len(sys.argv) < 3:
        print("用法: python tools\\c0100_tmp_launch.py <临时数据根> <端口>")
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
    # ★ 配置文件也要隔离：否则设置中心读的/写的是用户**真的** `config.local.json`
    #   （卡 C-0059 留的 `KB_SETTINGS_FILE` 就是给验证脚本干这个用的）。
    os.environ["KB_SETTINGS_FILE"] = str(tmp / "config.local.json")

    sys.path.insert(0, str(ROOT))
    import core.config as cfg                 # noqa: E402  此时已按上面的 env 取好初值
    import core.subjects as subjects_mod      # noqa: E402

    # ⚠ `config.ROOT` 一并指到临时数据根：`_norm_doc_path()` 靠 `RAW_DIR.relative_to(ROOT)`
    #   算"raw 相对项目根的前缀"，数据根留在项目根之外会让它退化成 ""。
    #   `BUNDLE_ROOT` **不动**——它给出随包资源（提示词种子等）的真实位置。
    cfg.ROOT = tmp
    subjects_mod.registry_path = lambda: tmp / "logs" / "subjects.json"   # type: ignore[assignment]
    subjects_mod.subjects_root = lambda: tmp / "subjects"                 # type: ignore[assignment]

    print("[C-0100 临时实例] 数据根 = %s" % tmp)
    print("[C-0100 临时实例] db=%s" % cfg.DB_PATH)
    print("[C-0100 临时实例] logs=%s" % cfg.LOG_DIR)
    # 显式写一个**临时**配置：预习开关关着（人的口径就是默认关）。
    # 这样"未开启"这条分支既真实又是显式的，且与用户的 config.local.json 无关。
    if not cfg.SETTINGS_FILE.exists():
        cfg.SETTINGS_FILE.write_text(
            json.dumps({"PREVIEW_ENABLED": 0}, ensure_ascii=False), encoding="utf-8")
    print("[C-0100 临时实例] 配置文件=%s（PREVIEW_ENABLED=%s）"
          % (cfg.SETTINGS_FILE, cfg.value_of("PREVIEW_ENABLED")))
    print("[C-0100 临时实例] 关闭开关（默认）→ 预习面板应显示「未开启」分支")
    sys.stdout.flush()

    sys.argv = ["dashboard.py", "--host", "127.0.0.1", "--port", port, "--no-open"]
    # 真实入口一行都不改：直接以 __main__ 跑看板。
    runpy.run_path(str(ROOT / "tools" / "dashboard.py"), run_name="__main__")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
