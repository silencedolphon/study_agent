# -*- coding: utf-8 -*-
"""steward_review_watch 的 **blocked 监控**功能测试（临时账本，不污染真实状态）。

四个场景：
  1. 启动时账本里**已经有** blocked 卡 → 只记为基线，**不该**误叫；
  2. 运行中**新出现** blocked 卡 → 立即唤醒并退出，摘要含卡号与原因；
  3. review 唤醒（原有能力）**回归**仍工作；
  4. 离开 blocked 时不叫、**二次卡死重新叫**。

用法： venv\\Scripts\\python.exe -X utf8 tools\\check_steward_review_watch.py
结论行在最前面（本项目规范）。
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

PY = r"G:\study_agent\venv\Scripts\python.exe"