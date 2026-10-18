"""MCP 握手验证：真的起一个 stdio 服务进程，走完整 JSON-RPC 往返。

为什么需要它：
`serve_mcp.py --selftest` 只测 handle() 函数（进程内），测不到
「启动器 → stdio → stdout 是否被污染 → 响应是否逐行可解析」这条真实链路。
本脚本补上这一段：subprocess 起服务、逐行写请求、逐行断言 JSON。

用法::

    venv\\Scripts\\python.exe -X utf8 tools\\verify_mcp_handshake.py
    venv\\Scripts\\python.exe -X utf8 tools\\verify_mcp_handshake.py --via-cmd

成功输出 `RESULT: PASS`，失败输出 `RESULT: FAIL` 并以非 0 退出。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = ROOT / "venv" / "Scripts" / "python.exe"
MCP_PY = ROOT / "tools" / "serve_mcp.py"
MCP_CMD = ROOT / "tools" / "serve_mcp.cmd"


class Checks:
    def __init__(self) -> None:
        self.passed = 0
        self.failed: list[str] = []

    def check(self, name: str, cond: bool, detail: str = "") -> None:
        if cond:
            self.passed += 1
            print(f"  [OK  ] {name}")
        else:
            self.failed.append(name)
            print(f"  [FAIL] {name}" + (f" -> {detail}" if detail else ""))


def build_cmd(via_cmd: bool) -> list[str]:
    if via_cmd and MCP_CMD.exists():
        # 用 cmd /c 走和 DSH 配置完全一致的启动路径
        return ["cmd", "/c", str(MCP_CMD)]
    if not PY.exists():
        # 回退：用当前解释器（本脚本自身就是 venv 里的 python 在跑时）
        return [sys.executable, "-X", "utf8", str(MCP_PY)]
    return [str(PY), "-X", "utf8", str(MCP_PY)]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="MCP stdio 握手验证")
    ap.add_argument("--via-cmd", action="store_true",
                    help="通过 tools/serve_mcp.cmd 启动（模拟 DSH 配置）")
    ap.add_argument("--timeout", type=float, default=60.0)
    args = ap.parse_args(argv)

    cmd = build_cmd(args.via_cmd)
    print(f"=== MCP 握手验证 ===")
    print(f"启动: {' '.join(cmd)}")

    proc = subprocess.Popen(
        cmd, cwd=str(ROOT), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
        bufsize=1,
    )
    c = Checks()
    try:
        def send(obj: dict) -> None:
            assert proc.stdin is not None
            proc.stdin.write(json.dumps(obj, ensure_ascii=False) + "\n")
            proc.stdin.flush()

        def recv() -> dict:
            assert proc.stdout is not None
            line = proc.stdout.readline()
            if not line:
                raise RuntimeError("服务提前退出/无响应")
            return json.loads(line)

        # 1) initialize
        send({"jsonrpc": "2.0", "id": 1, "method": "initialize",
              "params": {"protocolVersion": "2024-11-05",
                         "capabilities": {},
                         "clientInfo": {"name": "handshake", "version": "0"}}})
        r = recv()
        c.check("initialize 返回 serverInfo",
                isinstance(r.get("result"), dict)
                and "serverInfo" in r["result"],
                detail=str(r)[:200])
        c.check("initialize 返回 protocolVersion",
                r.get("result", {}).get("protocolVersion") == "2024-11-05",
                detail=str(r)[:200])

        # 2) initialized 通知（不应有响应；用 ping 验证通道仍通畅）
        send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        send({"jsonrpc": "2.0", "id": 2, "method": "ping"})
        r = recv()
        c.check("ping 正常响应", r.get("id") == 2 and "result" in r, detail=str(r)[:200])

        # 3) tools/list
        send({"jsonrpc": "2.0", "id": 3, "method": "tools/list"})
        r = recv()
        tools = r.get("result", {}).get("tools", [])
        names = [t.get("name") for t in tools]
        c.check("tools/list 返回 14 个工具", len(tools) == 14, detail=str(names))
        expect = {"kb_search", "kb_docs", "kb_outline", "kb_chunk",
                  "kb_note_save", "kb_note_list", "kb_note_read",
                  "kb_review_queue", "kb_review_grade", "kb_kp_add",
                  "kb_kp_list", "kb_mistake_add", "kb_stats", "kb_ingest"}
        c.check("工具名齐全", expect == set(names), detail=str(expect ^ set(names)))

        # 4) tools/call kb_stats
        send({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
              "params": {"name": "kb_stats", "arguments": {}}})
        r = recv()
        text = ""
        try:
            text = r["result"]["content"][0]["text"]
        except Exception:
            pass
        c.check("kb_stats 调用成功", bool(text) and not r["result"].get("isError"),
                detail=str(r)[:300])
        c.check("kb_stats 返回合法 JSON", _is_json(text))

        # 5) tools/call kb_note_list
        send({"jsonrpc": "2.0", "id": 5, "method": "tools/call",
              "params": {"name": "kb_note_list", "arguments": {}}})
        r = recv()
        text = ""
        try:
            text = r["result"]["content"][0]["text"]
        except Exception:
            pass
        c.check("kb_note_list 调用成功", bool(text) and not r["result"].get("isError"),
                detail=str(r)[:300])

        # 6) 未知工具 → JSON-RPC error
        send({"jsonrpc": "2.0", "id": 6, "method": "tools/call",
              "params": {"name": "no_such_tool", "arguments": {}}})
        r = recv()
        c.check("未知工具返回 JSON-RPC error",
                "error" in r and r.get("id") == 6, detail=str(r)[:300])

        # 7) 未知方法 → error
        send({"jsonrpc": "2.0", "id": 7, "method": "no/such/method"})
        r = recv()
        c.check("未知方法返回 error", "error" in r and r.get("id") == 7,
                detail=str(r)[:300])
    except Exception as e:  # noqa: BLE001
        c.check("握手流程未抛异常", False, detail=f"{type(e).__name__}: {e}")
    finally:
        try:
            proc.stdin.close()
        except Exception:
            pass
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        err = ""
        if proc.stderr is not None:
            err = proc.stderr.read()
        if err.strip():
            print("\n--- 服务 stderr（应只有日志） ---")
            print(err.strip()[:2000])

    total = c.passed + len(c.failed)
    print(f"\n通过 {c.passed}/{total}")
    if c.failed:
        print("失败项: " + ", ".join(c.failed))
        print("RESULT: FAIL")
        return 1
    print("RESULT: PASS")
    return 0


def _is_json(text: str) -> bool:
    try:
        json.loads(text)
        return True
    except Exception:
        return False


if __name__ == "__main__":
    raise SystemExit(main())
