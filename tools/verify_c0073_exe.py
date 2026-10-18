# Copyright (c) 2026 潜声海豚 <2227663973@qq.com>. All rights reserved.
# 本源码为著作权人私有作品（proprietary）：仅供经书面授权的测试使用；
# 未经著作权人许可，不得复制、分发、公开、发布或用于任何商业用途。
# Unauthorized copying, distribution or publication is strictly prohibited.
# File-ID: SA-26-c0073vf   (project study_agent; keep this line)
"""卡 C-0073 验收脚本：把打包好的 exe 部署到**全新目录**并实测。

## 为什么这样部署

要验证的命题是"**对方下载后双击就能用，数据落在 exe 同级**"。
所以不能就在 `_tmp_c0073/dist` 里跑（数据会落进构建产物里），
也不能靠 `KB_HOME` 指走（那就绕开了"exe 同级"这条要验的东西）。

做法：在 `%TEMP%\\sa_exe_test\\StudyDashboard\\` 下建一个**全新目录**，
把 `StudyDashboard.exe` **真复制**过去、`_internal` 用**目录 junction** 指回
构建目录。这样：

  · `sys.executable` 是那个新目录里的真实文件 → `config.ROOT` 就是它，
    可写数据全落在新目录里，构建产物一个字节都不会被写脏；
  · `sys._MEIPASS` = 新目录下的 `_internal`（junction），只读资源照常找到；
  · junction 跨卷可用、不需要管理员权限、创建是瞬时的（复制 300MB 才慢）。

## 模式

  --quick  启动 + `GET /` 200 + 可写目录落在 exe 同级 + 重启后仍在（默认）
  --full   再加功能实测：入库自造 PDF、检索带出处、纯图片 PDF 走 OCR（全部本地，¥0）
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DEFAULT_EXE = ROOT / "release" / "C-0073" / "StudyDashboard" / "StudyDashboard.exe"
DEPLOY_ROOT = Path(os.environ.get("TEMP") or r"C:\Windows\Temp") / "sa_exe_test"
DEPLOY = DEPLOY_ROOT / "StudyDashboard"

# 干净环境：只留跑程序必需的，**刻意抹掉一切密钥与项目相关环境变量**——
# 这样"能跑"才真的是程序自己能跑，而不是搭了本机 .env 的便车。
#
# ⚠ 全部写成大写：Windows 上 `os.environ` 的键**一律被规整成大写**
#   （`SystemRoot` 在字典里其实是 `SYSTEMROOT`）。写成首字母大写会一个都匹配
#   不上，子进程拿不到 `SystemRoot`，Winsock 初始化直接失败：
#   `OSError: [WinError 10106] 无法加载或初始化请求的服务提供程序`。
_KEEP_ENV = ("SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "PATHEXT",
             "TEMP", "TMP", "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE",
             "USERPROFILE", "HOMEDRIVE", "HOMEPATH", "LANG", "PATH")
# 本地零成本：关掉两个**要调大模型花钱**的开关（识图与 AI 结构分析）。
# OCR 与向量嵌入不受影响，它们本来就在本地跑。
_EXTRA_ENV = {"OUTLINE_ANALYSIS": "0", "FIGURE_DESC": "0", "PYTHONUTF8": "1"}


def _setup_output() -> None:
    """输出被**重定向/捕获**时用 UTF-8；交互终端保持原编码。

    理由：Windows 的 cmd 窗口是 GBK，强改 UTF-8 会把中文显示成乱码；
    但被重定向到文件、或被验收方捕获时，写 GBK 又会让日志没法按 UTF-8 读。
    两边都要照顾，所以按 isatty 分开处理。任何一种情况下都不许因为
    一个编不出来的字符把脚本崩掉（`log()` 还有一层兜底）。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            if stream.isatty():
                stream.reconfigure(errors="replace")
            else:
                stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:                          # noqa: BLE001
            pass


