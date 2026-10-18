                "before": {"type": "integer", "description": "向前取几块，默认 1"},
                "after": {"type": "integer", "description": "向后取几块，默认 1"},
            },
            "required": ["chunk_id"],
        },
    },
    {
        "name": "kb_note_save",
        "description": (
            "把一段结论落成笔记文件保存到 notes/。"
            "这是本系统对抗上下文刷新的核心机制：对话上下文会被压缩清空，"
            "写下来的笔记是无损的、下次可直接读回。"
            "每当讨论得出有长期价值的结论，就调用它。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "笔记标题，也是文件名"},
                "body": {"type": "string", "description": "Markdown 正文"},
                "refs": {
                    "type": "array", "items": {"type": "string"},
                    "description": "出处列表，如 ['教材.pdf › 3.1 › p.12']",
                },
                "tags": {"type": "array", "items": {"type": "string"}},
                "kind": {
                    "type": "string",
                    "enum": ["note", "summary", "mistake", "cheatsheet"],
                },
            },
            "required": ["title", "body"],
        },
    },
    {
        "name": "kb_note_list",
        "description": "列出已保存的笔记。新会话开始时先调用它，把既往结论捡回上下文。",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "kb_note_read",
        "description": "读取某篇笔记的原文。支持标题或文件名的模糊匹配。",
        "inputSchema": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
    },
    {
        "name": "kb_review_queue",
        "description": "取今日到期的复习队列（间隔重复调度）。status=due 是到期项，new 是尚未学过的新知识点。",
        "inputSchema": {
            "type": "object",
            "properties": {"limit": {"type": "integer", "description": "默认 20"}},
        },
    },
    {
                "right_answer": {"type": "string"},
                "reason": {"type": "string"},
                "kp_id": {"type": "integer"},
                "chunk_id": {"type": "integer"},
            },
            "required": ["question"],
        },
    },
        },
    },
    {
        "name": "kb_stats",
        "description": "学习看板数据：文档数、知识点数、今日到期与已复习、错题分布、最薄弱知识点。",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "kb_ingest",
        "description": (
            "摄入文件或目录进知识库（默认 raw/）。幂等：以内容哈希判重，"
            "未改动的文件会跳过，因此可安全重复调用。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "paths": {
                    "type": "array", "items": {"type": "string"},
                    "description": "文件或目录路径，留空则摄入 raw/",
                },
                "force": {"type": "boolean", "description": "忽略哈希强制重建"},
            },
        },
    },
]


