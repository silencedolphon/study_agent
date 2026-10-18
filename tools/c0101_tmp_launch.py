# -*- coding: utf-8 -*-
"""卡 C-0101：把看板跑在**临时数据根**上的启动器（验收专用，看板自身不引用它）。

血统：`tools\\c0100_tmp_launch.py`（同一套隔离手法，只是把库的来源做成参数）。
为什么必须有它：`subjects.restore()` 会按 `<项目根>\\logs\\subjects.json` 的 `current`
把 config 的 5 条路径重绑到**真实学科库**；而本卡要真浏览器验证预习面板。这里把
「注册表位置」与「学科根」两处都指到临时目录，于是：

    restore() 读不到注册表 → 回 default 学科
    → `_DEFAULT_PATHS` = 本进程 import config 时的初值 = 上面设的 KB_* 环境变量
    → raw/notes/index/logs 全部落在临时目录里

真库只以**文件复制**的方式读一次副本给 data 模式用（`--copy-db`），本启动器
不写它一个字节；`--empty` 模式连副本都不拷，库里什么都没有（验空态用）。

用法：
    python tools\\c0101_tmp_launch.py <临时数据根> <端口> [--copy-db <真库路径>]
"""
from __future__ import annotations

import argparse
import json
import os
import runpy
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("tmp", help="临时数据根（会被建出来）")
    ap.add_argument("port", type=int)
    ap.add_argument("--copy-db", default="",
                    help="把这份库**复制**成临时库（只读源文件；留空 = 空库）")
    a = ap.parse_args()
    tmp = Path(a.tmp).resolve()
    for sub in ("raw", "notes", "index", "logs"):
        (tmp / sub).mkdir(parents=True, exist_ok=True)
    dst = tmp / "index" / "study.db"
    if a.copy_db:
        src = Path(a.copy_db).resolve()
        if not src.exists():
            print("C0101-TMP-FAIL 源库不存在：%s" % src)
            return 2
        shutil.copyfile(src, dst)      # 单向：真库 → 副本
        print("[C-0101 临时实例] 已把真库**复制**成副本：%s → %s" % (src, dst))

    # 必须在 import core.config 之前设好：config 的路径常量是模块级快照。
    os.environ["KB_RAW"] = str(tmp / "raw")
    os.environ["KB_NOTES"] = str(tmp / "notes")
    os.environ["KB_INDEX"] = str(tmp / "index")
    os.environ["KB_DB"] = str(dst)
    os.environ["KB_LOG"] = str(tmp / "logs")
    # ★ 配置文件也要隔离：否则设置中心读的是人**真的** `config.local.json`
    os.environ["KB_SETTINGS_FILE"] = str(tmp / "config.local.json")

    sys.path.insert(0, str(ROOT))
    import core.config as cfg                 # noqa: E402
    import core.subjects as subjects_mod      # noqa: E402

    cfg.ROOT = tmp
    subjects_mod.registry_path = lambda: tmp / "logs" / "subjects.json"   # type: ignore[assignment]
    subjects_mod.subjects_root = lambda: tmp / "subjects"                 # type: ignore[assignment]

    print("[C-0101 临时实例] 数据根 = %s" % tmp)
    print("[C-0101 临时实例] db=%s（存在=%s）" % (cfg.DB_PATH, Path(cfg.DB_PATH).exists()))
    print("[C-0101 临时实例] logs=%s" % cfg.LOG_DIR)
    print("[C-0101 临时实例] 端口 = %d" % a.port)
    if not cfg.SETTINGS_FILE.exists():
        # 开关默认关着（人的口径就是默认关）：面板照样要能列出教材清单，
        # 所以「未开启」提示框与本次验证的列表渲染互不干扰。
        cfg.SETTINGS_FILE.write_text(
            json.dumps({"PREVIEW_ENABLED": 0}, ensure_ascii=False), encoding="utf-8")
    sys.stdout.flush()

    sys.argv = ["dashboard.py", "--host", "127.0.0.1", "--port", str(a.port), "--no-open"]
    # 真实入口一行都不改：直接以 __main__ 跑看板。
    runpy.run_path(str(ROOT / "tools" / "dashboard.py"), run_name="__main__")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
