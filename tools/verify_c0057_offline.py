# -*- coding: utf-8 -*-
"""卡 C-0057 离线验证：独立管线（不检索 / 不用提示词 / 思考强度最高）。

一次跑完，**不联网、不花钱**：

  ① 静态：新管线的函数体里没有任何检索调用（AST 取函数源码再逐个标识符查）；
  ② 静态：新管线的调用路径不读任何提示词文件、不含 SYSTEM_PROMPT；
  ③ 对照：`api_chat_stream`（知识库问答）与 `gather_evidence` / `build_messages` /
     `chat_once` / `SYSTEM_PROMPT` 与本卡**开工前的工作区快照**
     （`<卡目录>/before/`，另附 git HEAD 对照）**逐字相同**；
  ④ 打桩：把 `urllib.request.urlopen` 换掉，真跑一遍 `stream_deck_chat`，
     把**实际 payload** 抓下来看 thinking / reasoning_effort / model / messages；
  ⑤ 消息结构：逐字打印真正发给模型的消息骨架，并断言里面没有 system 角色、
     没有知识库问答的系统提示词片段、没有 logs/knowledge 提示词的内容；
  ⑥ 截断：小课件（预算内）与大课件（doc#89 112 块 / doc#91 教材 867 块）各跑一次
     `build_deck_context`，报送出多少、一共多少、是否截断；
  ⑦ 配置独立：改 `config.DECK_CHAT_MODEL` / `DECK_CHAT_REASONING_EFFORT`
     后 `deck_cfg()` 跟着变，而知识库问答的 `chat.CHAT_MODEL` 一动不动。

用法（在 G:\\study_agent 下）：
    venv\\Scripts\\python.exe -X utf8 tools\\verify_c0057_offline.py
"""
from __future__ import annotations

import ast
import json
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(r"G:\study_agent")
CARD = Path(r"C:\Users\Lenovo\.dsh\steward-lite\consoles"