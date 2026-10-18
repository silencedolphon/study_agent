# Copyright (c) 2026 潜声海豚 <2227663973@qq.com>. All rights reserved.
# 本源码为著作权人私有作品（proprietary）：仅供经书面授权的测试使用；
# 未经著作权人许可，不得复制、分发、公开、发布或用于任何商业用途。
# Unauthorized copying, distribution or publication is strictly prohibited.
# File-ID: SA-26-c0086vf   (project study_agent; keep this line)
"""卡 C-0086 验收：v1.1 发布包（重打 exe + 人的提示词改动 + 分享/本轮新功能在 exe 环境实测）。

## 两条命令，各管一段

    python tools\\verify_c0086_release.py --package        # 静态：包内提示词逐字一致 + exe 同级清单（秒级）
    python tools\\verify_c0086_release.py --exe            # 实测：异地全新目录**真复制**后跑通全流程
    python tools\\verify_c0086_release.py --exe --kp       # 再补一次「AI 知识点总结」真跑（要密钥、几分钱）
    python tools\\verify_c0086_release.py --all --kp       # 连 verify_c0073_exe.py 的 --audit / --quick 一起

## 为什么要新写一个，而不是改 verify_c0073_exe.py

    C-0073 那个脚本验的是「**包能不能跑**」；本卡要验的是「**这一版包里装的是不是
    人改过的那份提示词、这一轮的新功能在 exe 环境里到底能不能用**」。两者的判据
    没有交集，硬塞进一个脚本只会让两边都改不动（C-0073 那份还被 --audit/--quick
    的历史判据引用着）。所以这里另起一个：**静态**与**实测**分成两个模式，
    `--package` 保持秒级，好当交付后的回归命令。

## ★ 为什么 `--exe` 是**真复制**而不是 junction

    C-0073 的验收脚本用目录 junction 把 `_internal` 指回构建目录（省掉 300MB
    复制）。本卡不用那一手：卡面要求的是「**异地全新目录**实测」——junction
    只是让 `sys.executable` 换了地方，`_internal` 里的字节仍留在工程目录里。
    这里整棵 `copytree` 过去（实测约半分钟），并断言部署目录**既不在工程路径下、
    也不在同一个盘**，把"异地"这件事做实。

## 输出约定

    · stdout **只在结束时**输出结论块，**第一行就是判定**（PASS/FAIL），
      紧接着就是全部失败项——大管家与宿主都只看这一屏。
    · 运行过程中的每一步明细写日志文件（`_tmp_c0086/verify-*.log`）。
    · 刻意不在运行时往 stdout 打进度：那会把判定行挤到第 6 行之后，
      而卡面明确要求「结论行放前 6 行」。
"""
from __future__ import annotations

import argparse
import hashlib
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
import uuid
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
APP = ROOT / "release" / "C-0073" / "StudyDashboard"
EXE = APP / "StudyDashboard.exe"
V10_ZIP = ROOT / "release" / "C-0073" / "StudyDashboard-v1.0-win64.zip"
V11_ZIP = ROOT / "release" / "C-0073" / "StudyDashboard-v1.1-win64.zip"
SRC_KNOW = ROOT / "logs" / "knowledge"
LOGDIR = ROOT / "_tmp_c0086"
DEPLOY_ROOT = Path(os.environ.get("TEMP") or r"C:\Windows\Temp") / "sa_c0086_remote"
DEPLOY = DEPLOY_ROOT / "StudyDashboard"

# 包内 4 份**业务**提示词：必须与源文件**逐字一致**（卡面★第一条）。
# `README.md` 不在其列：它是开发说明、含本机路径与内部运维字样，打包时
# 会被 `pack_exe._copy_sanitized` 脱敏——**预期与源不同**。
PROMPT_BIZ = (
    "提示词-知识点总结-v2.md",
    "提示词-知识点总结.md",
    "提示词-名词解释.md",
    "提示词-思维导图结构.md",
)
PROMPT_ALL = PROMPT_BIZ + ("README.md",)

# 人（2026-10-12）手改的 5 处：4 处新措辞必须在、4 处旧措辞必须不在、
# 整节「## 材料不够就直说」必须已删。逐条对，不做模糊匹配。
SEMANTIC_MUST_HAVE = (
    ("尽量依据【材料】",              "铁律：只能→尽量"),
    ("可以用你自己的专业知识补充",      "补充：不许→可以"),
    ("400 字以下",                    "summary：150到400字→400字以下"),
    ("要点尽量少",                    "points：3到6条→尽量少"),
)
SEMANTIC_MUST_NOT_HAVE = (
    ("只能依据【材料】",              "旧铁律措辞"),
    ("不许用你自己的专业知识补充",      "旧补充措辞"),
    ("150 到 400 字",                 "旧 summary 字数"),
    ("3 到 6 条",                     "旧 points 条数"),
    ("## 材料不够就直说",             "旧「材料不够就直说」整节"),
)

# exe 同级应有的 6 个文件（卡面★第 3 条）
SIDE_FILES = ("StudyDashboard.exe", "使用说明.md", "LICENSE", "NOTICE.txt",
              "THIRD-PARTY-NOTICES.md", "THIRD-PARTY-LICENSES.txt")

# 干净环境：只留跑程序必需的，抹掉一切密钥与项目相关环境变量
# （键名必须**全大写**——Windows 上 os.environ 的键一律被规整成大写）。
_KEEP_ENV = ("SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "PATHEXT",
             "TEMP", "TMP", "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE",
             "USERPROFILE", "HOMEDRIVE", "HOMEPATH", "LANG", "PATH")
_EXTRA_ENV = {"OUTLINE_ANALYSIS": "0", "FIGURE_DESC": "0", "PYTHONUTF8": "1"}

TEXT_TOKEN = "C0086TEXTTEST"

_checks: list[tuple[bool, str]] = []
_logf = None


# ---------------------------------------------------------------- 输出
def _setup_output() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            if stream.isatty():
                stream.reconfigure(errors="replace")
            else:
                stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:                          # noqa: BLE001
            pass


def log(msg: str) -> None:
    """明细进日志文件（stdout 只在结束时出结论块，见文件头）。"""
    try:
        if _logf:
            _logf.write(msg + "\n")
            _logf.flush()
    except Exception:                              # noqa: BLE001
        pass


def log_both(msg: str) -> None:
    log(msg)
    try:
        print(msg, flush=True)
    except UnicodeEncodeError:
        enc = sys.stdout.encoding or "utf-8"
        print(msg.encode(enc, "replace").decode(enc, "replace"), flush=True)


