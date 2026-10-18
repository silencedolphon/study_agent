# Copyright (c) 2026 潜声海豚 <2227663973@qq.com>. All rights reserved.
# 本源码为著作权人私有作品，适用项目根目录 LICENSE（专有许可）。
# 允许个人安装与使用；禁止反向工程、修改、再分发、商业使用及声称原创。
# 本软件不含源码，按「现状」提供；详见 NOTICE.txt。
r"""卡 C-0081 验收宿主：在**两个互相隔离的"整机根"**上各起一个看板实例。

为什么要另写一个宿主，而不是复用 `tools/verify_c0080_share_ui.py`：
  · C-0080 的宿主把甲乙合成一台机器（同一个临时库、同一个根），
    而本卡要验的恰恰是"甲导出的包，换一台机器（另一个数据根）能不能用"；
  · 本卡还要给出**零模型调用 / 零费用**的运行时证据，所以在这里装了三个哨兵：
      ① socket 哨兵：任何 `socket.connect` 都记一笔（host/port/via），
         `--offline` 时**对外网目标直接抛异常**——乙端在"断网 + 没配密钥"
         的条件下还能导入成功，那才叫真没花钱；
      ② 模块调用哨兵：用 `threading.setprofile` 数 `core.embed / core.kp_build /
         core.figures / core.outline / core.chat / onnxruntime` 等模块的函数调用次数
         （由 `<root>/prof_on` 文件开关，避免拖慢不想统计的那一次导入）；
      ③ 环境哨兵：乙端进程内把密钥清空（**不动 .env 文件**），并追加
         `tools/dashboard.py` 模块的 `__file__` 记录，证明乙端进程的环境变量里
         没有甲端数据根的任何路径。

隔离手法（与 C-0080 同源）：`KB_RAW/KB_NOTES/KB_INDEX/KB_DB/KB_LOG/KB_SETTINGS_FILE`
全部指到 `--root`，并把 `config.ROOT` 改成 `--root`——于是 `subjects.root()`、
注册表、`share\` 落点、`documents.path` 的相对基准**全都跟着走**，
用户的真实库与真实注册表一个字节都不碰。

跑法（临时端口，绝不碰 8756）：
    venv\\Scripts\\python.exe tools\\verify_c0081_host.py --role A --port 8811 ^
        --root "%TEMP%\\sa_share_test\\A" --json "%TEMP%\\sa_share_test\\A\\ready.json"
它打印一行 `C0081_READY {...}`（纯 ASCII JSON）后常驻；写出 `<root>\\STOP` 即退出。
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import socket
import sys
import threading
import time
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent

# 这些模块的任何一次函数调用都说明"模型/网络那条线"被碰了。
WATCH_MODULES = (
    "core.embed", "core.kp_build", "core.figures", "core.outline",
    "core.chat", "core.ingest", "core.terms_build", "core.multippt",
    "onnxruntime", "tokenizers", "transformers", "requests", "httpx",
    "urllib3", "http.client",
)


def _force_utf8() -> None:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:                      # noqa: BLE001
            pass


_force_utf8()


# --------------------------------------------------------------------- 哨兵
def install_sentinels(sentinel_path: Path, *, offline: bool) -> dict:
    """装三个哨兵，并把快照每 0.25 s 写进 `sentinel_path`（驱动脚本随时可读）。"""
    state: dict = {
        "offline": bool(offline), "started": time.time(),
        "net": {"connects": [], "blocked": 0, "blocked_targets": []},
        "prof_on": False, "stop": False,
    }
    lock = threading.Lock()
    counts: collections.Counter = collections.Counter()
    events: list = []                          # [[模块名, 相对秒], …]（按窗口切分用）
    prof_flag = sentinel_path.parent / "prof_on"

    def _is_loopback(host: str) -> bool:
        h = str(host)
        return (h in ("", "localhost", "::1", "0.0.0.0")
                or h.startswith("127.") or h.startswith("::ffff:127."))

    orig_connect = socket.socket.connect
    orig_connect_ex = socket.socket.connect_ex
    orig_create = socket.create_connection

    def _record(addr, via: str) -> dict:
        try:
            if isinstance(addr, (tuple, list)) and len(addr) >= 2:
                host, port = str(addr[0]), int(addr[1])
            else:
                host, port = str(addr), 0
        except Exception:                      # noqa: BLE001
            host, port = "?", 0
        rec = {"host": host, "port": port, "via": via, "loopback": _is_loopback(host),
               "t": round(time.time() - state["started"], 3)}
        with lock:
            state["net"]["connects"].append(rec)
        return rec

    def _maybe_block(rec: dict) -> None:
        if offline and not rec["loopback"]:
            with lock:
                state["net"]["blocked"] += 1
                state["net"]["blocked_targets"].append(rec)
            raise OSError(101, "C-0081 断网哨兵：外网连接被阻断"
                               "（模拟乙端无网络 / 没配密钥）")

    def connect(self, addr, *a, **kw):         # noqa: ANN001
        _maybe_block(_record(addr, "connect"))
        return orig_connect(self, addr, *a, **kw)

    def connect_ex(self, addr, *a, **kw):      # noqa: ANN001
        _maybe_block(_record(addr, "connect_ex"))
        return orig_connect_ex(self, addr, *a, **kw)

    def create_connection(addr, *a, **kw):     # noqa: ANN001
        _maybe_block(_record(addr, "create_connection"))
        return orig_create(addr, *a, **kw)

    socket.socket.connect = connect            # type: ignore[method-assign]
    socket.socket.connect_ex = connect_ex      # type: ignore[method-assign]
    socket.create_connection = create_connection  # type: ignore[assignment]

    def prof(frame, event, arg):               # noqa: ANN001
        if event == "call" and state["prof_on"]:
            try:
                n = frame.f_globals.get("__name__")
            except Exception:                  # noqa: BLE001
                return None
            if n in WATCH_MODULES:
                counts[n] += 1
                if len(events) < 50000:
                    events.append([n, round(time.time() - state["started"], 3)])
        return None

    threading.setprofile(prof)

    def _dump() -> None:
        tick = 0
        while True:
            try:
                state["prof_on"] = prof_flag.exists()
            except OSError:
                state["prof_on"] = False
            tick += 1
            if tick % 5 == 0 or state["stop"]:      # 每 5 圈（≈0.25 s）落一次盘
                with lock:
                    net = state["net"]
                    snap = {
                        "offline": state["offline"], "prof_on": state["prof_on"],
                        "started": state["started"],
                        "n_connects": len(net["connects"]),
                        "connects": net["connects"][-400:],
                        "blocked": net["blocked"],
                        "blocked_targets": net["blocked_targets"][-50:],
                        "mod_calls": dict(counts),
                        "mod_events": list(events[-20000:]),
                        "updated": time.time(),
                    }
                try:
                    tmp = sentinel_path.with_name(sentinel_path.name + ".tmp")
                    tmp.write_text(json.dumps(snap, ensure_ascii=False, indent=2),
                                   encoding="utf-8")
                    os.replace(tmp, sentinel_path)
                except OSError:
                    pass
            if state["stop"]:
                break
            time.sleep(0.05)

    threading.Thread(target=_dump, daemon=True).start()
    return state


# --------------------------------------------------------------------- 主流程
def main() -> int:
    ap = argparse.ArgumentParser(description="C-0081 隔离看板实例（甲/乙）")
    ap.add_argument("--role", choices=["A", "B"], required=True)
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--root", required=True, help="本实例的整机根（数据根）")
    ap.add_argument("--sentinel", default="", help="哨兵快照 JSON 落点")
    ap.add_argument("--offline", action="store_true",
                    help="阻断一切非回环外网连接（模拟乙端没网）")
    ap.add_argument("--json", default="", help="就绪信息也写一份到这里")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    for sub in ("raw", "notes", "index", "logs", "share"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    (root / "STOP").unlink(missing_ok=True)

    # ★ 必须在 `import core.config` **之前**：config 的路径在导入时定值。
    os.environ.update({
        "KB_RAW": str(root / "raw"), "KB_NOTES": str(root / "notes"),
        "KB_INDEX": str(root / "index"), "KB_DB": str(root / "index" / "study.db"),
        "KB_LOG": str(root / "logs"),
        "KB_SETTINGS_FILE": str(root / "config.local.json"),
        "PYTHONIOENCODING": "utf-8",
    })
    if args.role == "B":
        # 乙端"刚装好、还没配密钥"：只清**进程内**环境变量，不动 .env 文件。
        for k in ("DEEPSEEK_API_KEY", "DECK_CHAT_API_KEY", "ZHIPU_API_KEY",
                  "OPENAI_API_KEY", "CHAT_API_KEY"):
            os.environ.pop(k, None)
    sys.path.insert(0, str(PROJ))

    sentinel_path = Path(args.sentinel) if args.sentinel else (root / "sentinel.json")
    sentinel_path.parent.mkdir(parents=True, exist_ok=True)
    state = install_sentinels(sentinel_path, offline=args.offline)

    from core import config                       # noqa: E402
    config.ROOT = root                            # 数据根跟着走（注册表/学科/share 全在内）
    from core import subjects as subjects_mod     # noqa: E402

    if args.role == "A":
        # 甲端：数据根里只有一个学科，整份是从真实库**只读复制**来的。
        names = [d.name for d in (root / "subjects").iterdir() if d.is_dir()] \
            if (root / "subjects").is_dir() else []
        if not names:
            print("甲端数据根里没有学科目录：%s" % (root / "subjects"), file=sys.stderr)
            return 3
        entry = subjects_mod.switch(names[0])
    else:
        # 乙端：全新库。两个空学科 = 乙自己建的两个库（接收精简包 / 接收全量包）。
        if not subjects_mod.list_subjects() or all(
                s["slug"] == subjects_mod.DEFAULT_SLUG
                for s in subjects_mod.list_subjects()):
            subjects_mod.create("接收精简库", note="C-0081 乙端（全新）", use=True)
            subjects_mod.create("接收全量库", note="C-0081 乙端（全新）")
        entry = subjects_mod.switch("接收精简库")

    config.ensure_dirs()

    # 乙端追加一道"没有密钥"的保险：核心凭据链也返回空。
    if args.role == "B":
        try:
            from core import chat as chat_mod
            chat_mod.load_api_key = lambda *a, **kw: ""      # type: ignore[assignment]
        except Exception as e:                                # noqa: BLE001
            print("[警告] 清空凭据链失败：%s" % e, flush=True)

    import importlib.util                          # noqa: E402
    spec = importlib.util.spec_from_file_location(
        "dash_c0081", PROJ / "tools" / "dashboard.py")
    dash = importlib.util.module_from_spec(spec)
    sys.modules["dash_c0081"] = dash
    spec.loader.exec_module(dash)

    conn = dash._conn()
    try:
        dbinfo = dash.db.init_db(conn)
        n_docs = conn.execute("SELECT COUNT(*) AS n FROM documents").fetchone()["n"]
    finally:
        conn.close()

    online = int("onnxruntime" in sys.modules)
    srv = dash.ThreadingHTTPServer(("127.0.0.1", args.port), dash.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    dirty = [k for k, v in os.environ.items()
             if k.startswith("KB_") and str(PROJ) in str(v) and "sa_share_test" not in str(v)]
    info = {
        "ok": True, "role": args.role, "port": args.port, "pid": os.getpid(),
        "root": str(root), "db": str(config.DB_PATH), "log_dir": str(config.LOG_DIR),
        "raw_dir": str(config.RAW_DIR), "share_dir": str(Path(config.ROOT) / "share"),
        "subject": entry["slug"], "subjects": [s["slug"] for s in subjects_mod.list_subjects()],
        "n_docs": int(n_docs), "fts": bool(dbinfo.get("fts5")),
        "offline": bool(args.offline), "sentinel": str(sentinel_path),
        "onnx_loaded_at_start": bool(online),
        "env_leak_check": dirty,
        "settings_file": str(config.SETTINGS_FILE),
    }
    print("C0081_READY " + json.dumps(info, ensure_ascii=True), flush=True)
    if args.json:
        Path(args.json).write_text(json.dumps(info, ensure_ascii=False, indent=2),
                                   encoding="utf-8")

    stop = root / "STOP"
    try:
        while not stop.exists():
            time.sleep(0.3)
    except KeyboardInterrupt:
        pass
    state["stop"] = True
    time.sleep(0.4)
    srv.shutdown()
    print("[宿主退出] role=%s root=%s" % (args.role, root), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
