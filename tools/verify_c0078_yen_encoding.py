# Copyright (c) 2026 潜声海豚 <2227663973@qq.com>. All rights reserved.
# 本源码为著作权人私有作品（proprietary）：仅供经书面授权的测试使用；
# 未经著作权人许可，不得复制、分发、公开、发布或用于任何商业用途。
# Unauthorized copying, distribution or publication is strictly prohibited.
# File-ID: SA-26-c0078vf   (project study_agent; keep this line)
"""卡 C-0078 验收脚本：编码容错 + `U+00A5` 清理 + 许可文件随包。

## 这个脚本查什么（全部零成本、不联网、不碰任何数据库）

  ① U+00A5 清理：**会打进 exe 的运行时源码**（`core/*.py` + `tools/dashboard.py`）
                 里 U+00A5 字节对（`0xC2 0xA5`）计数必须为 **0**；
                 卡面点名的 5 个文件单独再报一次逐文件计数。
  ② 容错在位   ：两个入口都定义了 `_setup_console_encoding()` 且在模块级**已调用**。
  ③ 容错生效   ：**子进程实测** —— 在被管道捕获（= GBK，exe 的现场）的环境里
                 打印 GBK 编不出的字符（U+00A5 / U+26A0 / U+21BB）：
                 修好前会 `UnicodeEncodeError` 退出码 1，修好后必须 rc=0 且原样打印；
                 tty 分支（保留编码 + `errors=replace`）另测一次。
  ④ 许可随包   ：`release/C-0073/StudyDashboard/` 的 **exe 同级**必须有
                 LICENSE / NOTICE.txt / THIRD-PARTY-NOTICES.md /
                 THIRD-PARTY-LICENSES.txt / 使用说明.md 五份，且非空。

## 为什么范围是"运行时源码"而不是"全项目"

`docs/`、`README.md`、`tools/` 下那些**不进包**的脚本里还有 U+00A5（历史文档与
探针脚本），它们不参与 exe 运行、不写 stdout，属于**内容资产/历史记录**，
按卡面"只改展示符号、不动其它"的边界刻意保持原样。
真正的触发面只有"会随 exe 跑起来并可能 print 的那些文件"——就是 ① 的集合。

用法：

    venv\\Scripts\\python.exe tools\\verify_c0078_yen_encoding.py
"""
from __future__ import annotations

import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
PY = str(ROOT / "venv" / "Scripts" / "python.exe")

# 卡面点名要逐文件报数的 5 个文件
CARD_FILES = ("core/kp_build.py", "tools/dashboard.py", "core/figures.py",
              "core/config.py", "core/ingest.py")
# 会随 exe 跑起来的运行时源码（core 全部模块 + 看板入口），外加本卡一并
# 加了编码容错的 CLI 入口 tools/study.py。
RUNTIME_FILES = [p for p in sorted((ROOT / "core").glob("*.py"))] + \
                [ROOT / "tools" / "dashboard.py", ROOT / "tools" / "study.py"]
ENTRIES = ("tools/dashboard.py", "tools/study.py")
EXE_DIR = ROOT / "release" / "C-0073" / "StudyDashboard"
PACK_DOCS = ("LICENSE", "NOTICE.txt", "THIRD-PARTY-NOTICES.md",
             "THIRD-PARTY-LICENSES.txt", "使用说明.md")

YEN = b"\xc2\xa5"          # U+00A5 的 UTF-8 字节对（两字节，不会与别的字符混淆）