def ck(ok: bool, msg: str) -> bool:
    _checks.append((bool(ok), msg))
    log("  [%s] %s" % ("OK" if ok else "NG", msg))
    return bool(ok)


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for blk in iter(lambda: fh.read(1 << 20), b""):
            h.update(blk)
    return h.hexdigest()


def sanitize_like_pack(blob: bytes) -> tuple[bytes, int]:
    """按 `pack_exe` **打包时同一套**规则脱敏，返回 (结果, 替换处数)。

    用途：判断"包内那份与源文件不同"到底是**声明的脱敏**造成的，
    还是内容真的被改过。复用打包脚本自己的 `_SANITIZE_PATTERNS`，
    避免"打包用一套、验收用另一套"的自欺（与 `verify_c0073_exe.py --audit`
    复用 `pack_exe.audit_tree` 是同一个道理）。
    """
    sys.path.insert(0, str(HERE))
    import pack_exe  # noqa: PLC0415
    n = 0
    for pat in pack_exe._SANITIZE_PATTERNS:       # noqa: SLF001
        blob, k = pat.subn("<本机路径>".encode("utf-8"), blob)
        n += k
    return blob, n


def zip_files(zp: Path) -> dict[str, str]:
    """读出 zip 里每个**文件**条目的 {相对名: sha256 前16}（目录条目不算）。"""
    out: dict[str, str] = {}
    try:
        with zipfile.ZipFile(zp) as z:
            for i in z.infolist():
                if i.filename.endswith("/"):
                    continue
                name = i.filename
                try:
                    name = name.encode("cp437").decode("utf-8")
                except (UnicodeEncodeError, UnicodeDecodeError):
                    pass
                out[name] = hashlib.sha256(z.read(i)).hexdigest()[:16]
    except (zipfile.BadZipFile, OSError):
        return {}
    return out


def diff_lines(a: bytes, b: bytes, limit: int = 12) -> list[str]:
    import difflib
    al = a.decode("utf-8", "replace").splitlines()
    bl = b.decode("utf-8", "replace").splitlines()
    return [l for l in difflib.unified_diff(al, bl, "源", "包内",
                                            lineterm="", n=0)][2:][:limit]


# ---------------------------------------------------------------- HTTP
def http_get(url: str, timeout: float = 15.0):
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read()


def http_post(url: str, payload: dict, timeout: float = 900.0):
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        raw = e.read() or b""
        try:
            return e.code, json.loads(raw or b"{}")
        except ValueError:
            return e.code, {"error": raw.decode("utf-8", "replace")[:400]}


def http_upload(url: str, folder: str, files: list[tuple[str, bytes]],
                timeout: float = 600.0):
    """`POST /api/upload` 的 multipart 体。

    ★ `folder` 字段必须排在文件**之前**：后端是边收边落盘的流式解析，遇到第一个
      文件时才去解析目标目录（见 `dashboard.py::_parse_multipart_stream`）。
    """
    boundary = "----C0086" + uuid.uuid4().hex
    buf: list[bytes] = []

    def field(name: str, value: str) -> None:
        buf.append(('--%s\r\nContent-Disposition: form-data; name="%s"\r\n\r\n'
                    % (boundary, name)).encode("utf-8"))
        buf.append(value.encode("utf-8") + b"\r\n")

    field("folder", folder)
    for fname, blob in files:
        buf.append(('--%s\r\nContent-Disposition: form-data; name="files";'
                    ' filename="%s"\r\nContent-Type: application/octet-stream'
                    '\r\n\r\n' % (boundary, fname)).encode("utf-8"))
        buf.append(blob + b"\r\n")
    buf.append(("--%s--\r\n" % boundary).encode("utf-8"))
    body = b"".join(buf)
    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": "multipart/form-data; boundary=%s" % boundary})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        raw = e.read() or b""
        try:
            return e.code, json.loads(raw or b"{}")
        except ValueError:
            return e.code, {"error": raw.decode("utf-8", "replace")[:400]}


# ---------------------------------------------------------------- 运行 exe
def free_port(start: int = 8851) -> int:
    for p in range(start, start + 60):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", p))
            except OSError:
                continue
        return p
    raise SystemExit("找不到空闲端口")


def local_api_key() -> str:
    """取一把可用于真调用的密钥（来源顺序照抄 `core/chat.py::load_api_key`）。

    ⚠ 只进子进程环境，**不打印、不落盘**。
    """
    k = os.environ.get("DEEPSEEK_API_KEY", "")
    if k.startswith("sk-"):
        return k.strip()
    p = ROOT / ".env"
    if p.is_file():
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            s = line.strip()
            if "=" not in s or s.startswith("#"):
                continue
            key, _, val = s.partition("=")
            if key.strip() == "DEEPSEEK_API_KEY":
                v = val.strip().strip('"').strip("'")
                if v:
                    return v
    import re as _re
    for cp in (Path(r"C:\Users\Lenovo\.dsh\.credentials.yaml"),
               Path.home() / ".dsh" / ".credentials.yaml"):
        if not cp.is_file():
            continue
        try:
            txt = cp.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        m = _re.search(
            r"DEEPSEEK_API_KEY\s*:\s*['\"]?(sk-[A-Za-z0-9_\-\.]{8,})", txt)
        if m:
            return m.group(1)
    return ""


def child_env(with_key: bool = False) -> dict:
    env = {k: v for k, v in os.environ.items() if k.upper() in _KEEP_ENV}
    env.update(_EXTRA_ENV)
    if with_key:
        k = local_api_key()
        if k:
            env["DEEPSEEK_API_KEY"] = k
    return env


class App:
    def __init__(self, exe: Path, port: int, log_path: Path,
                 with_key: bool = False) -> None:
        self.exe, self.port, self.log_path, self.with_key = exe, port, log_path, with_key
        self.proc: subprocess.Popen | None = None
        self.fh = None

    @property
    def base(self) -> str:
        return "http://127.0.0.1:%d" % self.port

    def start(self, wait: float = 120.0) -> int:
        self.fh = self.log_path.open("a", encoding="utf-8", errors="replace")
        self.fh.write("\n===== 启动 %s --port %d（%s）=====\n"
                      % (time.strftime("%H:%M:%S"), self.port, self.exe))
        self.fh.flush()
        self.proc = subprocess.Popen(
            [str(self.exe), "--port", str(self.port), "--no-open"],
            cwd=str(self.exe.parent), env=child_env(self.with_key),
            stdout=self.fh, stderr=subprocess.STDOUT)
        t0, last = time.time(), ""
        while time.time() - t0 < wait:
            if self.proc.poll() is not None:
                raise SystemExit("exe 启动即退出（退出码 %s），日志见 %s"
                                 % (self.proc.returncode, self.log_path))
            try:
                st, _ = http_get(self.base + "/", timeout=3.0)
                if st == 200:
                    return int((time.time() - t0) * 1000)
            except (urllib.error.URLError, OSError, TimeoutError) as e:
                last = str(e)
            time.sleep(0.4)
        raise SystemExit("exe 启动超时（%.0fs）：%s，日志见 %s"
                         % (wait, last, self.log_path))

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=10)
        if self.fh:
            self.fh.close()
            self.fh = None