def log(msg: str) -> None:
    """打印一行。

    ⚠ 必须容错：验收方可能在 **GBK 代码页的 cmd** 里跑，而这里要回显的
      内容有一截来自 exe 的日志（UTF-8/GBK 混着读，解码失败会留下 U+FFFD）。
      GBK 编不出 U+FFFD，`print` 会直接抛 `UnicodeEncodeError` 把整个验收脚本
      崩掉退出码 1——实测踩过。所以编不动就退化成 `?`，绝不因为一行日志失败。
    """
    try:
        print(msg, flush=True)
    except UnicodeEncodeError:
        enc = sys.stdout.encoding or "utf-8"
        print(msg.encode(enc, "replace").decode(enc, "replace"), flush=True)


def free_port(start: int = 8791) -> int:
    for p in range(start, start + 60):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", p))
            except OSError:
                continue
        return p
    raise SystemExit("找不到空闲端口")


def clean_deploy() -> None:
    """清掉上一次的部署。

    ⚠ `_internal` 是 junction：**必须先摘掉它**再删目录。Windows 上
    `shutil.rmtree` 认不出 junction（`os.path.islink` 对它返回 False），
    会一头扎进去把**构建目录里的真东西删光**。`os.rmdir` 只摘联接本身。
    """
    link = DEPLOY / "_internal"
    if link.exists():
        try:
            os.rmdir(link)
        except OSError:
            subprocess.run(["cmd", "/c", "rmdir", str(link)],
                           capture_output=True)
    if DEPLOY.exists():
        shutil.rmtree(DEPLOY, ignore_errors=True)


