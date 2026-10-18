# -*- coding: utf-8 -*-
"""C-0057 真实调用探测（一次，极小；施工用，交付前删）。

用途：确认真实服务端接受 thinking+reasoning_effort=max，并量出 reasoning_tokens
的量级——它决定 `DECK_CHAT_MAX_TOKENS` 够不够（思考与正文共用这个配额）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(r"G:\study_agent")
sys.path.insert(0, str(ROOT))
from core import chat as chat_mod  # noqa: E402
from core import db               # noqa: E402

doc_id = int(sys.argv[1]) if len(sys.argv) > 1 else 67
query = sys.argv[2] if len(sys.argv) > 2 else "这份课件讲了什么？用三句话概括。"

conn = db.connect()
db.init_db(conn)
cfg = chat_mod.deck_cfg()
ctx = chat_mod.build_deck_context(conn, doc_id, cfg=cfg)
print("doc#%d《%s》正文 %d/%d 块 · 约 %d token · 预算 %d · truncated=%s"
      % (doc_id, ctx.doc_title, ctx.chunks_used, ctx.chunks_total,
         ctx.tokens_est, ctx.tokens_budget, ctx.truncated))
print("模型 %s · 思考 %s · max_tokens %d"
      % (cfg["model"], cfg["reasoning_effort"], cfg["max_tokens"]))
print("提问：%s" % query)
print("-" * 70)

answer, usage, t0 = [], {}, None
import time
t0 = time.perf_counter()
for kind, data in chat_mod.stream_deck_chat(ctx, query, None, cfg=cfg):
    if kind == "delta":
        answer.append(data)
    elif kind == "usage":
        usage = data
elapsed = time.perf_counter() - t0

text = "".join(answer)
print(text)
print("-" * 70)
print("usage =", json.dumps(usage, ensure_ascii=False))
print("elapsed = %.1fs" % elapsed)
print("正文长度 = %d 字符" % len(text))
print("金额 = ¥%.4f（口径同 core/kp_build.py：$0.15/1M 入 + $0.60/1M 出，汇率 7.1）"
      % chat_mod.deck_cost_cny(usage))
conn.close()