# ---------------- 工具实现 ----------------
def call_tool(name: str, args: dict) -> str:
    conn = db.connect()
    db.init_db(conn)
    try:
        if name == "kb_search":
            # corpus 默认 "main"，这是需求红线：不显式指定时看不到 aux，
            # 助手不可能把试题/资料笔记当教材依据回答。
            hits = search_mod.search(
                conn, args.get("query", ""), top_k=int(args.get("k") or 8),
                mode=args.get("mode") or "hybrid",
                corpus=args.get("corpus") or "main")
            if not hits:
                return json.dumps({
                    "n": 0,
                    "note": "知识库里没有找到相关内容。不要凭记忆编造，"
                            "如实告知用户资料中没有，或用 kb_docs / kb_outline 确认资料范围。",
                }, ensure_ascii=False, indent=2)
            return json.dumps({"n": len(hits), "hits": [_hit_brief(h) for h in hits]},
                              ensure_ascii=False, indent=2)

        if name == "kb_docs":
            # 带 corpus 字段：助手要能分辨"哪些资料可用于回答"（main）
            # 与"哪些只能用于构建知识点"（aux）。
            rows = conn.execute(
                """SELECT id, path, kind, title, corpus, n_chunks, n_chars,
                          status, warnings
                   FROM documents ORDER BY indexed_at DESC"""
            ).fetchall()
            return json.dumps({"n": len(rows), "documents": [dict(r) for r in rows]},
                              ensure_ascii=False, indent=2)

        if name == "kb_outline":
                               (did,)).fetchone()
            if not row:
                return json.dumps({"error": f"文档 {did} 不存在"},
                                  ensure_ascii=False)
            return json.dumps({"doc_id": did, "title": row["title"],
                               "path": row["path"],
                               "outline": search_mod.doc_outline(conn, did)},
                              ensure_ascii=False, indent=2)

        if name == "kb_chunk":
            cid = int(args["chunk_id"])
            h = search_mod.get_chunk(conn, cid)
            if not h:
                return json.dumps({"error": f"切块 {cid} 不存在"}, ensure_ascii=False)
            ctx = search_mod.neighbors(conn, h, before=int(args.get("before") or 1),
                                      after=int(args.get("after") or 1))
            return json.dumps({
                "target": _hit_brief(h),
                "context": [{"chunk_id": c.chunk_id, "cite": c.cite(),
                             "text": c.text} for c in ctx],
            }, ensure_ascii=False, indent=2)

        if name == "kb_note_save":
            p = study.save_note(
                conn, args["title"], args.get("body", ""),
                tags=args.get("tags") or [], source_refs=args.get("refs") or [],
                kind=args.get("kind") or "note")
            return json.dumps({"saved": p}, ensure_ascii=False)

        if name == "kb_note_list":
            return json.dumps({"notes": study.list_notes()},
                              ensure_ascii=False, indent=2)

        if name == "kb_note_read":
            want = (args.get("name") or "").strip().lower()
            notes = study.list_notes()
            target = None
            for n in notes:
                if want in (n["file"].lower(), n["title"].lower(),
                            n["file"].removesuffix(".md").lower()):
                    target = n
                    break
            if not target:
                for n in notes:
                    if want and (want in n["title"].lower()
                                 or want in n["file"].lower()):
                        target = n
                        break
            if not target:
                return json.dumps({"error": "找不到笔记",
                                   "available": [n["file"] for n in notes]},
                                  ensure_ascii=False)
            return json.dumps({
                "file": target["file"], "title": target["title"],
                "content": Path(target["path"]).read_text(encoding="utf-8",
                                                          errors="replace"),
            }, ensure_ascii=False, indent=2)

        if name == "kb_review_queue":
            q = study.due_queue(conn, limit=int(args.get("limit") or 20))
                # front-matter、自动生成的一级标题、「## 出处」段）。
                # `content` 仍是**原文**——按它算的老用法一个字都不变。
                "body": study.note_body(text),
            }, ensure_ascii=False, indent=2)

        if name == "kb_review_queue":
            q = study.due_queue(conn, limit=int(args.get("limit") or 20))
            return json.dumps({"n": len(q), "queue": q},
                              ensure_ascii=False, indent=2)

        if name == "kb_review_grade":
            r = study.schedule(conn, int(args["kp_id"]), int(args["grade"]),
                               question=args.get("question", ""),
                               answer=args.get("answer", ""))
            return json.dumps(r, ensure_ascii=False, indent=2)

        if name == "kb_kp_add":
            kp = study.upsert_kp(
                conn, args["name"], summary=args.get("summary", ""),
                subject=args.get("subject", ""),
                chunk_ids=[int(x) for x in (args.get("chunk_ids") or [])])
            return json.dumps({"kp_id": kp, "name": args["name"]},
                              ensure_ascii=False)

        if name == "kb_kp_list":
            rows = conn.execute(
                """SELECT id, name, subject, mastery, interval_days, reps, lapses,
                          due_at FROM knowledge_points ORDER BY mastery ASC"""
            ).fetchall()
            return json.dumps({"n": len(rows),
                               "knowledge_points": [dict(r) for r in rows]},
                              ensure_ascii=False, indent=2)

        if name == "kb_mistake_add":
            mid = study.add_mistake(
                conn, question=args.get("question", ""),
                my_answer=args.get("my_answer", ""),
                right_answer=args.get("right_answer", ""),
                reason=args.get("reason", ""),
                kp_id=args.get("kp_id"), chunk_id=args.get("chunk_id"))
            return json.dumps({"mistake_id": mid,
                               "note": "若关联了知识点，其复习间隔已减半并提前到期"},
                              ensure_ascii=False)

        if name == "kb_stats":
            return json.dumps(study.dashboard(conn), ensure_ascii=False,
                              indent=2, default=str)

        if name == "kb_ingest":
            from core import ingest as ingest_mod
            paths = args.get("paths") or [str(config.RAW_DIR)]
            rep = ingest_mod.ingest_paths(
                conn, [Path(p) for p in paths], force=bool(args.get("force")))
            return json.dumps({
                "summary": rep.summary(), "added": rep.added,
                "updated": rep.updated, "skipped": rep.skipped,
                "failed": rep.failed, "needs_ocr": rep.needs_ocr,
                "chunks": rep.chunks,
                "details": [d for d in rep.details if d["status"] != "skipped"],
            }, ensure_ascii=False, indent=2)

        return json.dumps({"error": f"未知工具: {name}"}, ensure_ascii=False)
    finally:
        conn.close()


