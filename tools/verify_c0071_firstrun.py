# -*- coding: utf-8 -*-
"""卡 C-0071 交付自检：首次运行体验（空库引导 + 无密钥引导 + 公开版 README）。

跑法（cmd 与 pwsh 都可以）：

    G:\\study_agent\\venv\\Scripts\\python.exe -X utf8 G:\\study_agent\\tools\\verify_c0071_firstrun.py

它回答三个问题，全部**只读**且**不碰用户真实库**：

  一、静态：源码里该有的在不在
      · `tools/dashboard.py` 有 `/api/firstrun` 接口、`renderFirstrun()` 与
        两条横幅（`#fr-nokey` / `#fr-empty`）；
      · `core/chat.py`、`core/figures.py` 里不再有写死作者用户名的凭据路径。

  二、动态：把 `core/` 与 `tools/dashboard.py` 拷进一个**临时空目录**，在那里起
      一个实例（端口动态、USERPROFILE 隔离），断言
      · `/api/firstrun` 说 `empty=true / docs=0 / credential.ok=false`；
      · 首页 HTML 真的带那两条横幅的容器与文案；
      · `/api/stats`、`/api/docs` 这类**不依赖模型**的接口照常可用。
      ⚠ 为什么必须用副本、而不是在项目根上设 `KB_*` 环境变量：学科注册表
      （`core/subjects.py::registry_path`）取的是**项目根**下的 `logs/subjects.json`，
      不看 `KB_LOG`；在项目根起实例会被 `subjects.restore()` 切到用户真实学科库上，
      于是"空库"根本测不出来，还会把真实课件名读进日志。

  三、文档卫生：`README.md` 含要求的章节，且**不含**任何真实用户数据 ——
      包括「拿用户真实库里的文档标题逐个反查 README」这一条（库在才查）。

输出约定：**stdout 前 6 行是结论**，逐条明细写日志文件
（`卡目录/verify_c0071.log`，卡目录不可写时退到临时目录）。
"""
from __future__ import annotations

import json
import os
import re
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DASH = ROOT / "tools" / "dashboard.py"
README = ROOT / "README.md"
CHAT = ROOT / "core" / "chat.py"
FIGURES = ROOT / "core" / "figures.py"
# 明细日志落哪儿：设了 C0071_CARD 就写那儿（交付时指向卡目录），否则写临时目录。
# 刻意**不写死任何用户目录** —— 这个脚本是要跟着程序分发的，路径不该带作者痕迹。
CARD = Path(os.environ["C0071_CARD"]) if os.environ.get("C0071_CARD") else None

_lines: list[str] = []
_ok = 0
_bad = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global _ok, _bad
    if cond:
        _ok += 1
        _lines.append(f"[OK  ] {name}" + (f" — {detail}" if detail else ""))
    else:
        _bad += 1
        _lines.append(f"[FAIL] {name}" + (f" — {detail}" if detail else ""))


def log_path() -> Path:
    cands = [d for d in (CARD, Path(tempfile.gettempdir())) if d is not None]
    for d in cands:
        try:
            d.mkdir(parents=True, exist_ok=True)
            p = d / "verify_c0071.log"
            p.write_text("", encoding="utf-8")
            return p
        except OSError:
            continue
    return Path(tempfile.gettempdir()) / "verify_c0071.log"


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def get(url: str, timeout: float = 5.0) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:                                   # noqa: BLE001
        return 0, f"{type(e).__name__}: {e}"