# ---------------------------------------------------------------- 自造样张
def make_text_pdf(path: Path) -> None:
    """一份**有文字层**的样张 PDF（自己造，不含任何真实资料）。

    刻意写得有点结构：这样「AI 知识点总结」才有东西可总结。
    """
    import fitz
    doc = fitz.open()
    pages = [
        [
            "C0086 release self-check sample (not user data).",
            "",
            "一、定义",
            "光合作用是绿色植物利用光能，把二氧化碳和水合成有机物，",
            "并释放氧气的过程。",
            "",
            "二、场所与条件",
            "叶绿体是光合作用的场所，叶绿素负责吸收光能。",
            "",
            "三、原料与产物",
            "原料：二氧化碳和水。产物：有机物（葡萄糖）和氧气。",
            "",
            "Unique marker for retrieval assertion: " + TEXT_TOKEN,
        ],
        [
            "四、两个阶段",
            "光反应在类囊体薄膜上进行，暗反应在叶绿体基质中进行。",
            "",
            "五、影响因素",
            "光照强度、二氧化碳浓度与温度都会影响速率。",
        ],
    ]
    for lines in pages:
        page = doc.new_page()
        y = 90.0
        for ln in lines:
            # 纯 ASCII 行走内置 helv（半角宽）：CJK 字体的 ASCII 是全角宽，
            # 一行长英文会顶出页面右边界被裁掉尾巴（C-0073 实测踩过）。
            ascii_only = all(ord(ch) < 128 for ch in ln)
            page.insert_text((60, y), ln, fontsize=12.5,
                             fontname="helv" if ascii_only else "china-s")
            y += 24
    doc.save(str(path))
    doc.close()


# ---------------------------------------------------------------- 模式一：静态
def zip_member_bytes(zp: Path, suffix: str) -> bytes | None:
    """从 zip 里按**文件名后缀**取一份内容（成员名的中文编码方式不确定，
    所以只认后缀，取不到就返回 None——这个对比是佐证、不是判据）。"""
    try:
        with zipfile.ZipFile(zp) as z:
            for name in z.namelist():
                fixed = name
                try:
                    fixed = name.encode("cp437").decode("utf-8")
                except (UnicodeEncodeError, UnicodeDecodeError):
                    pass
                if fixed.replace("\\", "/").endswith(suffix):
                    return z.read(name)
    except (zipfile.BadZipFile, OSError):
        return None
    return None