def setup_output() -> None:
    """输出被捕获/重定向时写 UTF-8；交互终端保留原编码只把 errors 换成 replace。

    和本卡给 `tools/dashboard.py` / `tools/study.py` 加的是同一套分流：
    验收方多半是**管道捕获**本脚本的输出（那时 UTF-8 才不会乱码），
    而人直接在 cmd 窗口里跑时保持 GBK、中文照旧显示。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            if stream.isatty():
                stream.reconfigure(errors="replace")
            else:
                stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:                                # noqa: BLE001
            pass


def say(msg: str) -> None:
    try:
        print(msg, flush=True)
    except UnicodeEncodeError:                       # GBK 控制台兜底
        enc = sys.stdout.encoding or "utf-8"
        print(msg.encode(enc, "replace").decode(enc, "replace"), flush=True)


def check_yen() -> tuple[bool, list[str]]:
    rows: list[str] = []
    total = 0
    for p in RUNTIME_FILES:
        n = p.read_bytes().count(YEN)
        total += n
        if n:
            rows.append("  [NG] %s 仍有 %d 处 U+00A5"
                        % (p.relative_to(ROOT).as_posix(), n))
    card = {}
    for rel in CARD_FILES:
        card[rel] = (ROOT / rel).read_bytes().count(YEN)
    rows.append("  运行时源码（core/*.py + tools/dashboard.py，共 %d 个文件）"
                "U+00A5 合计 = %d" % (len(RUNTIME_FILES), total))
    rows.append("  卡面点名 5 文件逐文件计数：" +
                "、".join("%s=%d" % (k, v) for k, v in card.items()))
    return total == 0, rows


def check_entry() -> tuple[bool, list[str]]:
    rows: list[str] = []
    ok = True
    for rel in ENTRIES:
        txt = (ROOT / rel).read_text(encoding="utf-8")
        has_def = "def _setup_console_encoding() -> None:" in txt
        # 模块级调用：函数定义**之后**必须出现一次裸调用（不是只在注释里提）
        after = txt.split("def _setup_console_encoding() -> None:", 1)[-1]
        has_call = any(ln.strip() == "_setup_console_encoding()"
                       for ln in after.splitlines())
        rows.append("  %s：定义=%s 模块级已调用=%s" % (rel, has_def, has_call))
        ok = ok and has_def and has_call
    return ok, rows


def check_runtime() -> tuple[bool, list[str]]:
    """子进程实测：GBK 现场打印不可编码字符。"""
    rows: list[str] = []
    probe = "print('\\u00a5\\u26a0\\u21bb OK')"
    load = ("import importlib.util as u;"
            "s=u.spec_from_file_location('d', r'%s');"
            "m=u.module_from_spec(s);s.loader.exec_module(m);"
            % (ROOT / "tools" / "dashboard.py"))

    # (a) 对照：不加载入口 → 必须复现 GBK 编码错误
    p0 = subprocess.run([PY, "-c", probe], capture_output=True)
    e0 = p0.stderr.decode("utf-8", "replace")
    ctrl_reproduces = ("UnicodeEncodeError" in e0 and "gbk" in e0)
    rows.append("  (a) 对照·不加载入口：rc=%d，复现 GBK 编码错误=%s"
                % (p0.returncode, ctrl_reproduces))

    # (b) 加载真入口 → 必须 rc=0 且三个字符原样出现
    p1 = subprocess.run([PY, "-c", load + probe], capture_output=True)
    o1 = p1.stdout.decode("utf-8", "replace")
    fixed_ok = (p1.returncode == 0 and "\u00a5\u26a0\u21bb OK" in o1
                and "UnicodeEncodeError" not in p1.stderr.decode("utf-8", "replace"))
    # ⚠ 回显一律转成 ASCII 转义：GBK 控制台会把 U+00A5/⚠/↻ 显示成 `?`，
    #   直接打出来会让"证据"看起来像失败（本卡踩过这个坑）。
    last = (o1.strip().splitlines()[-1] if o1.strip() else "")
    rows.append("  (b) 加载真入口后：rc=%d，三字符原样打印=%s，stdout 末行=%s"
                % (p1.returncode, fixed_ok,
                   last.encode("unicode_escape").decode("ascii")))

    # (c) tty 分支：保留 GBK 编码、只加 errors=replace 也必须不崩
    code_c = ("import io;b=io.BytesIO();w=io.TextIOWrapper(b,encoding='gbk');"
              "w.reconfigure(errors='replace');w.write('\\u00a5\\u26a0');w.flush();"
              "print('TTY_OK')")
    p2 = subprocess.run([PY, "-c", code_c], capture_output=True)
    tty_ok = p2.returncode == 0 and "TTY_OK" in p2.stdout.decode("utf-8", "replace")
    rows.append("  (c) tty 分支（GBK + errors=replace）：rc=%d，不崩=%s"
                % (p2.returncode, tty_ok))
    return (ctrl_reproduces and fixed_ok and tty_ok), rows


def check_pack() -> tuple[bool, list[str]]:
    rows: list[str] = []
    ok = True
    for name in PACK_DOCS:
        p = EXE_DIR / name
        good = p.is_file() and p.stat().st_size > 0
        ok = ok and good
        rows.append("  exe 同级 %-24s 存在=%-5s 大小=%s"
                    % (name, p.is_file(),
                       ("%d 字节" % p.stat().st_size) if p.is_file() else "-"))
    rows.append("  发布目录：%s" % EXE_DIR)
    return ok, rows


def main() -> int:
    setup_output()
    ok_yen, r_yen = check_yen()
    ok_entry, r_entry = check_entry()
    ok_rt, r_rt = check_runtime()
    ok_pack, r_pack = check_pack()
    ok = ok_yen and ok_entry and ok_rt and ok_pack

    say("C0078-%s  编码容错 + U+00A5 清理 + 许可随包" % ("PASS" if ok else "FAIL"))
    say("  ① U+00A5 清理（运行时源码计数=0）：%s" % ok_yen)
    say("  ② 两个入口容错在位且已调用：%s" % ok_entry)
    say("  ③ 子进程实测容错生效（对照+修复+tty 三组）：%s" % ok_rt)
    say("  ④ exe 同级 5 份许可/说明齐备：%s" % ok_pack)
    say("")
    for rows in (r_yen, r_entry, r_rt, r_pack):
        for line in rows:
            say(line)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