def deploy(exe: Path) -> str:
    app_dir = exe.parent
    if not exe.exists():
        raise SystemExit("找不到可执行文件：%s" % exe)
    clean_deploy()
    DEPLOY.mkdir(parents=True, exist_ok=True)
    shutil.copy2(exe, DEPLOY / exe.name)
    src_internal = app_dir / "_internal"
    if src_internal.is_dir():
        r = subprocess.run(["cmd", "/c", "mklink", "/J",
                            str(DEPLOY / "_internal"), str(src_internal)],
                           capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        if r.returncode != 0:
            # 退路：老老实实整棵复制（慢，但一定能成）
            shutil.copytree(src_internal, DEPLOY / "_internal")
            return "完整复制（junction 不可用：%s）" % (r.stdout or r.stderr).strip()
        return "exe 复制 + _internal junction → %s" % src_internal
    return "exe 复制（无 _internal，疑似 onefile）"


def child_env(with_key: bool = False) -> dict:
    env = {k: v for k, v in os.environ.items() if k.upper() in _KEEP_ENV}
    env.update(_EXTRA_ENV)
    if with_key:
        # `--kp` 要先跑一次真识图（见 main 里的原因），所以把识图开关放回 1。
        # 结构分析仍关着（`OUTLINE_ANALYSIS=0`），少花一次调用的钱。
        env["FIGURE_DESC"] = "1"
        k = local_api_key()
        if k:
            env["DEEPSEEK_API_KEY"] = k
    return env


def local_api_key() -> str:
    """取一把可用于 `--kp` 真调用验证的密钥。

    来源顺序照抄 `core/chat.py::load_api_key`（环境变量 → DSH 凭据文件），
    中间多认一处项目 `.env`（留给人自己配的时候用）。
    ⚠ 密钥只进子进程环境，**不打印、不落盘**。
    """
    k = os.environ.get("DEEPSEEK_API_KEY", "")
    if k.startswith("sk-"):
        return k.strip()
    p = ROOT / ".env"
    if p.is_file():
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            s = line.strip()
            if s.startswith("#") or "=" not in s:
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


def http_get(url: str, timeout: float = 10.0):
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read()


def http_post(url: str, payload: dict, timeout: float = 1800.0):
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        # 把服务端的错误体原样带回来，别让它变成一个光秃秃的 "500"
        raw = e.read() or b""
        try:
            return e.code, json.loads(raw or b"{}")
        except ValueError:
            return e.code, {"error": raw.decode("utf-8", "replace")[:500]}


class App:
    def __init__(self, exe: Path, port: int, log_path: Path,
                 with_key: bool = False) -> None:
        self.exe, self.port, self.log_path = exe, port, log_path
        self.with_key = with_key
        self.proc: subprocess.Popen | None = None
        self.fh = None

    @property
    def base(self) -> str:
        return "http://127.0.0.1:%d" % self.port

    def start(self, wait: float = 90.0) -> int:
        self.fh = self.log_path.open("a", encoding="utf-8", errors="replace")
        self.fh.write("\n===== 启动 %s --port %d =====\n"
                      % (time.strftime("%H:%M:%S"), self.port))
        self.fh.flush()
        self.proc = subprocess.Popen(
            [str(self.exe), "--port", str(self.port), "--no-open"],
            cwd=str(DEPLOY), env=child_env(self.with_key),
            stdout=self.fh, stderr=subprocess.STDOUT)
        t0 = time.time()
        last = ""
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
TEXT_TOKEN = "C0073TEXTTEST"
OCR_TOKEN = "C0073OCRTOKEN"


def make_text_pdf(path: Path) -> None:
    """一份**有文字层**的两页小 PDF（自己造，不含任何真实资料）。

    刻意写得有点结构（定义 / 场所 / 原料产物 / 两个阶段 / 影响因素）：
    这样「AI 知识点总结」才有东西可总结，能验出"产物里真有知识点"，
    而不是只验到"管线跑完没报错"。
    """
    import fitz
    doc = fitz.open()
    pages = [
        [
            "C0073 packaging self-check sample (not user data).",
            "",
            "一、定义",
            "光合作用是绿色植物利用光能，把二氧化碳和水合成有机物，",
            "并释放氧气的过程。",
            "",
            "二、场所与条件",
            "叶绿体是光合作用的场所，叶绿素负责吸收光能。",
            "条件包括光、叶绿体与适宜的温度。",
            "",
            "三、原料与产物",
            "原料：二氧化碳和水。产物：有机物（葡萄糖）和氧气。",
            "",
            "Unique marker for retrieval assertion: " + TEXT_TOKEN,
        ],
        [
            "四、两个阶段",
            "1. 光反应：在类囊体薄膜上进行，光解水产生氧气与 NADPH，",
            "   同时合成 ATP。",
            "2. 暗反应（卡尔文循环）：在叶绿体基质中进行，固定二氧化碳，",
            "   最终生成三碳糖。",
            "",
            "五、影响因素",
            "光照强度、二氧化碳浓度、温度、水分与矿质元素都会影响速率。",
            "",
            "六、意义",
            "制造有机物、释放氧气、维持生物圈的碳氧平衡。",
        ],
    ]
    for lines in pages:
        page = doc.new_page()
        y = 90.0
        for ln in lines:
            # ⚠ 字体要按内容挑：`china-s` 这种 CJK 字体里 ASCII 是**全角宽**，
            # 一行 50 个英文字符就会顶出页面右边界、被 PDF 裁掉尾巴——
            # 实测踩过：`...assertion: C0073TEXTTEST` 被裁成 `...: C007`，
            # 于是检索断言假失败。纯 ASCII 行走内置 `helv`（半角宽）就没这问题。
            ascii_only = all(ord(ch) < 128 for ch in ln)
            page.insert_text((60, y), ln, fontsize=12.5,
                             fontname="helv" if ascii_only else "china-s")
            y += 24
    doc.save(str(path))
    doc.close()


def make_image_pdf(path: Path) -> None:
    """一份**没有文字层**的图片 PDF：只能靠 OCR 拿到字。"""
    from PIL import Image, ImageDraw, ImageFont
    img = Image.new("RGB", (1200, 400), "white")
    d = ImageDraw.Draw(img)
    font = None
    for name in ("msyh.ttc", "simhei.ttf", "simsun.ttc", "arial.ttf"):
        try:
            font = ImageFont.truetype("C:/Windows/Fonts/" + name, 64)
            break
        except OSError:
            continue
    if font is None:
        raise SystemExit("找不到可用字体，无法造图片样张")
    d.text((60, 70), "光合作用 自检样张", fill="black", font=font)
    d.text((60, 190), OCR_TOKEN, fill="black", font=font)
    img.save(str(path), "PDF", resolution=200.0)


# ---------------------------------------------------------------- 主流程
def audit_only(app_dir: Path) -> int:
    """只审计产物（不启动进程）：隐私 / 密钥 / 本项目源码明文。

    复用 `tools/pack_exe.py` 里**打包时同一套**门禁规则，避免"打包用一套、
    验收用另一套"的自欺。
    """
    sys.path.insert(0, str(HERE))
    import pack_exe  # noqa: PLC0415

    if not app_dir.is_dir():
        print("C0073-AUDIT-FAIL 产物目录不存在：%s" % app_dir)
        return 1
    probs = pack_exe.audit_tree(app_dir, what="dist")
    py_all = [p for p in app_dir.rglob("*.py")]
    ours = [p for p in py_all
            if pack_exe._is_our_source(p.relative_to(app_dir).as_posix())]
    pyc = [p for p in app_dir.rglob("*.pyc")]
    n, size, mb = pack_exe.measure(app_dir)

    log("=" * 72)
    log("%s 产物审计 %s"
        % ("C0073-AUDIT-FAIL" if (probs or ours) else "C0073-AUDIT-PASS", app_dir))
    log("  [%s] 禁文件/禁后缀/禁目录/密钥特征/内部痕迹：%d 处违规"
        % ("OK" if not probs else "NG", len(probs)))
    log("  [%s] 本项目源码明文（core/、tools/ 下的 .py）：%d 个"
        % ("OK" if not ours else "NG", len(ours)))
    log("  [%s] 第三方库自带 .py（开源库自己的源码，非本项目）：%d 个；"
        "独立 .pyc：%d 个（PYZ 归档在 exe 内）"
        % ("OK", len(py_all) - len(ours), len(pyc)))
    log("  [OK] 体积 %.1f MB / %d 个文件" % (mb, n))

    # 逐条点名"红线清单"，让人一眼看到这些名字**一个都没出现**
    bad_hits: list[str] = []
    for pat in ("**/.env", "**/.env.*", "**/config.local.json", "**/config.local.json.tmp",
                "**/*.db", "**/*.db-wal", "**/*.db-shm", "**/*.sqlite",
                "**/*.sqlite3", "**/.credentials.yaml", "**/credentials.yaml"):
        bad_hits += [p.relative_to(app_dir).as_posix() for p in app_dir.glob(pat)]
    for d in ("subjects", "raw", "cards", "index", ".git", "venv"):
        bad_hits += [p.relative_to(app_dir).as_posix() + "/"
                     for p in app_dir.rglob(d) if p.is_dir()]
    # 包内 logs/ 只允许是提示词真源（_internal/logs/knowledge/*.md）
    stray_logs: list[str] = []
    for p in app_dir.rglob("logs"):
        if not p.is_dir():
            continue
        for f in p.rglob("*"):
            if f.is_file() and f.suffix.lower() not in (".md",):
                stray_logs.append(f.relative_to(app_dir).as_posix())
    log("  [%s] 红线逐条点名：.env / config.local.json / *.db / subjects/ / raw/ / "
        "index/ / cards/ / .git = %d 处%s"
        % ("OK" if not bad_hits else "NG", len(bad_hits),
           ("（" + "、".join(bad_hits[:5]) + "）") if bad_hits else ""))
    log("  [%s] 包内 logs/ 下非 .md 文件（应为 0：只允许提示词真源）：%d 处"
        % ("OK" if not stray_logs else "NG", len(stray_logs)))
    log("=" * 72)
    for x in probs[:30]:
        log("  ! " + x)
    for x in ours[:10]:
        log("  ! 源码明文：" + x.relative_to(app_dir).as_posix())
    # 关键目录清点
    inner_logs = sorted(p.relative_to(app_dir).as_posix()
                        for p in (app_dir / "_internal" / "logs").rglob("*")
                        if p.is_file()) if (app_dir / "_internal" / "logs").is_dir() else []
    log("  包内 _internal/logs/ 只有提示词真源：%s" % inner_logs)
    models = sorted(p.name for p in (app_dir / "_internal" / "models").rglob("*")
                    if p.is_file()) if (app_dir / "_internal" / "models").is_dir() else []
    log("  包内 _internal/models/：%s" % models)
    tools = sorted(p.name for p in (app_dir / "_internal" / "tools").rglob("*")
                   if p.is_file()) if (app_dir / "_internal" / "tools").is_dir() else []
    log("  包内 _internal/tools/：%s" % tools)
    return 1 if (probs or ours or bad_hits or stray_logs) else 0


def doc_total(app_base: str) -> int:
    """`/api/stats` 里的文档数（结构见 core/study.py::dashboard）。"""
    try:
        _, body = http_get(app_base + "/api/stats")
        return int((json.loads(body or b"{}").get("documents") or {}).get("total") or 0)
    except Exception:                              # noqa: BLE001
        return -1


def src_regression() -> int:
    """源码模式回归：在项目根按**原方式**启动看板，确认没被打包改动破坏。

    用临时端口（**绝不碰 8756 那个常驻实例**），起来后查四个只读接口，
    再确认用户真实数据（文档数）还在。
    """
    port = free_port(8801)
    log_path = DEPLOY_ROOT.parent / "sa_src_test.log"
    DEPLOY_ROOT.parent.mkdir(parents=True, exist_ok=True)
    fh = log_path.open("w", encoding="utf-8", errors="replace")
    proc = subprocess.Popen(
        [sys.executable, "-X", "utf8", str(ROOT / "tools" / "dashboard.py"),
         "--port", str(port), "--no-open"],
        cwd=str(ROOT), stdout=fh, stderr=subprocess.STDOUT)
    base = "http://127.0.0.1:%d" % port
    checks: list[tuple[bool, str]] = []
    try:
        t0 = time.time()
        while time.time() - t0 < 90:
            if proc.poll() is not None:
                raise SystemExit("源码模式启动即退出（%s），日志 %s"
                                 % (proc.returncode, log_path))
            try:
                st, body = http_get(base + "/", timeout=3.0)
                if st == 200:
                    checks.append((True, "源码模式 GET / → HTTP %d（%d 字节，启动 %.1f 秒）"
                                   % (st, len(body), time.time() - t0)))
                    break
            except Exception:                       # noqa: BLE001
                pass
            time.sleep(0.4)
        else:
            checks.append((False, "源码模式启动超时"))
        n = doc_total(base)
        checks.append((n >= 0, "源码模式 /api/stats 可用：文档数 %d（=%s 的真实数据）"
                       % (n, ROOT)))
        try:
            _, d = http_get(base + "/api/docs")
            checks.append((True, "源码模式 /api/docs 可用：%d 份资料"
                           % len(json.loads(d or b"{}").get("documents") or [])))
        except Exception as e:                      # noqa: BLE001
            checks.append((False, "源码模式 /api/docs 失败：%s" % e))
        try:
            _, s = http_get(base + "/api/settings")
            slots = json.loads(s or b"{}").get("prompts") or []
            checks.append((len(slots) == 3 and all(x.get("ok") for x in slots),
                           "源码模式提示词真源仍读得到：%s"
                           % "、".join("%s=%d" % (x.get("id"), x.get("chars") or 0)
                                       for x in slots)))
        except Exception as e:                      # noqa: BLE001
            checks.append((False, "源码模式 /api/settings 失败：%s" % e))
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                proc.kill()
        fh.close()

    bad = [m for ok, m in checks if not ok]
    log("%s 源码模式回归（端口 %d，未触碰 8756）"
        % ("C0073-SRC-FAIL" if bad else "C0073-SRC-PASS", port))
    for ok, m in checks:
        log("  [%s] %s" % ("OK" if ok else "NG", m))
    log("  日志：%s" % log_path)
    return 1 if bad else 0


def main() -> int:
    _setup_output()
    ap = argparse.ArgumentParser(description="卡 C-0073 exe 验收")
    ap.add_argument("--exe", default=str(DEFAULT_EXE))
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--quick", action="store_true", help="只做启动与路径验证")
    ap.add_argument("--full", action="store_true", help="加上入库/检索/OCR 实测")
    ap.add_argument("--kp", action="store_true",
                    help="再加上「AI 知识点总结」真跑一次（★ 要密钥、会花几分钱）")
    ap.add_argument("--src", action="store_true",
                    help="源码模式回归：在项目根按原方式启动看板（临时端口）")
    ap.add_argument("--audit", action="store_true",
                    help="只审计产物：无用户数据 / 无密钥 / 无本项目源码明文")
    args = ap.parse_args()

    if args.src:
        return src_regression()
    if args.audit:
        return audit_only(Path(args.exe).parent)

    exe = Path(args.exe)
    port = args.port or free_port()
    full = bool(args.full or args.kp) and not args.quick
    DEPLOY_ROOT.mkdir(parents=True, exist_ok=True)
    log_path = DEPLOY_ROOT / "run.log"
    if log_path.exists():
        log_path.unlink()
    checks: list[tuple[bool, str]] = []

    def ck(ok: bool, msg: str) -> None:
        checks.append((bool(ok), msg))

    how = deploy(exe)
    # ★ 必须用**部署后**的那个 exe：直接拿 dist 里的原路径去跑，`sys.executable`
    #   就还是 dist，数据会写进构建产物里——那正是这一步要验的东西。
    app = App(DEPLOY / exe.name, port, log_path, with_key=bool(args.kp))
    try:
        ms = app.start()
        st, body = http_get(app.base + "/")
        ck(st == 200, "GET / → HTTP %d（%d 字节，启动 %.1f 秒）" % (st, len(body), ms / 1000))

        # 可写目录必须落在 **exe 同级**
        want = ["index", "logs", "notes", "raw", "logs/knowledge"]
        missing = [d for d in want if not (DEPLOY / d).is_dir()]
        ck(not missing, "exe 同级自动生成可写目录：%s%s"
           % ("、".join(want), ("；缺 " + "、".join(missing)) if missing else ""))
        db = DEPLOY / "index" / "study.db"
        ck(db.is_file(), "index/study.db 已建：%s（%d 字节）"
           % (db, db.stat().st_size if db.is_file() else -1))

        # 提示词种子是否种下（打包模式下才做）
        seeded = sorted(p.name for p in (DEPLOY / "logs" / "knowledge").glob("*.md"))
        need = ("提示词-知识点总结.md", "提示词-知识点总结-v2.md",
                "提示词-思维导图结构.md", "提示词-名词解释.md", "README.md")
        lack = [n for n in need if n not in seeded]
        ck(not lack, "提示词真源已种到 exe 同级：%d 个%s"
           % (len(seeded), ("；缺 " + "、".join(lack)) if lack else ""))

        # 提示词**能不能被读到**（不是"文件在不在"）——设置窗口那条只读接口
        # 会把三个真源槽位的 path/sha256/chars 报回来，读不到就是 ok=false。
        _, sbody = http_get(app.base + "/api/settings")
        settings = json.loads(sbody or b"{}")
        slots = settings.get("prompts") or []
        bad = [s.get("id") for s in slots if not s.get("ok")]
        ck(len(slots) == 3 and not bad,
           "提示词真源可读：%s"
           % ("、".join("%s=%d字符" % (s.get("id"), s.get("chars") or 0)
                        for s in slots) + ("；读不到 " + str(bad) if bad else "")))

        n_docs0 = doc_total(app.base)
        ck(n_docs0 == 0, "空程序：启动时 /api/stats 文档数 = %d（应为 0，不带任何用户数据）"
           % n_docs0)

        # ---------------- 功能实测 ----------------
        if full:
            raw = DEPLOY / "raw"
            raw.mkdir(parents=True, exist_ok=True)
            tpdf = raw / "c0073_text.pdf"
            ipdf = raw / "c0073_image.pdf"
            make_text_pdf(tpdf)
            make_image_pdf(ipdf)

            st, r1 = http_post(app.base + "/api/ingest",
                               {"paths": ["c0073_text.pdf"], "no_figures": True})
            n1 = int(r1.get("chunks") or 0)
            ck(st == 200 and n1 > 0,
               "① 入库有文字层 PDF：chunks=%d（summary=%s）"
               % (n1, str(r1.get("summary", ""))[:70].replace("\n", " ")))

            st, s1 = http_get(app.base + "/api/search?q=" + TEXT_TOKEN)
            hits = json.loads(s1 or b"{}").get("hits") or []
            cites = [h.get("cite") for h in hits if h.get("cite")]
            ck(bool(hits) and bool(cites),
               "② 检索 %s：命中 %d 条，带出处 %d 条（首条 cite=%s）"
               % (TEXT_TOKEN, len(hits), len(cites),
                  cites[0] if cites else "-"))

            st, r2 = http_post(app.base + "/api/ingest",
                               {"paths": ["c0073_image.pdf"], "no_figures": True})
            n2 = int(r2.get("chunks") or 0)
            st, s2 = http_get(app.base + "/api/search?q=" + OCR_TOKEN)
            hits2 = json.loads(s2 or b"{}").get("hits") or []
            ocr_ok = bool(hits2) and any(
                OCR_TOKEN.lower() in (h.get("text") or "").lower().replace(" ", "")
                for h in hits2)
            ck(n2 > 0 and ocr_ok,
               "③ 纯图片 PDF 走 OCR：chunks=%d，检索 %s 命中 %d 条且正文含暗号=%s"
               % (n2, OCR_TOKEN, len(hits2), ocr_ok))

            # 向量嵌入：入库能出 chunks 就说明 embed_documents 真跑通了；
            # 这里再单独确认"向量路"确实参与（hits 带非零 vec_score）
            vs = [h.get("vec_score") for h in (hits + hits2) if h.get("vec_score")]
            ck(bool(vs) and max(vs) > 0.3,
               "③b 向量嵌入可用：命中结果的 vec_score = %s" % (vs[:4],))

        # ---------------- 「AI 知识点总结」真跑一次（★ 要密钥、几分钱）----------
        if args.kp:
            # ★ 为什么先补一次**真识图**：
            #   `figure_desc` 表**不在 `db.init_db` 的建表清单里**，全项目只有
            #   `core/figures.py::ensure_schema` 会建它，而那只在识图路径上被调用。
            #   于是**全新的空库**（正是本卡交付物的目标场景）里没有这张表，
            #   `core/kp_build.py` 直接 `FROM figure_desc` 就抛
            #   `OperationalError: no such table: figure_desc`。
            #   这是**既有产品缺陷、与打包无关**（源码模式建空库同样复现，
            #   实测证据见交付说明）。本卡不修它（不扩大范围），
            #   但验证必须能照实走通，所以这里先按正常使用路径把表建出来。
            st, r0 = http_post(app.base + "/api/ingest",
                               {"paths": ["c0073_text.pdf"], "force": True,
                                "no_figures": False})
            ck(not r0.get("error"),
               "④-pre 带识图入库（建 figure_desc 表，★ 真调了一次视觉模型）：%s"
               % json.dumps(r0, ensure_ascii=False)[:140])

            _, dbody = http_get(app.base + "/api/docs")
            docs = json.loads(dbody or b"{}").get("documents") or []
            did = int(docs[0]["id"]) if docs else 0
            if did <= 0:
                ck(False, "④ AI 知识点总结：库里没有可整理的文档")
            else:
                out = DEPLOY / "logs" / "knowledge" / ("%d-末级知识点总结.json" % did)
                st, r = http_post(app.base + "/api/kp-build",
                                  {"doc_id": did, "max_cost": 0.1})
                # ⚠ 判"跑完了"不能看产物文件大小稳定：管线是**分阶段落盘**的，
                #   写完结构就先落一次盘，此时 `kp_done` 还是 0。上一次就是被这个
                #   间隙骗了，误判成"知识点为 0 条"。改看进度接口的 active。
                t0, seen, last_pr = time.time(), False, {}
                while time.time() - t0 < 900:
                    try:
                        _, pb = http_get(app.base + "/api/kp-build/progress")
                        pr = json.loads(pb or b"{}")
                    except Exception:                  # noqa: BLE001
                        pr = {}
                    last_pr = pr or last_pr
                    if pr.get("active"):
                        seen = True
                    elif seen:
                        break
                    time.sleep(3)
                n_kp, sha_ok = 0, False
                if out.is_file():
                    try:
                        d = json.loads(out.read_text(encoding="utf-8"))
                        meta = d.get("meta") or {}
                        n_kp = len(d.get("knowledge_points") or [])
                        sha_ok = bool(meta.get("prompt_sha256"))
                    except Exception:                  # noqa: BLE001
                        pass
                ck(out.is_file() and out.stat().st_size > 200 and n_kp > 0,
                   "④ AI 知识点总结（提示词真源真被读进来用）：POST /api/kp-build → %s；"
                   "产物 %s（%d 字节，知识点 %d 条，进度 kp_done=%s/kp_total=%s）"
                   % (json.dumps(r, ensure_ascii=False)[:70], out.name,
                      out.stat().st_size if out.is_file() else -1, n_kp,
                      last_pr.get("kp_done"), last_pr.get("kp_total")))
                # 把产物留一份档：它是"这条管线真跑通了"最硬的证据，
                # 而部署目录每次验证都会被清空重建。
                if out.is_file():
                    keep = (ROOT / "_tmp_c0073" / "logs"
                            / ("kp-artifact-%s.json" % time.strftime("%H%M%S")))
                    keep.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(out, keep)
                    ck(True, "④-evidence 知识点产物已归档：%s（%d 字节）"
                       % (keep, keep.stat().st_size))

        app.stop()

        # ---------------- 持久性 ----------------
        ms2 = app.start()
        st, _ = http_get(app.base + "/")
        db2 = DEPLOY / "index" / "study.db"
        ck(st == 200 and db2.is_file(),
           "关掉再启动：GET / → %d，study.db 仍在（%d 字节，第二次启动 %.1f 秒）"
           % (st, db2.stat().st_size, ms2 / 1000))
        if full:
            stats1 = doc_total(app.base)
            ck(stats1 > 0,
               "持久性：重启后文档数 %d（首次启动时 %d）" % (stats1, n_docs0))
        app.stop()
    finally:
        app.stop()

    bad = [m for ok, m in checks if not ok]
    head = "C0073-EXE-FAIL" if bad else "C0073-EXE-PASS"
    log("=" * 72)
    log("%s  部署目录=%s  端口=%d  模式=%s"
        % (head, DEPLOY, port, "full" if full else "quick"))
    for ok, m in checks[:4]:
        log("  [%s] %s" % ("OK" if ok else "NG", m))
    log("  %s" % how)
    log("  %d/%d 项通过；明细见 %s" % (len(checks) - len(bad), len(checks), log_path))
    log("=" * 72)
    for ok, m in checks:
        log("  [%s] %s" % ("OK" if ok else "NG", m))
    for m in bad:
        log("  ! 未通过：%s" % m)
    log("部署目录内的一级条目：%s"
        % sorted(p.name for p in DEPLOY.iterdir()))
    log("run.log 末尾 20 行：")
    try:
        tail = log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-20:]
        for ln in tail:
            log("    | " + ln)
    except OSError:
        pass
    # 刻意**不删**部署目录：它是"可写数据落在 exe 同级"的现场证据，
    # 主会话/大管家随时可以自己去 %TEMP%\sa_exe_test\StudyDashboard 复核。
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