def mode_package() -> None:
    log("=" * 78)
    log("模式 --package：静态校验包内容（%s）" % APP)
    log("=" * 78)

    ck(EXE.is_file(), "①-a exe 存在：%s" % EXE)
    if not EXE.is_file():
        return

    # ---- 1. 4 份业务提示词逐字一致（卡面★第一条）
    #
    # ⚠ 判据为什么要写成两级：**打包脚本对随包文本资源有一道声明的脱敏**
    #   （`pack_exe._copy_sanitized`，只把"本机盘符路径 / 内部运维痕迹"换成
    #   `<本机路径>`）。实测 `提示词-知识点总结.md` 头部的**版本沿革表**里写着
    #   `cards\C-0031\提示词-知识点总结.md` 这类内部卡目录 —— 它会被脱敏规则
    #   改写，于是 sha256 与源**不可能**相等；而它**必须**被改写：原样带进包
    #   会被 `pack_exe` 自己的审计门禁按「内部痕迹」拦下，包根本打不出来。
    #   所以判据是：**要么逐字节相同，要么差异恰好等于打包脚本自己声明的那套
    #   脱敏**（用同一个 `_SANITIZE_PATTERNS` 对源做一遍再比）。任何第三种情况
    #   ——内容被改、少字、多字——都判 FAIL。
    log("\n【1】包内提示词 vs 源文件 sha256 逐份对照")
    log("  %-34s %-18s %-18s %s" % ("文件", "源 sha256(前16)", "包内 sha256(前16)", "结论"))
    exact, sanit_only, bad_files = 0, [], []
    for name in PROMPT_BIZ:
        src = SRC_KNOW / name
        dst = APP / "_internal" / "logs" / "knowledge" / name
        if not src.is_file():
            ck(False, "①-b 源文件缺失：%s" % src)
            continue
        if not dst.is_file():
            ck(False, "①-c 包内缺文件：%s" % dst)
            continue
        sb, db = src.read_bytes(), dst.read_bytes()
        hs, hd = (hashlib.sha256(sb).hexdigest(), hashlib.sha256(db).hexdigest())
        if hs == hd:
            exact += 1
            log("  %-34s %-18s %-18s %s" % (name, hs[:16], hd[:16], "逐字一致"))
            continue
        exp, n = sanitize_like_pack(sb)
        if exp == db and n > 0:
            sanit_only.append((name, n))
            log("  %-34s %-18s %-18s %s" % (name, hs[:16], hd[:16],
                                            "仅 %d 处声明脱敏（内容未改）" % n))
            for l in diff_lines(sb, db):
                log("        差异行 %s" % l)
        else:
            bad_files.append(name)
            log("  %-34s %-18s %-18s %s" % (name, hs[:16], hd[:16], "★内容被改"))
            for l in diff_lines(sb, db):
                log("        ★差异行 %s" % l)
    ck(not bad_files and (exact + len(sanit_only)) == len(PROMPT_BIZ),
       "① 包内 4 份业务提示词与 logs\\knowledge\\ 源文件一致：%d 份逐字节相同%s%s"
       % (exact,
          ("；%d 份仅差异于打包脚本声明的脱敏（%s）"
           % (len(sanit_only), "、".join("%s %d处" % (n, c) for n, c in sanit_only)))
          if sanit_only else "",
          ("；★内容被改 " + "、".join(bad_files)) if bad_files else ""))
    # ①-e 人改的那一份：必须**逐字节相同**（不含任何脱敏差异）——这是卡面★核心
    v2b = SRC_KNOW / "提示词-知识点总结-v2.md"
    v2p = APP / "_internal" / "logs" / "knowledge" / "提示词-知识点总结-v2.md"
    if v2b.is_file() and v2p.is_file():
        ck(sha256_file(v2b) == sha256_file(v2p),
           "①-e ★人（10-12）改过的 提示词-知识点总结-v2.md 在包内**逐字节相同**："
           "sha256=%s（%d B）" % (sha256_file(v2b), v2b.stat().st_size))

    # 每份的完整 sha256 也落一行，供交付说明直接抄
    for name in PROMPT_BIZ:
        src = SRC_KNOW / name
        if src.is_file():
            log("  · %s  sha256=%s  %d B" % (name, sha256_file(src), src.stat().st_size))

    # ---- 1b. 「包内」的**字面**含义：发布 zip 里那 4 份也要逐字一致。
    #      交付物是 zip，不是解包目录——只验目录等于少验一步。
    if V11_ZIP.is_file():
        zrows, zbad = [], []
        for name in PROMPT_BIZ:
            src = SRC_KNOW / name
            blob = zip_member_bytes(V11_ZIP, "_internal/logs/knowledge/" + name)
            sb = src.read_bytes() if src.is_file() else b""
            ok = False
            if blob is not None and sb:
                if blob == sb:
                    ok = True
                else:
                    exp, _n = sanitize_like_pack(sb)
                    ok = blob == exp
            zrows.append((name, ok, len(blob) if blob else -1))
            if not ok:
                zbad.append(name)
        ck(not zbad, "①-d 发布 zip（%s）内 4 份业务提示词同样一致"
                     "（逐字节相同或仅声明脱敏）：%d/%d%s"
           % (V11_ZIP.name, len(PROMPT_BIZ) - len(zbad), len(PROMPT_BIZ),
              ("；不符 " + "、".join(zbad)) if zbad else ""))
        for name, ok, sz in zrows:
            log("      %s zip 内 %-32s %8d B" % ("OK " if ok else "NG ", name, sz))
    else:
        log("      · v1.1 的 zip 尚未产出（%s）→ 跳过 zip 内复核" % V11_ZIP.name)

    # ---- 2. README.md：预期被脱敏（卡面明确「README.md 除外」）
    r_src = SRC_KNOW / "README.md"
    r_dst = APP / "_internal" / "logs" / "knowledge" / "README.md"
    if r_src.is_file() and r_dst.is_file():
        same = sha256_file(r_src) == sha256_file(r_dst)
        blob = r_dst.read_bytes()
        leaks = [t for t in (b"steward-lite", b".dsh\\consoles", b".dsh/consoles")
                 if t in blob]
        import re as _re
        leaks += ["cards\\C-xxxx"] if _re.search(rb"cards[\\/]+C-\d+", blob) else []
        ck(r_dst.stat().st_size > 500 and not leaks,
           "② README.md 随包且已脱敏（与源%s；包内 %d B；内部痕迹 %d 处）"
           % ("相同" if same else "不同（预期）", r_dst.stat().st_size, len(leaks)))
    else:
        ck(False, "② README.md 未随包：源=%s 包内=%s" % (r_src.is_file(), r_dst.is_file()))

    # ---- 3. 人改过的 5 处语义（在**包内**那份 v2 里验，不是源文件里验）
    v2 = APP / "_internal" / "logs" / "knowledge" / "提示词-知识点总结-v2.md"
    if not v2.is_file():
        ck(False, "③ 包内缺 提示词-知识点总结-v2.md")
    else:
        text = v2.read_text(encoding="utf-8", errors="replace")
        miss = [d for s, d in SEMANTIC_MUST_HAVE if s not in text]
        ck(not miss, "③-a 人改的 4 处新措辞都在包内 v2 里%s"
           % ("" if not miss else "；缺：" + "、".join(miss)))
        for s, d in SEMANTIC_MUST_HAVE:
            log("      %s 「%s」=%s" % ("OK " if s in text else "NG ", d, s in text))
        left = [d for s, d in SEMANTIC_MUST_NOT_HAVE if s in text]
        ck(not left, "③-b 旧措辞与已删整节确认不在包内 v2 里%s"
           % ("" if not left else "；仍在：" + "、".join(left)))
        for s, d in SEMANTIC_MUST_NOT_HAVE:
            log("      %s 旧「%s」仍在=%s" % ("OK " if s not in text else "NG ", d, s in text))

        # 与 v1.0 包里那份对比：证明"改动真的进包了"（佐证，取不到不算失败）
        old = zip_member_bytes(V10_ZIP, "_internal/logs/knowledge/提示词-知识点总结-v2.md")
        if old is None:
            log("      · v1.0 包内 v2 提示词取不到（成员名编码/包不存在）→ 跳过此佐证")
        else:
            new = v2.read_bytes()
            log("      · v1.0 包内 v2  sha256=%s  %d B；v1.1 包内 %s  %d B"
                % (hashlib.sha256(old).hexdigest()[:16], len(old),
                   hashlib.sha256(new).hexdigest()[:16], len(new)))
            ck(old != new, "③-c 包内 v2 提示词确与 v1.0 包里那份不同（人的改动真进了这一版）")

    # ---- 4. exe 同级的 6 个文件
    got = []
    for name in SIDE_FILES:
        p = APP / name
        got.append((name, p.is_file(), p.stat().st_size if p.is_file() else -1))
    lack = [n for n, ok, _ in got if not ok]
    ck(not lack, "④ exe 同级 %d 个文件齐（%s）%s"
       % (len(SIDE_FILES), "、".join(SIDE_FILES),
          "" if not lack else "；缺：" + "、".join(lack)))
    for n, ok, sz in got:
        log("      %s %-26s %10d B" % ("OK " if ok else "NG ", n, sz))
    # 同级**不得**多出红线里点名的那几样
    bad = [p.name for p in APP.iterdir()
           if p.name.lower() in (".env", "config.local.json") or p.name in ("raw", "subjects")]
    ck(not bad, "⑤ exe 同级无 .env / config.local.json / raw / subjects%s"
       % ("" if not bad else "；发现：" + "、".join(bad)))

    # ---- 5. 包内 core/ 与 tools/ 下不得有本项目 .py 明文（与 pack_exe 同一判据）
    ours = []
    for p in APP.rglob("*.py"):
        rel = p.relative_to(APP).as_posix()
        parts = rel.split("/")
        if parts and parts[0] == "_internal":
            parts = parts[1:]
        if len(parts) >= 2 and parts[0] in ("core", "tools"):
            ours.append(rel)
    ck(not ours, "⑥ 包内无本项目源码明文（core/、tools/ 下的 .py）：%d 个%s"
       % (len(ours), ("；" + "、".join(ours[:5])) if ours else ""))

    # ---- 7. 与上一版发布 zip 的**逐文件**差异：交付说明里那张差异表要能复现，
    #         也顺手证明"改动范围是可控的"（不是整包换了一遍）。
    if V10_ZIP.is_file() and V11_ZIP.is_file():
        a, b = zip_files(V10_ZIP), zip_files(V11_ZIP)
        added, gone = sorted(b.keys() - a.keys()), sorted(a.keys() - b.keys())
        changed = sorted(n for n in a.keys() & b.keys() if a[n] != b[n])
        log("\n【7】与上一版 %s 的逐文件差异" % V10_ZIP.name)
        log("  v1.0 文件 %d ；v1.1 文件 %d ；新增 %d ；消失 %d ；同名但内容变化 %d"
            % (len(a), len(b), len(added), len(gone), len(changed)))
        for n in changed:
            log("      ~ %s  %s → %s" % (n, a[n], b[n]))
        key = "_internal/logs/knowledge/提示词-知识点总结-v2.md"
        ck(not added and not gone and len(changed) <= 6
           and a.get(key) != b.get(key),
           "⑥-d 与 v1.0 的差异可控：文件集合完全相同（%d 个）、内容仅 %d 个变化（%s）；"
           "其中 v2 提示词 %s → %s（★人的改动）"
           % (len(b), len(changed),
              "、".join(n.rsplit("/", 1)[-1] for n in changed),
              a.get(key), b.get(key)))
    else:
        log("\n【7】v1.0 包不在（%s）→ 跳过版本差异对比"
            % V10_ZIP.name)