# ---------------------------------------------------------------- 一、静态
def static_checks() -> None:
    dash = DASH.read_text(encoding="utf-8")
    check("dashboard.py 有 /api/firstrun 路由", '"/api/firstrun"' in dash)
    check("dashboard.py 有 api_firstrun()", "def api_firstrun(" in dash)
    check("dashboard.py 有 renderFirstrun()", "function renderFirstrun()" in dash)
    check("首页有引导容器 #firstrun", 'id="firstrun"' in dash)
    check("无密钥横幅 #fr-nokey", 'id="fr-nokey"' in dash)
    check("空库三步横幅 #fr-empty", 'id="fr-empty"' in dash)
    check("横幅文案指向右上角设置", "openSettings()" in dash and "DEEPSEEK_API_KEY" in dash)
    check("横幅明说离线功能照常可用", "照常可用" in dash)

    chat = CHAT.read_text(encoding="utf-8")
    figs = FIGURES.read_text(encoding="utf-8")
    hard = re.compile(r"C:\\\\Users\\\\[A-Za-z]")
    check("core/chat.py 无写死的作者凭据路径", not hard.search(chat))
    check("core/figures.py 无写死的作者凭据路径", not hard.search(figs))
    check("core/figures.py 仍能从环境变量取密钥",
          'os.environ.get("DEEPSEEK_API_KEY")' in figs)


# ---------------------------------------------------------------- 二、动态
def dynamic_checks(tmp: Path) -> None:
    """在**临时空副本**里验证首次启动 —— 这是本卡最核心的一条。"""
    import shutil

    app = tmp / "app"
    (app / "tools").mkdir(parents=True, exist_ok=True)
    shutil.copytree(ROOT / "core", app / "core")
    shutil.copy2(DASH, app / "tools" / "dashboard.py")
    # 只拷运行必需的两样：模型不拷（这条链路不加载嵌入模型，也就省掉 114MB），
    # 依赖用当前解释器（venv），副本本身保持"空程序"形态。
    dash_app = app / "tools" / "dashboard.py"

    port = free_port()
    env = dict(os.environ)
    env.update({
        "PYTHONIOENCODING": "utf-8",
        # 隔离家目录：让"没有配置任何密钥"这一状态可以被复现
        "USERPROFILE": str(tmp / "home"),
        "HOMEDRIVE": str(tmp.drive or "C:"), "HOMEPATH": str(tmp)[2:],
    })
    env.pop("DEEPSEEK_API_KEY", None)
    (tmp / "home").mkdir(parents=True, exist_ok=True)

    out = (tmp / "dash_stdout.log").open("w", encoding="utf-8")
    err = (tmp / "dash_stderr.log").open("w", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, "-X", "utf8", str(dash_app), "--port", str(port), "--no-open"],
        cwd=str(app), env=env, stdout=out, stderr=err)
    base = f"http://127.0.0.1:{port}"
    try:
        fr = None
        for _ in range(80):                                   # 最多 20 秒
            code, body = get(base + "/api/firstrun")
            if code == 200:
                try:
                    fr = json.loads(body)
                    break
                except json.JSONDecodeError:
                    pass
            if proc.poll() is not None:
                break
            time.sleep(0.25)

        check("临时空副本里实例起来了（/api/firstrun 200）", fr is not None,
              f"端口 {port}，副本 {app}")
        if fr:
            check("空库：docs=0 且 empty=true",
                  fr.get("docs") == 0 and fr.get("empty") is True,
                  f"docs={fr.get('docs')} empty={fr.get('empty')}")
            cred = fr.get("credential") or {}
            check("无凭据：credential.ok=false（隔离 HOME 生效）", cred.get("ok") is False)
            check("接口回传的路径指向副本、不是用户库",
                  str(app) in str(fr.get("db_path", ""))
                  and str(app) in str(fr.get("raw_dir", "")))
        check("首启自动建库建表（index/study.db 出现在副本里）",
              (app / "index" / "study.db").exists())
        check("首启自动建目录（raw/ notes/ logs/ models/）",
              all((app / d).is_dir() for d in ("raw", "notes", "logs", "models")))

        code, html = get(base + "/")
        check("首页 200", code == 200, f"{len(html)} 字节")
        check("首页带引导容器与两条横幅的挂点",
              'id="firstrun"' in html and "fr-nokey" in html and "fr-empty" in html)
        check("首页含无密钥文案（不可用/照常可用 都写明）",
              "还没有配置大模型密钥" in html and "照常可用" in html)
        check("首页含空库三步引导文案", "先做这三步" in html)

        code, body = get(base + "/api/stats")
        check("非模型功能可用：/api/stats 200", code == 200, body[:80])
        code, body = get(base + "/api/docs")
        check("非模型功能可用：/api/docs 200", code == 200, body[:80])
        code, body = get(base + "/api/settings")
        check("非模型功能可用：/api/settings 200", code == 200, body[:80])
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=8)
        except Exception:                                     # noqa: BLE001
            try:
                proc.kill()
            except Exception:                                 # noqa: BLE001
                pass
        out.close()
        err.close()


