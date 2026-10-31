"""隔离验收环境管理（P2 验收套件的基础设施）。

设计目标
--------
1. **绝不碰真实数据**：`index/`、`subjects/`、`raw/`、`notes/`、`logs/`、`models/` 只读；
   被测实例的数据全部来自用 sqlite3 backup API 复制到 `_work/verify/data/` 的副本。
2. **代码根也要隔离**：`core/config.py` 用 `Path(__file__).parent.parent` 算 `ROOT`，
   而 `core/subjects.py` 的注册表固定落在 `ROOT/logs/subjects.json`。所以把
   `core/*.py` + `core/frontend/*` + `tools/dashboard.py` + `tools/study.py` 复制到
   `_work/verify/sandbox/`，让被测进程的 ROOT 指向沙箱——这样连
   `config.local.json`、学科注册表、`share/` 也都落在沙箱里。
3. 数据副本另行放在 `_work/verify/data/`，通过 `KB_DB/KB_RAW/KB_NOTES/KB_INDEX/KB_LOG`
   环境变量指过去（`core/config.py::_load_env` 规定进程环境变量优先于 `.env`）。
4. 每个测试会话用一个临时端口（8897/8898/8899 优先），结束时停进程、释放端口。

用法（测试代码里）
------------------
    import env
    env.prepare()                      # 同步代码副本 + 重建数据副本
    srv = env.Server()                 # 上下文管理器：起服务 / 停服务
    srv.get("/api/stats")              # -> HttpResult(status, body, json)
    cli = env.run_study(["stats", "--json"])
    dom = env.edge_dump_dom(srv.url)   # Edge headless 渲染后的 DOM
"""

from __future__ import annotations

import atexit
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
VERIFY_DIR = PROJECT / "_work" / "verify"
SANDBOX_DIR = VERIFY_DIR / "sandbox"
DATA_DIR = VERIFY_DIR / "data"
ARTIFACT_DIR = VERIFY_DIR / "artifacts"
LOG_DIR = VERIFY_DIR / "logs"
REPORT_DIR = VERIFY_DIR

PYTHON = PROJECT / "venv" / "Scripts" / "python.exe"
EDGE = Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")
MODEL_DIR = PROJECT / "models" / "bge-small-zh-v1.5"

CANDIDATE_PORTS = (8897, 8898, 8899)

# 真实库 → 测试 fixture 的角色映射。
#   main   : 有文档/切块/向量/图注的库（用来测检索、文档、图注、思维导图）
#   deck   : 有 deck_plans / deck_reviews 的库（用来测复习月历与标记）
# fixture 只在第一次运行时从真实库用 backup API 快照一次并缓存在 _work/verify/fixtures/，
# 之后每次跑套件都从 fixture 复制——真实库在验收期间被别的进程改动也不会让基线漂移。
# 想按当前真实库重建 fixture：set VERIFY_REFRESH_FIXTURES=1
REAL_DB_ROLES = {
    "main": PROJECT / "subjects" / "生药药动" / "index" / "study.db",
    "deck": PROJECT / "subjects" / "药物化学" / "index" / "study.db",
}
FIXTURE_DIR = VERIFY_DIR / "fixtures"
# 沙箱里额外造的学科（用于 /api/subjects 切换测试）：直接复制真实学科库副本
SANDBOX_SUBJECTS = {
    "验收-药物化学": "deck",
}

CODE_FILES = [
    ("core", "*.py"),
    ("core/frontend", "*"),
    # P0-A：新包 core/pipeline_runtime（RunContext/telemetry）必须同步进代码沙箱，
    # 否则被测 dashboard 进程 import core.chat → pipeline_runtime 会 ModuleNotFoundError。
    ("core/pipeline_runtime", "*.py"),
    ("tools", "dashboard.py"),
    ("tools", "study.py"),
]


# --------------------------------------------------------------------------- 基础工具
def ensure_dirs() -> None:
    for d in (VERIFY_DIR, SANDBOX_DIR, DATA_DIR, ARTIFACT_DIR, LOG_DIR, FIXTURE_DIR):
        d.mkdir(parents=True, exist_ok=True)


