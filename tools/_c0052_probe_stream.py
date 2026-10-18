# -*- coding: utf-8 -*-
"""C-0052 临时探针：跑一次 /api/deck-chat/stream，记录 SSE 事件与花费。"""
from __future__ import annotations

import json
import sys
import urllib.request

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8891
DOC = int(sys.argv[2]) if len(sys.argv) > 2 else 7
Q = sys.argv[3] if len(sys.argv) > 3 else "请总结这份课件的整体内容与重点。"

body = json.dumps({"doc_id": DOC, "query": Q}, ensure_ascii=False).encode("utf-8")
req = urllib.request.Request(
    f"http://127.0.0.1:{PORT}/api/deck-chat/stream", data=body,
    headers={"Content-Type": "application/json"}, method="POST")
scope = None
ev = []
answer = ""
usage = {}
elapsed = 0.0
with urllib.request.urlopen(req, timeout=300) as r:
    for raw in r:
        line = raw.decode("utf-8", "replace").strip()
        if not line.startswith("data: "):
            continue
        obj = json.loads(line[6:])
        if obj["type"] == "scope":
            scope = obj
        elif obj["type"] == "sources":
            ev = obj["evidence"]
        elif obj["type"] == "delta":
            answer += obj["text"]
        elif obj["type"] == "done":
            usage = obj.get("usage") or {}
            elapsed = obj.get("elapsed") or 0
        elif obj["type"] == "error":
            print("ERROR:", obj["message"])

print("scope:", json.dumps(scope, ensure_ascii=False))
print("证据条数:", len(ev), "doc_id 集合:", sorted({e["doc_id"] for e in ev}))
for e in ev:
    print("   ", e["idx"], "doc#%s" % e["doc_id"], e["doc_title"], e["location"],
          "fig" if e["is_figure"] else "", e.get("is_extra") and "邻页" or "")
print("usage:", json.dumps(usage, ensure_ascii=False), "elapsed %.1fs" % elapsed)
print("回答长度:", len(answer))
print("---- 回答前 600 字 ----")
print(answer[:600])