# ---------------------------------------------------------------- 三、文档
REQUIRED_SECTIONS = [
    "## 这是什么", "## 环境要求", "## 三步上手", "## 支持的文件格式",
    "## 已知限制", "## 常见问题", "## 许可声明",
]
FORBIDDEN_PATTERNS = [
    (r"sk-[A-Za-z0-9]{16,}", "疑似真实 API 密钥"),
    (r"C:\\Users\\", "作者的家目录路径"),
    (r"G:\\study_agent", "作者的盘符路径"),
    (r"8,?069", "真实库规模数字（切块数）"),
    (r"1,?580", "真实库规模数字（识图页数）"),
    (r"182\.3", "真实库规模数字（字符数）"),
]


def doc_checks() -> None:
    text = README.read_text(encoding="utf-8")
    for sec in REQUIRED_SECTIONS:
        check(f"README 含章节 {sec}", sec in text)
    check("README 写明 Windows + Python 版本要求",
          "Windows 10 / 11" in text and "3.12" in text)
    check("README 写明 Office 依赖限制", "PowerPoint" in text and "Microsoft Office" in text)
    check("README 写明联网与费用自付",
          "需要联网" in text and "费用由你自付" in text or "费用由使用者自付" in text)
    check("README 头部 File-ID 行保留",
          "File-ID: SA-22-35971877" in text)

    for pat, why in FORBIDDEN_PATTERNS:
        m = re.search(pat, text)
        check(f"README 不含{why}", m is None, m.group(0) if m else "")

    # 拿用户真实库里的文档标题反查 README（真实数据泄漏的最强检查）。
    db = ROOT / "index" / "study.db"
    if db.exists():
        try:
            conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
            titles = [r[0] for r in conn.execute(
                "SELECT title FROM documents WHERE title IS NOT NULL").fetchall()]
            conn.close()
            leaked = [t for t in titles if t and len(t) >= 6 and t in text]
            check(f"README 不含任何真实课件名（反查 {len(titles)} 个标题）",
                  not leaked, "; ".join(leaked[:3]))
        except Exception as e:                                # noqa: BLE001
            check("README 真实课件名反查", False, f"{type(e).__name__}: {e}")
    else:
        _lines.append("[SKIP] 用户真实库不存在，跳过课件名反查")


def main() -> int:
    t0 = time.time()
    static_checks()
    with tempfile.TemporaryDirectory(prefix="sa_c0071_verify_") as td:
        dynamic_checks(Path(td))
    doc_checks()

    lp = log_path()
    detail = "\n".join(_lines)
    lp.write_text(detail + "\n", encoding="utf-8")

    verdict = "PASS" if _bad == 0 else "FAIL"
    # stdout 前 6 行固定是结论 —— 自动验收会截断长输出。
    print("C-0071 首次运行体验自检（空库引导 / 无密钥引导 / README 卫生）")
    print(f"RESULT: {verdict}")
    print(f"断言: 通过 {_ok} 项，失败 {_bad} 项，用时 {time.time() - t0:.1f}s")
    print(f"明细日志: {lp}")
    print(f"检查项: 源码静态 / 临时实例动态 / README 文档卫生 —— 全程只读，不碰用户真实库")
    print("")
    print(detail)
    return 0 if _bad == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