# ---------------- JSON-RPC ----------------
def handle(msg: dict) -> dict | None:
    mid = msg.get("id")
    method = msg.get("method")
    params = msg.get("params") or {}

    if method == "initialize":
        return {"jsonrpc": "2.0", "id": mid, "result": {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": SERVER_INFO,
        }}
    if method in ("notifications/initialized", "initialized"):
        return None  # 通知无需回复
            return {"jsonrpc": "2.0", "id": mid, "result": {
                "content": [{"type": "text", "text": text}],
                "isError": False,
            }}
        except Exception as e:
            # 工具错误按 MCP 约定回在 result 里，而不是 JSON-RPC error，
            # 这样模型能看到错误内容并自行纠正
            log(f"tools/call {name} 失败: {type(e).__name__}: {e}")
            return {"jsonrpc": "2.0", "id": mid, "result": {
                "content": [{"type": "text",
                             "text": f"错误: {type(e).__name__}: {e}"}],
                "isError": True,
            }}
    if method == "ping":
        return {"jsonrpc": "2.0", "id": mid, "result": {}}
    if mid is None:
        return None
    return {"jsonrpc": "2.0", "id": mid,
            "error": {"code": -32601, "message": f"未实现的方法: {method}"}}


def serve() -> int:
def selftest() -> int:
    """不放 DSH 也能验证协议与工具：手工喂几轮 JSON-RPC。"""
    print("=== MCP 协议自测 ===")
    steps = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": PROTOCOL_VERSION,
                    "clientInfo": {"name": "selftest", "version": "0"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
         "params": {"name": "kb_stats", "arguments": {}}},
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
         "params": {"name": "kb_search",
                    "arguments": {"query": "三次握手", "k": 2}}},
        {"jsonrpc": "2.0", "id": 5, "method": "tools/call",
         "params": {"name": "kb_note_list", "arguments": {}}},
        {"jsonrpc": "2.0", "id": 6, "method": "tools/call",
         "params": {"name": "不存在的工具", "arguments": {}}},
    ]
    ok = 0
    for s in steps:
        r = handle(s)
        label = s.get("method")
        if r is None:
            print(f"  [OK  ] {label} -> (通知，无回复)")
            ok += 1
            continue
        if "error" in r and s.get("id") == 6:
            print(f"  [OK  ] {label} -> 正确返回错误")
            ok += 1
            continue
        if "result" in r:
            res = r["result"]
            if "tools" in res:
                print(f"  [OK  ] {label} -> {len(res['tools'])} 个工具: "
                      + ", ".join(t["name"] for t in res["tools"]))
            elif "content" in res:
                txt = res["content"][0]["text"]
                print(f"  [OK  ] {label} -> {len(txt)} 字节"
                      + ("（isError）" if res.get("isError") else "")
                      + f"，首行: {txt.splitlines()[0][:70] if txt else ''}")
            else:
                print(f"  [OK  ] {label} -> {list(res.keys())}")
            ok += 1
        else: