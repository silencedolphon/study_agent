# -*- coding: utf-8 -*-
"""卡 C-0074 首次运行探测：干净部署 exe，无密钥环境启动，采集目录/日志/接口证据。

**它回答的问题**：「对方下载了一个 exe、什么环境都没有，双击之后到底发生了什么？」
把分发包**真复制**到干净目录（等价于用户解压 zip）后启动，记录：

  · 启动前/后 exe 同级目录里多了什么（该建的目录建了没）；
  · `index\\study.db` 建出来没有、建了哪几张表；
  · stdout/stderr 的真实输出；
  · 首屏接口（`/api/firstrun` 的 `empty` / `credential`）到底是"空库 + 没密钥"还是别的。

⚠ 部署**必须真复制**，不能用 `_internal` junction：junction 会让 `sys.executable`
  解析到构建目录，数据写回 `release\\` 而不是"exe 同级"，测出来的结论是假的。
⚠ 数据落在**部署目录**里，绝不碰真实库与 8756 常驻实例（端口从 8791 起找一个空的）。

用法：
    <python> tools\\probe_c0074_firstrun.py --tag before [--exe <exe 路径>]
              [--keep] [--reuse] [--mode copy|junction] [--with-key] [--blank-creds]

`--exe` 缺省指向 `release\\C-0073\\StudyDashboard\\StudyDashboard.exe`；
若该目录正被重新打包（会短暂消失），用 `--exe` 指到别处的分发包即可。
"""
from __future__ import annotations