# ---------------------------------------------------------------- 模式二：实测
def deploy_remote(exe: Path) -> tuple[str, float]:
    """**真复制**整包到异地全新目录（不是 junction）。

    断言"异地"= 既不在工程路径下、也不在同一个盘。
    """
    app_dir = exe.parent
    rel_ok = ROOT.resolve() not in DEPLOY.resolve().parents
    drive_ok = DEPLOY.resolve().drive.lower() != ROOT.resolve().drive.lower()
    if DEPLOY.exists():
        shutil.rmtree(DEPLOY, ignore_errors=True)
    DEPLOY.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    shutil.copytree(app_dir, DEPLOY, dirs_exist_ok=True)
    dt = time.time() - t0
    n_src = sum(1 for p in app_dir.rglob("*") if p.is_file())
    n_dst = sum(1 for p in DEPLOY.rglob("*") if p.is_file())
    ck(rel_ok and drive_ok and n_src == n_dst,
       "⑦ 真复制到异地全新目录：%s（%d 个文件 / 与源一致 %s；不在工程路径下 %s；跨盘 %s；用时 %.1f 秒）"
       % (DEPLOY, n_dst, n_src == n_dst, rel_ok, drive_ok, dt))
    return "真复制 %s → %s（%.1f 秒）" % (app_dir, DEPLOY, dt), dt


def docs_of(base: str) -> list[dict]:
    try:
        _, b = http_get(base + "/api/docs")
        return json.loads(b or b"{}").get("documents") or []
    except Exception:                              # noqa: BLE001
        return []