def sha256(path: Path) -> str:
    import hashlib

    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def copy_sqlite(src: Path, dst: Path) -> None:
    """用 sqlite3 backup API 复制库（对 WAL 模式安全，不写源库）。"""
    if not src.exists():
        raise FileNotFoundError(f"缺少真实库（应为只读源）：{src}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    uri = "file:%s?mode=ro" % src.as_posix()
    s = sqlite3.connect(uri, uri=True, timeout=30)
    try:
        d = sqlite3.connect(str(dst))
        try:
            s.backup(d)
        finally:
            d.close()
    finally:
        s.close()


def _same_file(a: Path, b: Path) -> bool:
    try:
        return a.stat().st_size == b.stat().st_size and sha256(a) == sha256(b)
    except OSError:
        return False


# --------------------------------------------------------------------------- 代码沙箱
def sync_code(force: bool = False) -> list[str]:
    """把产品代码同步进沙箱。返回实际有变化的文件（相对沙箱的路径）。"""
    changed: list[str] = []
    plan: list[tuple[Path, Path]] = []
    for sub, pattern in CODE_FILES:
        src_dir = PROJECT / sub
        dst_dir = SANDBOX_DIR / sub
        dst_dir.mkdir(parents=True, exist_ok=True)
        for p in sorted(src_dir.glob(pattern)):
            if p.is_file() and ".bak" not in p.name:
                plan.append((p, dst_dir / p.name))
    for src, dst in plan:
        if not force and dst.exists() and _same_file(src, dst):
            continue
        shutil.copy2(src, dst)
        changed.append(str(dst.relative_to(SANDBOX_DIR)))
    # 沙箱顶层目录：logs（学科注册表固定落 ROOT/logs）、share（分享包输出）
    for d in (SANDBOX_DIR / "logs", SANDBOX_DIR / "share", SANDBOX_DIR / "subjects"):
        d.mkdir(parents=True, exist_ok=True)
    return changed


def verify_code_sync() -> list[str]:
    """返回与产品代码不一致的沙箱文件（空列表 = 一致）。"""
    bad: list[str] = []
    for sub, pattern in CODE_FILES:
        for p in sorted((PROJECT / sub).glob(pattern)):
            if not p.is_file() or ".bak" in p.name:
                continue
            dst = SANDBOX_DIR / sub / p.name
            if not dst.exists() or not _same_file(p, dst):
                bad.append(f"{sub}/{p.name}")
    return bad


# --------------------------------------------------------------------------- 数据副本
def ensure_fixtures(refresh: bool | None = None) -> dict:
    """把真实库快照成 fixture（默认只快照一次，之后复用），返回 manifest。

    真实库在验收期间可能被真实看板/其它进程改动；fixture 冻结后基线可复现。
    设环境变量 VERIFY_REFRESH_FIXTURES=1 强制重拍。
    """
    ensure_dirs()
    if refresh is None:
        refresh = os.environ.get("VERIFY_REFRESH_FIXTURES", "") not in ("", "0")
    manifest = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "roles": {}}
    for role, src in REAL_DB_ROLES.items():
        dst = FIXTURE_DIR / f"{role}.db"
        entry = {"source": str(src.relative_to(PROJECT))}
        try:
            entry["source_size"] = src.stat().st_size
            entry["source_sha256"] = sha256(src)
        except OSError as e:  # noqa: BLE001
            entry["source_error"] = str(e)
        if refresh or not dst.exists():
            copy_sqlite(src, dst)
            entry["created"] = True
        else:
            entry["created"] = False
        entry["fixture_size"] = dst.stat().st_size
        entry["fixture_sha256"] = sha256(dst)
        manifest["roles"][role] = entry
    (FIXTURE_DIR / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def prepare_data(clean: bool = True) -> dict:
    """重建 _work/verify/data/ 与沙箱学科 fixture。返回路径字典。"""
    ensure_dirs()
    ensure_fixtures()
    if clean:
        for d in (DATA_DIR,):
            if d.exists():
                shutil.rmtree(d)
        for d in (SANDBOX_DIR / "subjects", SANDBOX_DIR / "logs", SANDBOX_DIR / "share"):
            if d.exists():
                shutil.rmtree(d)
    data = {
        "main_db": DATA_DIR / "main.db",
        "empty_db": DATA_DIR / "empty.db",
        "deck_db": DATA_DIR / "deck.db",
        "raw": DATA_DIR / "raw",
        "notes": DATA_DIR / "notes",
        "index": DATA_DIR / "index",
        "logs": DATA_DIR / "logs",
        "settings": DATA_DIR / "config.local.json",
        "share": SANDBOX_DIR / "share",
    }
    for key in ("raw", "notes", "index", "logs", "share"):
        data[key].mkdir(parents=True, exist_ok=True)
    for role in REAL_DB_ROLES:
        copy_sqlite(FIXTURE_DIR / f"{role}.db", DATA_DIR / f"{role}.db")
    # 空库：0 字节文件，由被测进程的 db.init_db 建 schema（完全确定性，不依赖真实库现状）
    data["empty_db"].write_bytes(b"")
    # 沙箱学科 fixture（用于 /api/subjects、switch、deck 测试）
    for slug, role in SANDBOX_SUBJECTS.items():
        d = SANDBOX_DIR / "subjects" / slug
        for sub in ("raw", "notes", "index", "logs"):
            (d / sub).mkdir(parents=True, exist_ok=True)
        copy_sqlite(DATA_DIR / f"{role}.db", d / "index" / "study.db")
        meta = {
            "name": slug,
            "slug": slug,
            "created_at": "2026-10-18T00:00:00",
            "note": "自动化验收 fixture",
            "layout": {
                "RAW_DIR": str(d / "raw"),
                "NOTES_DIR": str(d / "notes"),
                "INDEX_DIR": str(d / "index"),
                "DB_PATH": str(d / "index" / "study.db"),
                "LOG_DIR": str(d / "logs"),
            },
        }
        (d / "subject.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    (SANDBOX_DIR / "logs").mkdir(parents=True, exist_ok=True)
    create_raw_fixtures(data)
    return data


def create_raw_fixtures(data: dict | None = None) -> dict:
    """在临时 raw / ingest_src 里放小 fixtures（导入预览、入库、重命名用例用）。"""
    data = data or {"raw": DATA_DIR / "raw"}
    raw = Path(data["raw"])
    raw.mkdir(parents=True, exist_ok=True)
    (raw / "子目录").mkdir(parents=True, exist_ok=True)
    (raw / "验收样例.md").write_text(
        "# 验收样例文档\n\n## 第一章 总论\n\n药物代谢是药物在体内经酶催化发生化学结构改变的过程。\n\n"
        "## 第二章 排泄\n\n药物排泄的主要途径是肾脏排泄和胆汁排泄。\n",
        encoding="utf-8",
    )
    (raw / "子目录" / "样例二.txt").write_text(
        "细胞色素 P450 酶系参与药物氧化代谢。\n", encoding="utf-8"
    )
    src = DATA_DIR / "ingest_src"
    src.mkdir(parents=True, exist_ok=True)
    (src / "小样.md").write_text(
        "# 小样测试\n\n## 一、药物转运\n\n药物跨膜转运分为被动转运和主动转运。\n\n"
        "## 二、药物代谢\n\n肝脏是药物代谢的主要器官。\n",
        encoding="utf-8",
    )
    return {"raw": raw, "ingest_src": src}


def prepare(clean_data: bool = True) -> dict:
    """完整准备：代码沙箱同步 + 数据副本重建。"""
    ensure_dirs()
    changed = sync_code()
    data = prepare_data(clean=clean_data)
    data["code_changed"] = changed
    return data


# --------------------------------------------------------------------------- 进程环境
def base_env(db_path: Path | None = None, extra: dict | None = None) -> dict:
    """构造被测进程环境：数据路径全指向副本，不读真实 .env（沙箱根下没有 .env）。"""
    env = os.environ.copy()
    db = Path(db_path) if db_path else DATA_DIR / "main.db"
    env.update(
        {
            "KB_DB": str(db),
            "KB_RAW": str(DATA_DIR / "raw"),
            "KB_NOTES": str(DATA_DIR / "notes"),
            "KB_INDEX": str(DATA_DIR / "index"),
            "KB_LOG": str(DATA_DIR / "logs"),
            "KB_SETTINGS_FILE": str(DATA_DIR / "config.local.json"),
            "EMBED_MODEL_DIR": str(MODEL_DIR),
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )
    # 去掉可能从父进程继承的全局覆盖开关与密钥，保证「无密钥/离线」基线可复现
    for k in (
        "KB_KNOWLEDGE_DIR",
        "KB_HOME",
        "DEEPSEEK_API_KEY",
        "CHAT_API_KEY",
        "KP_BUILD_API_KEY",
        "DECK_CHAT_API_KEY",
    ):
        env.pop(k, None)
    if extra:
        env.update({k: str(v) for k, v in extra.items()})
    return env


# --------------------------------------------------------------------------- 端口 / HTTP
def port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) != 0


def pick_port(exclude: tuple[int, ...] = ()) -> int:
    for p in CANDIDATE_PORTS:
        if p not in exclude and port_free(p):
            return p
    # 兜底：让系统分配一个临时端口
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def port_listening(port: int) -> bool:
    return not port_free(port)


class HttpResult:
    def __init__(self, status: int, body: bytes, headers: dict, url: str, elapsed: float):
        self.status = status
        self.body = body
        self.headers = headers
        self.url = url
        self.elapsed = elapsed

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", "replace")

    def json(self):
        try:
            return json.loads(self.text)
        except Exception:  # noqa: BLE001
            return None

    def __repr__(self) -> str:  # pragma: no cover
        return f"<HttpResult {self.status} {self.url} {len(self.body)}B>"


def url_with(base: str, path: str, **params) -> str:
    """拼 URL 并把参数值做百分号编码（中文参数必须编码，urllib 只发 ASCII）。"""
    url = base.rstrip("/") + path
    if params:
        url += "?" + urllib.parse.urlencode({k: str(v) for k, v in params.items()})
    return url


def http_request(
    url: str,
    method: str = "GET",
    data: bytes | None = None,
    headers: dict | None = None,
    timeout: float = 60.0,
) -> HttpResult:
    req = urllib.request.Request(url, data=data, method=method)
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)
    started = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
            return HttpResult(resp.status, body, dict(resp.headers), url, time.time() - started)
    except urllib.error.HTTPError as e:
        body = e.read() if hasattr(e, "read") else b""
        return HttpResult(e.code, body, dict(e.headers or {}), url, time.time() - started)


def http_json(
    url: str, method: str = "GET", payload=None, timeout: float = 60.0
) -> tuple[int, object]:
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"
    r = http_request(url, method=method, data=data, headers=headers, timeout=timeout)
    return r.status, r.json()


# --------------------------------------------------------------------------- 服务进程
_ACTIVE_SERVERS: list["Server"] = []


def _stop_all_servers() -> None:
    for srv in list(_ACTIVE_SERVERS):
        try:
            srv.stop()
        except Exception:  # noqa: BLE001
            pass


atexit.register(_stop_all_servers)


class Server:
    """隔离的 dashboard 实例。with 语句结束自动停止。"""

    USED_PORTS: list[int] = []

    def __init__(self, db_path: Path | None = None, port: int | None = None,
                 tag: str = "srv", extra_env: dict | None = None,
                 start_timeout: float = 60.0):
        self.port = port or pick_port()
        if not port_free(self.port):
            raise RuntimeError(f"端口 {self.port} 已被占用，拒绝启动测试实例")
        Server.USED_PORTS.append(self.port)
        self.db_path = Path(db_path) if db_path else DATA_DIR / "main.db"
        self.tag = tag
        self.extra_env = extra_env or {}
        self.start_timeout = start_timeout
        self.proc: subprocess.Popen | None = None
        self.log_path = LOG_DIR / f"{tag}-{self.port}.log"
        self.log_fh = None
        self.boot_stdout = ""
        self.url = f"http://127.0.0.1:{self.port}"

    # -- 启动 / 等待就绪
    def start(self) -> "Server":
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        self.log_fh = open(self.log_path, "wb")
        cmd = [
            str(PYTHON),
            "-X",
            "utf8",
            str(SANDBOX_DIR / "tools" / "dashboard.py"),
            "--port",
            str(self.port),
            "--no-open",
        ]
        flags = 0
        if os.name == "nt":
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self.proc = subprocess.Popen(
            cmd,
            cwd=str(SANDBOX_DIR),
            env=base_env(self.db_path, self.extra_env),
            stdout=self.log_fh,
            stderr=subprocess.STDOUT,
            creationflags=flags,
        )
        _ACTIVE_SERVERS.append(self)
        deadline = time.time() + self.start_timeout
        last_err = ""
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(
                    f"测试实例启动即退出（rc={self.proc.returncode}），日志：{self.log_path}\n"
                    + self.tail_log()
                )
            try:
                r = http_request(self.url + "/", timeout=3)
                if r.status == 200:
                    return self
                last_err = f"HTTP {r.status}"
            except Exception as e:  # noqa: BLE001
                last_err = f"{type(e).__name__}: {e}"
            time.sleep(0.25)
        self.stop()
        raise RuntimeError(f"测试实例 {self.url} 在 {self.start_timeout}s 内未就绪（{last_err}）\n"
                           + self.tail_log())

    def tail_log(self, n: int = 40) -> str:
        try:
            text = self.log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return "(无日志)"
        return "\n".join(text.splitlines()[-n:])

    # -- HTTP 便捷方法
    def get(self, path: str, timeout: float = 60.0, **params) -> HttpResult:
        return http_request(url_with(self.url, path, **params), timeout=timeout)

    def get_json(self, path: str, timeout: float = 60.0, **params):
        return http_json(url_with(self.url, path, **params), timeout=timeout)

    def post_json(self, path: str, payload=None, timeout: float = 60.0):
        return http_json(self.url + path, method="POST", payload=payload or {}, timeout=timeout)

    def post(self, path: str, payload=None, timeout: float = 60.0) -> HttpResult:
        data = json.dumps(payload or {}, ensure_ascii=False).encode("utf-8")
        return http_request(
            self.url + path, method="POST", data=data,
            headers={"Content-Type": "application/json; charset=utf-8"}, timeout=timeout,
        )

    # -- 停止 / 清理
    def stop(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                try:
                    self.proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    pass
        if self.log_fh is not None:
            try:
                self.log_fh.close()
            except OSError:
                pass
            self.log_fh = None
        # 等端口真正释放（TIME_WAIT 不影响 bind，但监听必须消失）
        deadline = time.time() + 10
        while time.time() < deadline and port_listening(self.port):
            time.sleep(0.1)
        try:
            _ACTIVE_SERVERS.remove(self)
        except ValueError:
            pass

    @property
    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def __enter__(self) -> "Server":
        return self.start()

    def __exit__(self, exc_type, exc, tb) -> None:
        self.stop()


# --------------------------------------------------------------------------- CLI
def run_study(args: list[str], db_path: Path | None = None, timeout: float = 240.0,
              extra_env: dict | None = None) -> dict:
    """跑 tools/study.py 子命令（沙箱副本）。返回 rc/stdout/stderr/耗时。"""
    cmd = [str(PYTHON), "-X", "utf8", str(SANDBOX_DIR / "tools" / "study.py"), *args]
    started = time.time()
    try:
        cp = subprocess.run(
            cmd,
            cwd=str(SANDBOX_DIR),
            env=base_env(db_path, extra_env),
            capture_output=True,
            timeout=timeout,
        )
        rc, out, err = cp.returncode, cp.stdout, cp.stderr
    except subprocess.TimeoutExpired as e:
        rc, out, err = -9, e.stdout or b"", e.stderr or b""
    return {
        "rc": rc,
        "stdout": out.decode("utf-8", "replace"),
        "stderr": err.decode("utf-8", "replace"),
        "elapsed": round(time.time() - started, 2),
        "cmd": " ".join(cmd),
    }


# --------------------------------------------------------------------------- Edge headless
def edge_dump_dom(url: str, timeout: float = 90.0, virtual_time_ms: int = 8000,
                  screenshot: Path | None = None) -> str:
    """用 Edge headless 渲染页面并返回 DOM（--dump-dom）。"""
    if not EDGE.exists():
        raise FileNotFoundError(f"找不到 Edge：{EDGE}")
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    profile = ARTIFACT_DIR / "edge-profile"
    if profile.exists():
        shutil.rmtree(profile, ignore_errors=True)
    profile.mkdir(parents=True, exist_ok=True)
    cmd = [
        str(EDGE),
        "--headless=new",
        "--disable-gpu",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-extensions",
        "--disable-background-networking",
        "--run-all-compositor-stages-before-draw",
        "--virtual-time-budget=%d" % virtual_time_ms,
        "--user-data-dir=" + str(profile),
    ]
    if screenshot is not None:
        screenshot.parent.mkdir(parents=True, exist_ok=True)
        cmd += ["--screenshot=" + str(screenshot), "--window-size=1440,900"]
    cmd += ["--dump-dom", url]
    cp = subprocess.run(cmd, capture_output=True, timeout=timeout)
    dom = cp.stdout.decode("utf-8", "replace")
    return dom


# --------------------------------------------------------------------------- 真实数据只读校验
REAL_DATA_DBS = [
    PROJECT / "index" / "study.db",
    PROJECT / "subjects" / "生药药动" / "index" / "study.db",
    PROJECT / "subjects" / "药物化学" / "index" / "study.db",
    PROJECT / "subjects" / "生物药剂学与药物动力学" / "index" / "study.db",
]
REAL_DATA_DIRS = [PROJECT / "raw", PROJECT / "notes"]


def real_data_snapshot() -> dict:
    """记录真实数据目录的指纹（只读，用于证明验收过程没有写真实数据）。"""
    snap = {"db": {}, "dirs": {}, "taken_at": time.time()}
    for p in REAL_DATA_DBS:
        try:
            snap["db"][str(p.relative_to(PROJECT))] = {
                "size": p.stat().st_size, "sha256": sha256(p)
            }
        except OSError as e:
            snap["db"][str(p.relative_to(PROJECT))] = {"error": str(e)}
    for d in REAL_DATA_DIRS:
        files = {}
        try:
            for p in sorted(d.rglob("*")):
                if p.is_file():
                    files[str(p.relative_to(d))] = p.stat().st_size
        except OSError as e:
            files["<error>"] = str(e)
        snap["dirs"][d.name] = files
    return snap


def diff_real_snapshot(before: dict, after: dict) -> list[str]:
    """返回真实数据指纹的差异（空 = 验收过程没有改动真实数据）。"""
    diffs: list[str] = []
    for kind in ("db", "dirs"):
        for key in sorted(set(before.get(kind, {})) | set(after.get(kind, {}))):
            b = before.get(kind, {}).get(key)
            a = after.get(kind, {}).get(key)
            if b != a:
                diffs.append(f"{kind}:{key}: {b} -> {a}")
    return diffs


# --------------------------------------------------------------------------- 清理
def cleanup_leftovers() -> list[str]:
    """清理沙箱/数据中的临时 edge profile；返回清理项。"""
    removed: list[str] = []
    profile = ARTIFACT_DIR / "edge-profile"
    if profile.exists():
        shutil.rmtree(profile, ignore_errors=True)
        removed.append(str(profile))
    return removed


def candidate_ports_listening() -> dict[int, bool]:
    return {p: port_listening(p) for p in CANDIDATE_PORTS}


if __name__ == "__main__":  # 手工自检：python tests/env.py
    data = prepare()
    print("代码同步变化：", data["code_changed"] or "（无）")
    print("数据副本：")
    for k in ("main_db", "empty_db", "deck_db"):
        p = data[k]
        print(f"  {k}: {p} ({p.stat().st_size} B)")
    print("不一致文件：", verify_code_sync() or "（无）")
    with Server(tag="selfcheck") as srv:
        print("服务：", srv.url)
        st, obj = srv.get_json("/api/firstrun")
        print("firstrun:", st, json.dumps(obj, ensure_ascii=False)[:300])
        r = srv.get("/")
        print("首页：", r.status, len(r.body), "B, title in body:", "学习看板" in r.text)
    print("端口已释放：", port_free(CANDIDATE_PORTS[0]))
    print("清理：", cleanup_leftovers())