def mode_exe(with_kp: bool, keep: bool) -> None:
    log("\n" + "=" * 78)
    log("模式 --exe：异地全新目录实测（%s）" % DEPLOY)
    log("=" * 78)
    if not EXE.is_file():
        ck(False, "⑦-a 找不到 exe：%s" % EXE)
        return

    run_log = DEPLOY_ROOT / "run.log"
    DEPLOY_ROOT.mkdir(parents=True, exist_ok=True)
    if run_log.exists():
        run_log.unlink()
    deploy_remote(EXE)

    port = free_port()
    app = App(DEPLOY / EXE.name, port, run_log, with_key=with_kp)
    try:
        ms = app.start()
        st, body = http_get(app.base + "/")
        ck(st == 200, "⑧ 启动 exe → GET / HTTP %d（%d 字节，启动 %.1f 秒，端口 %d）"
           % (st, len(body), ms / 1000.0, port))

        want = ["index", "logs", "notes", "raw", "logs/knowledge"]
        lack = [d for d in want if not (DEPLOY / d).is_dir()]
        ck(not lack, "⑨ 可写数据落在 exe 同级：%s%s"
           % ("、".join(want), ("；缺 " + "、".join(lack)) if lack else ""))

        # ★ C-0077 回归：空库首启就**必须**有 figure_desc 表
        db = DEPLOY / "index" / "study.db"
        tbls: list[str] = []
        if db.is_file():
            try:
                c = sqlite3.connect("file:%s?mode=ro" % db.as_posix(), uri=True)
                try:
                    tbls = [r[0] for r in c.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'")]
                finally:
                    c.close()
            except sqlite3.Error as e:
                log("      · 读 study.db 失败：%s" % e)
        ck(db.is_file() and "figure_desc" in tbls,
           "⑩ C-0077 回归：全新空库（%d B）里 figure_desc 表已由 init_db 建出"
           "（表 %d 张；%s）"
           % (db.stat().st_size if db.is_file() else -1, len(tbls),
              "命中 figure_desc" if "figure_desc" in tbls else "★没有 figure_desc"))

        _, s0 = http_get(app.base + "/api/stats")
        n0 = int((json.loads(s0 or b"{}").get("documents") or {}).get("total") or 0)
        ck(n0 == 0, "⑪ 空程序：启动时文档数 = %d（应为 0，不带任何用户数据）" % n0)

        # 提示词真源在 exe 环境**读得到**，且哈希就是人改过的那份
        _, sbody = http_get(app.base + "/api/settings")
        slots = (json.loads(sbody or b"{}").get("prompts") or [])
        seeded = sorted(p.name for p in (DEPLOY / "logs" / "knowledge").glob("*.md"))
        v2_src = sha256_file(SRC_KNOW / "提示词-知识点总结-v2.md")
        v2_seed = DEPLOY / "logs" / "knowledge" / "提示词-知识点总结-v2.md"
        ck(len(seeded) == len(PROMPT_ALL) and v2_seed.is_file()
           and sha256_file(v2_seed) == v2_src,
           "⑫ exe 运行时提示词真源：种下 %d 份（%s），其中 v2 的 sha256 与源一致=%s"
           % (len(seeded), "、".join(seeded),
              v2_seed.is_file() and sha256_file(v2_seed) == v2_src))
        log("      · /api/settings prompts：%s"
            % "、".join("%s=%s(%s字符)" % (x.get("id"), x.get("ok"), x.get("chars"))
                        for x in slots))

        # ---------------- 入库 ----------------
        raw = DEPLOY / "raw"
        raw.mkdir(parents=True, exist_ok=True)
        tpdf = raw / "c0086_text.pdf"
        make_text_pdf(tpdf)
        st, r1 = http_post(app.base + "/api/ingest",
                           {"paths": ["c0086_text.pdf"], "no_figures": True})
        n1 = int(r1.get("chunks") or 0)
        ck(st == 200 and n1 > 0, "⑬ 入库有文字层 PDF：chunks=%d" % n1)

        docs = docs_of(app.base)
        doc_id = int(docs[0]["id"]) if docs else 0
        ck(doc_id > 0, "⑭ 库里拿得到 doc_id=%d（共 %d 份）" % (doc_id, len(docs)))

        # ★ C-0083 拖拽入库：走 /api/upload 的 multipart（folder 在前）
        drop_name = "c0086_drop.pdf"
        st, up = http_upload(app.base + "/api/upload", "",
                             [(drop_name, tpdf.read_bytes())])
        saved = up.get("saved") or []
        on_disk = (raw / drop_name).is_file()
        ck(st == 200 and bool(saved) and on_disk,
           "⑮ C-0083 拖拽入库（POST /api/upload，folder 先于文件）：saved=%s，"
           "raw\\%s 落盘=%s" % (json.dumps(saved, ensure_ascii=False)[:90],
                                drop_name, on_disk))
        # 落盘后**不自动入库**：库里的文档数不变
        ck(len(docs_of(app.base)) == len(docs),
           "⑮-b 拖拽上传**不自动入库**（文档数仍为 %d）" % len(docs_of(app.base)))

        # ★ C-0082 笔记：只显示自写内容 + 可编辑
        note_title = "C0086 自写笔记"
        st, nsave = http_post(app.base + "/api/note",
                              {"title": note_title, "body": "第一版正文：光合作用在叶绿体。",
                               "tags": ["c0086"], "refs": ["自检"], "kind": "note"})
        try:
            _, nl = http_get(app.base + "/api/notes")
            notes = json.loads(nl or b"{}").get("notes") or []
        except Exception:                          # noqa: BLE001
            notes = []
        mine = [n for n in notes if note_title in (n.get("title") or "")]
        fname = (mine[0].get("file") if mine else "") or ""
        body0 = ""
        if fname:
            _, nread = http_get(app.base + "/api/note?name=" + urllib.parse.quote(fname))
            body0 = (json.loads(nread or b"{}").get("body") or "")
        ck(bool(mine) and "第一版正文" in body0,
           "⑯ C-0082 笔记写入并读回：列表命中 %d 条，file=%s，body 含自写正文=%s"
           % (len(mine), fname, "第一版正文" in body0))

        st, nupd = http_post(app.base + "/api/note/update",
                             {"file": fname, "body": "第二版正文：已改写（exe 实测）。"})
        body1 = ""
        if fname:
            _, nread2 = http_get(app.base + "/api/note?name=" + urllib.parse.quote(fname))
            body1 = (json.loads(nread2 or b"{}").get("body") or "")
        ck(bool(nupd.get("ok", nupd.get("file"))) and "第二版正文" in body1
           and "第一版正文" not in body1,
           "⑰ C-0082 笔记可编辑（POST /api/note/update）：读回已是第二版=%s"
           % ("第二版正文" in body1))

        # ★ 分享：导出 → 看包 → 导入（skip / duplicate）
        st, ex = http_post(app.base + "/api/share/export",
                           {"doc_id": doc_id, "with_original": True,
                            "by": "C0086 exe 自检", "note": "release v1.1 实测"})
        zip_path = Path(str(ex.get("zip") or ""))
        in_deploy = str(zip_path).lower().startswith(str(DEPLOY).lower())
        ck(st == 200 and ex.get("ok") and zip_path.is_file() and in_deploy,
           "⑱ 分享**导出**（POST /api/share/export）：%s（%d 字节，counts=%s，落在 exe 同级=%s）"
           % (zip_path.name, ex.get("bytes") or (zip_path.stat().st_size
                                                 if zip_path.is_file() else -1),
              json.dumps(ex.get("counts") or {}, ensure_ascii=False)[:90], in_deploy))

        st, ins = http_post(app.base + "/api/share/inspect", {"zip": str(zip_path)})
        ck(st == 200 and not ins.get("error"),
           "⑲ 分享看包（POST /api/share/inspect）：%s"
           % json.dumps({k: ins.get(k) for k in ("ok", "title", "n_chunks", "conflict")},
                        ensure_ascii=False)[:140])

        st, im1 = http_post(app.base + "/api/share/import",
                            {"zip": str(zip_path), "on_conflict": "skip"})
        ck(st == 200 and im1.get("ok") and im1.get("status") == "skipped",
           "⑳ 分享**导入**（同库 skip）：status=%s（库里已有同一份，应当跳过）"
           % im1.get("status"))

        n_before = len(docs_of(app.base))
        st, im2 = http_post(app.base + "/api/share/import",
                            {"zip": str(zip_path), "on_conflict": "duplicate"})
        n_after = len(docs_of(app.base))
        dup_id = int(im2.get("doc_id") or 0)
        ck(st == 200 and im2.get("ok") and im2.get("status") == "imported"
           and n_after == n_before + 1,
           "㉑ 分享**导入**（duplicate 真导入）：status=%s，doc_id=%d，"
           "文档数 %d→%d，零模型调用 model_calls=%s / 费用 %s 元"
           % (im2.get("status"), dup_id, n_before, n_after,
              im2.get("model_calls"), im2.get("cost_cny")))
        # 导入进来的那份必须能检索到（证明 chunks/fts 一起进来了）
        try:
            _, sr = http_get(app.base + "/api/search?q=" + TEXT_TOKEN)
            hits = json.loads(sr or b"{}").get("hits") or []
        except Exception:                          # noqa: BLE001
            hits = []
        ck(bool(hits), "㉒ 导入后检索得到内容：命中 %d 条（首条出处=%s）"
           % (len(hits), (hits[0].get("cite") if hits else "-")))

        # ★ C-0085 重命名三条
        st, rd = http_post(app.base + "/api/doc/rename",
                           {"doc_id": doc_id, "title": "C0086 改名后的课件",
                            "dry_run": True})
        st2, rc = http_post(app.base + "/api/doc/rename",
                            {"doc_id": doc_id, "title": "C0086 改名后的课件",
                             "confirm": True})
        titles = [d.get("title") for d in docs_of(app.base) if int(d.get("id") or 0) == doc_id]
        ck(st == 200 and st2 == 200
           and (titles and titles[0] == "C0086 改名后的课件"),
           "㉓ C-0085 课件标题重命名（dry_run + confirm）：库里 title=%s" % (titles or "-"))

        st, rr = http_post(app.base + "/api/rename/raw",
                           {"rel": drop_name, "new_name": "c0086_drop_renamed.pdf",
                            "confirm": True})
        moved = (raw / "c0086_drop_renamed.pdf").is_file() and not (raw / drop_name).is_file()
        ck(st == 200 and moved, "㉔ C-0085 原文件重命名：raw\\%s → c0086_drop_renamed.pdf "
                                "（旧名已消失=%s）" % (drop_name, moved))

        st, ra = http_post(app.base + "/api/artifact/rename",
                           {"doc_id": doc_id, "name": "C0086 产物新标题", "dry_run": True})
        ck(st in (200,) and isinstance(ra, dict),
           "㉕ C-0085 产物标题重命名路由可用（dry_run）：%s"
           % json.dumps(ra, ensure_ascii=False)[:130])

        # ---------------- AI 知识点总结（--kp，真调模型，几分钱）----------------
        if with_kp:
            t0 = time.time()
            st, kr = http_post(app.base + "/api/kp-build",
                               {"doc_id": doc_id, "max_cost": 0.1}, timeout=1800)
            t_post = time.time() - t0
            seen, last = False, {}
            t1 = time.time()
            while time.time() - t1 < 1800:
                try:
                    _, pb = http_get(app.base + "/api/kp-build/progress")
                    pr = json.loads(pb or b"{}")
                except Exception:                  # noqa: BLE001
                    pr = {}
                last = pr or last
                if pr.get("active"):
                    seen = True
                elif seen:
                    break
                time.sleep(3)
            out = DEPLOY / "logs" / "knowledge" / ("%d-末级知识点总结.json" % doc_id)
            meta, n_kp, status = {}, 0, ""
            if out.is_file():
                try:
                    d = json.loads(out.read_text(encoding="utf-8"))
                    meta = d.get("meta") or {}
                    status = str(meta.get("status") or "")
                    n_kp = len(d.get("knowledge_points") or [])
                except Exception as e:             # noqa: BLE001
                    log("      · 读产物失败：%s" % e)
            ck(out.is_file() and n_kp > 0 and status != "failed",
               "㉖ AI 知识点总结（★ meta.status 不得为 failed）：POST /api/kp-build → %s；"
               "产物 %s（%d 字节，知识点 %d 条，meta.status=%s，"
               "prompt_sha256=%s，POST 用时 %.1f 秒，轮询 %.1f 秒）"
               % (json.dumps(kr, ensure_ascii=False)[:70], out.name,
                  out.stat().st_size if out.is_file() else -1, n_kp,
                  status or "(无)", str(meta.get("prompt_sha256") or "")[:16],
                  t_post, time.time() - t1))
            # ★ C-0078 回归：产物里的 ¥ 计费字样（GBK 编不出的字符）确实写进了文件
            blob = out.read_bytes() if out.is_file() else b""
            has_yen = "¥".encode("utf-8") in blob
            ck(out.is_file() and status != "failed",
               "㉗ C-0078 回归：产物在 exe（GBK 控制台）下**写全了**（含 ¥ 计费字符=%s，"
               "UTF-8 解码可读=%s）"
               % (has_yen, bool(meta)))
            # 有产物了 → 产物标题重命名可以**真改**
            if out.is_file():
                st, ra2 = http_post(app.base + "/api/artifact/rename",
                                    {"doc_id": doc_id, "name": "C0086 产物新标题",
                                     "confirm": True})
                ck(st == 200 and not ra2.get("error"),
                   "㉘ C-0085 产物标题重命名（真改）：%s"
                   % json.dumps(ra2, ensure_ascii=False)[:130])

        # ★ C-0084 批量真删（放在最后：它会把资料删掉）
        target = dup_id or doc_id
        st, dl = http_post(app.base + "/api/docs/delete",
                           {"doc_ids": [target], "confirm": True,
                            "delete_file": True, "purge_orphans": True})
        n_left = len(docs_of(app.base))
        ck(st == 200 and not dl.get("error") and n_left < n_after,
           "㉙ C-0084 批量真删（POST /api/docs/delete）：删 doc_id=%d，"
           "文档数 %d→%d，deleted=%s"
           % (target, n_after, n_left,
              json.dumps(dl.get("deleted_doc_ids") or dl.get("deleted") or [],
                         ensure_ascii=False)[:60]))

        app.stop()

        # ---------------- 持久性 ----------------
        ms2 = app.start()
        st, _ = http_get(app.base + "/")
        ck(st == 200 and (DEPLOY / "index" / "study.db").is_file(),
           "㉚ 关掉再启动：GET / → %d，study.db 仍在（第二次启动 %.1f 秒）"
           % (st, ms2 / 1000.0))
        app.stop()
    finally:
        app.stop()

    if not keep:
        log("      · 部署目录刻意保留（现场证据）：%s" % DEPLOY)


# ---------------------------------------------------------------- 模式四：出发布 zip
def mode_zip(version: str) -> None:
    """把 `release\\C-0073\\StudyDashboard\\` 打成发布 zip + `.sha256.txt`。

    结构照抄 v1.0：**zip 根就是 exe 与 `_internal/`**（不带 `StudyDashboard/`
    前缀），解压到新建文件夹即得到交付说明里画的那棵树。目录条目显式写入
    （与 v1.0 的 590 个条目口径一致），文件按路径排序，保证可复现。
    """
    out = ROOT / "release" / "C-0073" / ("StudyDashboard-v%s-win64.zip"
                                         % version.lstrip("vV"))
    log("\n" + "=" * 78)
    log("模式 --zip：产出 %s" % out)
    log("=" * 78)
    if not APP.is_dir():
        ck(False, "④-z 产物目录不存在：%s" % APP)
        return
    tmp = out.with_suffix(".zip.part")
    t0 = time.time()
    n_dir = n_file = total = 0
    items = sorted(APP.rglob("*"), key=lambda p: p.as_posix())
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for p in items:
            rel = p.relative_to(APP).as_posix()
            if p.is_dir():
                if any(p.iterdir()):
                    z.writestr(zipfile.ZipInfo(rel + "/"), b"")
                    n_dir += 1
                continue
            z.write(p, rel)
            n_file += 1
            total += p.stat().st_size
    dt = time.time() - t0
    if out.exists():
        out.unlink()
    tmp.rename(out)
    zb = out.stat().st_size
    sha = sha256_file(out)
    (out.parent / (out.name + ".sha256.txt")).write_text(
        "%s  %s\n" % (sha, out.name), encoding="utf-8")
    # 回读复核：条目数一致、4 份提示词取得到
    with zipfile.ZipFile(out) as z:
        n_back = len(z.namelist())
        bad = z.testzip()
    ck(n_back == n_dir + n_file and bad is None,
       "④-z 产出 %s：%d 条目（%d 文件 + %d 目录）/ 解压前 %d 文件 %.1f MB / "
       "zip %.1f MB / 压缩比 %.0f%% / 用时 %.1f 秒 / CRC 复核 %s"
       % (out.name, n_back, n_file, n_dir, n_file, total / 1e6, zb / 1e6,
          100.0 * zb / max(total, 1), dt, "通过" if bad is None else "★失败"))
    log("      SHA256 = %s" % sha)
    log("      校验值文件：%s" % (out.parent / (out.name + ".sha256.txt")))


# ---------------------------------------------------------------- 模式三：跑既有脚本
def run_existing(script: str, args: list[str], tag: str) -> None:
    cmd = [sys.executable, "-X", "utf8", str(HERE / script)] + args
    log("\n" + "-" * 78)
    log("调用 %s %s" % (script, " ".join(args)))
    t0 = time.time()
    r = subprocess.run(cmd, cwd=str(ROOT), capture_output=True)
    out = (r.stdout or b"").decode("utf-8", "replace")
    err = (r.stderr or b"").decode("utf-8", "replace")
    log(out)
    if err.strip():
        log("[stderr]\n" + err)
    head = [l for l in out.splitlines() if l.strip()][:6]
    for l in head:
        log("  | " + l)
    ck(r.returncode == 0, "%s %s 退出码 %d（用时 %.1f 秒）%s"
       % (tag, " ".join(args), r.returncode, time.time() - t0,
          "" if r.returncode == 0 else "；输出见日志"))


# ---------------------------------------------------------------- main
def main() -> int:
    global _logf
    _setup_output()
    ap = argparse.ArgumentParser(
        description="卡 C-0086 发布包验收（静态 / 异地真复制实测）")
    ap.add_argument("--package", action="store_true",
                    help="静态校验：包内提示词逐字一致 + 5 处语义 + exe 同级清单（秒级）")
    ap.add_argument("--exe", action="store_true",
                    help="实测：异地全新目录真复制后跑通全流程")
    ap.add_argument("--kp", action="store_true",
                    help="实测里再加一次「AI 知识点总结」真跑（★ 要密钥、几分钱）")
    ap.add_argument("--extras", action="store_true",
                    help="连 verify_c0073_exe.py 的 --audit 与 --quick 一起跑")
    ap.add_argument("--all", action="store_true",
                    help="= --package --exe --extras（--kp 另加）")
    ap.add_argument("--zip", dest="mkzip", action="store_true",
                    help="把产物目录打成发布 zip（StudyDashboard-v<版本>-win64.zip）")
    ap.add_argument("--zip-version", default="1.1",
                    help="发布 zip 的版本号，默认 1.1")
    ap.add_argument("--keep", action="store_true", help="保留部署目录（默认也保留）")
    args = ap.parse_args()

    if not any((args.package, args.exe, args.extras, args.all, args.mkzip)):
        args.package = True
    do_pkg = args.package or args.all
    do_exe = args.exe or args.all
    do_extras = args.extras or args.all

    LOGDIR.mkdir(parents=True, exist_ok=True)
    log_path = LOGDIR / ("verify-%s.log" % time.strftime("%Y%m%d-%H%M%S"))
    _logf = log_path.open("w", encoding="utf-8", errors="replace")
    modes = "+".join([m for m, on in (("package", do_pkg), ("exe", do_exe),
                                      ("extras", do_extras), ("kp", args.kp),
                                      ("zip", args.mkzip)) if on])
    log("卡 C-0086 发布包验收  模式=%s  时间=%s" % (modes, time.strftime("%F %T")))
    t_all = time.time()

    try:
        if do_pkg:
            mode_package()
        if args.mkzip:
            mode_zip(args.zip_version)
        if do_extras:
            run_existing("verify_c0073_exe.py", ["--audit"], "C0073-AUDIT")
            run_existing("verify_c0073_exe.py", ["--quick"], "C0073-QUICK")
        if do_exe:
            mode_exe(args.kp, args.keep)
    except SystemExit as e:
        ck(False, "流程中断：%s" % e)
    except Exception as e:                         # noqa: BLE001
        import traceback
        log(traceback.format_exc())
        ck(False, "未捕获异常：%s: %s" % (type(e).__name__, e))
    finally:
        pass

    bad = [m for ok, m in _checks if not ok]
    head = "C0086-RELEASE-FAIL" if bad else "C0086-RELEASE-PASS"
    log("\n" + "=" * 78)
    log("%s  模式=%s  项=%d 通过=%d 失败=%d  用时 %.1f 秒"
        % (head, modes, len(_checks), len(_checks) - len(bad), len(bad),
           time.time() - t_all))
    for ok, m in _checks:
        log("  [%s] %s" % ("OK" if ok else "NG", m))
    log("明细日志：%s" % log_path)
    if _logf:
        _logf.close()

    # stdout：第一行就是判定，紧接着全部结论行与失败项
    log_both("%s  模式=%s  项=%d 通过=%d 失败=%d  用时%.1f秒"
             % (head, modes, len(_checks), len(_checks) - len(bad), len(bad),
                time.time() - t_all))
    for ok, m in _checks[:4]:
        log_both("  [%s] %s" % ("OK" if ok else "NG", m))
    for m in bad:
        log_both("  ! [FAIL] %s" % m)
    log_both("  · 全部 %d 条明细见 %s" % (len(_checks), log_path))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
