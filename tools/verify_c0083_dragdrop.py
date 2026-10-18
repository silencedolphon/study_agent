#!/usr/bin/env python
"""卡 C-0083 交付验证：拖拽文件进入库区即入库（拖拽上传通道）。

一句话验的是：**拖放区走 multipart 真上传 → 文件真落盘到 raw\\ → 落盘后不自动入库。**

跑法（工作目录＝项目根，cmd 与 pwsh 都能跑）：
    .\\venv\\Scripts\\python.exe tools\\verify_c0083_dragdrop.py

四件事：
  1. 静态证据：拖放区前端代码（dragover/dragenter 已 preventDefault、
     `folder` 字段先于文件 append、复用既有 /api/upload）、
     `_parse_multipart_stream` 的 `dest_dir` 笔误已修、前端上传上限常量
     与后端 `MAX_UPLOAD_BYTES` 一致、既有「方式一」入口与 /api/import 路由逐字仍在；
  2. 真起一个看板实例（**临时数据根 + 临时端口**，注册表与学科根都指到临时目录，
     不碰真实学科库），做真 multipart 往返：多文件落盘、重名自动 _1 且不覆盖、
     `folder` 子目录生效、非课件类型返回 kind、超限与空体给出可操作中文提示；
  3. 页面 HTML 抽查：拖放区元素、「不会自动入库」文案、立即入库勾选项**默认关**；
  4. 回归：/api/import 仍能把本机路径拷进 raw\\（方式一行为不变）。

stdout 只打结论（≤6 行），明细写 `_tmp_c0083\\verify.log`；失败项立刻打 stdout。
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "tools" / "dashboard.py"
TMPROOT = ROOT / "_tmp_c0083"
DATAROOT = TMPROOT / "verify_data"
LOGPATH = TMPROOT / "verify.log"

RESULTS: list[tuple[bool, str, str]] = []
LOG_LINES: list[str] = []


def log(msg: str) -> None:
    LOG_LINES.append(msg)


def check(name: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append((bool(ok), name, detail))
    log(("  OK  " if ok else " FAIL ") + name + (("  | " + detail) if detail else ""))
    if not ok:
        print("[FAIL] " + name + (("  | " + detail) if detail else ""), flush=True)
    return bool(ok)


# ---------------------------------------------------------------- 静态证据
def static_checks() -> None:
    src = SRC.read_text(encoding="utf-8")

    body = src[src.index("def _parse_multipart_stream"):src.index("def api_upload")]
    code_lines = [ln for ln in body.splitlines() if not ln.lstrip().startswith("#")]
    check("服务端：_parse_multipart_stream 里已无 dest_dir 笔误（注释里的说明不算）",
          all("dest_dir" not in ln for ln in code_lines),
          "该函数体的代码里只剩 target_dir")

    # 分界符前那个换行只能退 1~2 个字节：退多了会吃掉最后一行内容
    check("服务端：落盘只退分界符前那个换行（不再退掉整行）",
          all("out.truncate(written - tail)" in ln for ln in code_lines
              if "out.truncate(" in ln)
          and "out.truncate(prev_pos)" not in [ln.strip() for ln in code_lines],
          "written - tail")

    # folder 留空必须落到 raw\ 根，与「方式一」一致
    check("服务端：folder 留空落 raw\\ 根目录（不是 raw\\unnamed\\）",
          'sub = _safe_filename(want) if want else ""' in src
          and 'return Path(config.RAW_DIR) / sub if sub else Path(config.RAW_DIR)' in src,
          "与 api_import_path 同义")

    # folder 必须先于文件 append：后端边收边落盘，遇到第一个文件时才解析目录
    i_folder = src.index("fd.append('folder', sub)")
    i_files = src.index("ok.forEach(f => fd.append('files', f, f.name))")
    check("前端：FormData 里 folder 字段排在文件之前", i_folder < i_files,
          "folder@%d < files@%d" % (i_folder, i_files))

    drop = src[src.index("function bindDropzone()"):src.index("// ---- 入库进度条 ----")]
    check("前端：dragover/dragenter/drop 都 preventDefault（拖上去不会打开文件）",
          "['dragenter','dragover','dragleave','drop'].forEach" in drop
          and "e.preventDefault(); e.stopPropagation();" in drop)
    check("前端：走既有 /api/upload（没有另写一套上传通道）",
          "'/api/upload'" in drop and "new FormData()" in drop
          and "XMLHttpRequest" in drop)
    check("前端：上传有进度反馈（upload.onprogress）与连接层失败的可操作提示",
          "xhr.upload.onprogress" in drop and "xhr.onerror" in drop
          and "方式一" in drop)
    check("前端：拖文件夹 / 非课件格式 / 超大文件各有专门提示",
          "webkitGetAsEntry" in drop and "不入库管线" in drop and "DZ_KIND" in drop)
    check("前端：立即入库是显式勾选项（复选框默认不勾）",
          'id="dz-auto"' in src and 'id="dz-auto" checked' not in src
          and "checked" not in src[src.index('id="dz-auto"') - 60:
                                   src.index('id="dz-auto"') + 12],
          "默认关")

    # 前端上限常量必须等于后端 MAX_UPLOAD_BYTES（漂移的后果是"前端放行、后端 400"）
    m_back = re.search(r"^MAX_UPLOAD_BYTES\s*=\s*(.+)$", src, re.M)
    m_front = re.search(r"const DZ_MAX\s*=\s*(.+?);", src)
    back_val = eval(m_back.group(1).strip(), {"__builtins__": {}})  # noqa: S307 - 本文件常量
    front_val = eval(m_front.group(1).strip(), {"__builtins__": {}})  # noqa: S307
    check("前端上传上限常量 == 后端 MAX_UPLOAD_BYTES",
          back_val == front_val == 256 * 1024 * 1024,
          "后端 %d / 前端 %d" % (back_val, front_val))

    # 落盘后不自动入库：默认路径不允许出现"上传完就 reindex"的无条件调用
    check("前端：不勾选就不会入库（reindex 只在 auto 分支里）",
          re.search(r"const auto = \$\('#dz-auto'\).*?\n\s*if \(auto\)", drop, re.S) is not None
          and drop.count("reindex(") == 1,
          "reindex( 出现 %d 次，在 auto 分支内" % drop.count("reindex("))

    # 既有通道逐字仍在（回归）
    for anchor, why in (("id=\"im-src\"", "方式一：路径输入框"),
                        ("id=\"im-sub\"", "方式一：子文件夹输入框"),
                        ("id=\"im-go\"", "方式一：导入按钮"),
                        ("if u.path == \"/api/import\":", "/api/import 路由"),
                        ("def api_import_path(", "api_import_path()"),
                        ("shutil.copy2(str(f), str(dest))", "导入=copy2 落盘")):
        check("回归：%s 仍在" % why, anchor in src)


def page_checks(html: str) -> None:
    check("页面：拖放区元素（#dz / #dz-log / #dz-auto）都渲染出来了",
          all(k in html for k in ('id="dz"', 'id="dz-log"', 'id="dz-auto"', 'id="dz-bar"')))
    check("页面：写明「只放进 raw\\，不会自动入库」",
          "不会自动入库" in html and "拖拽入库" in html)
    check("页面：写明拖拽受浏览器上传限制、大文件走方式一",
          "fail to fetch" in html and "方式一" in html)
    check("页面：既有方式一与分享包入口都还在",
          'id="im-go"' in html and 'id="sh-open"' in html)


# ---------------------------------------------------------------- HTTP 往返
def multipart_body(files: list[tuple[str, bytes]], fields: list[tuple[str, str]]):
    boundary = "----c0083" + uuid.uuid4().hex
    out = bytearray()
    for k, v in fields:
        out += ("--%s\r\nContent-Disposition: form-data; name=\"%s\"\r\n\r\n%s\r\n"
                % (boundary, k, v)).encode("utf-8")
    for name, data in files:
        out += ("--%s\r\nContent-Disposition: form-data; name=\"files\"; filename=\"%s\"\r\n"
                "Content-Type: application/octet-stream\r\n\r\n"
                % (boundary, name)).encode("utf-8")
        out += data + b"\r\n"
    out += ("--%s--\r\n" % boundary).encode()
    return boundary, bytes(out)


def post_multipart(port: int, files, fields):
    boundary, body = multipart_body(files, fields)
    req = urllib.request.Request(
        "http://127.0.0.1:%d/api/upload" % port, data=body, method="POST",
        headers={"Content-Type": "multipart/form-data; boundary=" + boundary})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8") or "{}")


def post_json(port: int, path: str, payload: dict):
    req = urllib.request.Request(
        "http://127.0.0.1:%d%s" % (port, path),
        data=json.dumps(payload).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8") or "{}")


def raw_http(port: int, method: str, path: str, headers: dict | None = None,
             content_length: int | None = None, timeout: float = 8.0) -> str:
    """手写一个 HTTP 请求并读回**完整**响应（头 + body）。

    为什么不用 urllib 的两个理由：
      · 要测"Content-Length 超限"——必须在**一个字节 body 都不发**的情况下
        把大数报上去，看后端是不是在读 body 之前就拦住；
      · 响应的头与 body 是两次 write，一次 recv 可能只拿到头。
    带 Connection: close，所以读到 EOF 就是收全了。
    """
    s = socket.create_connection(("127.0.0.1", port), timeout=timeout)
    try:
        req = "%s %s HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n" % (method, path)
        for k, v in (headers or {}).items():
            req += "%s: %s\r\n" % (k, v)
        if content_length is not None:
            req += "Content-Length: %d\r\n" % content_length
        s.sendall((req + "\r\n").encode("utf-8"))
        chunks = []
        s.settimeout(timeout)
        while True:
            try:
                b = s.recv(65536)
            except socket.timeout:
                break
            if not b:
                break
            chunks.append(b)
        return b"".join(chunks).decode("utf-8", "replace")
    finally:
        s.close()


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def wait_ready(port: int, proc: subprocess.Popen, timeout: float = 25.0) -> bool:
    t0 = time.time()
    while time.time() - t0 < timeout:
        if proc.poll() is not None:
            return False
        try:
            with urllib.request.urlopen("http://127.0.0.1:%d/" % port, timeout=3) as r:
                if r.status == 200:
                    return True
        except Exception:
            time.sleep(0.4)
    return False


def live_checks(port: int, tmp: Path) -> None:
    # ---- 页面 ----
    with urllib.request.urlopen("http://127.0.0.1:%d/" % port, timeout=10) as r:
        html = r.read().decode("utf-8")
    page_checks(html)
    log("页面长度 %d 字节" % len(html))

    raw = tmp / "raw"
    data_a = ("卡C-0083 拖拽上传测试 A " * 40).encode("utf-8")     # 约 1KB
    data_b = ("卡C-0083 拖拽上传测试 B " * 60).encode("utf-8")

    # ---- 1. 多文件一次上传（folder 在前），落到子目录 ----
    st, res = post_multipart(port, [("讲义一.pdf", data_a), ("讲义二.docx", data_b)],
                             [("folder", "教材")])
    saved = {s["name"]: s for s in (res.get("saved") or [])}
    check("上传：两个文件都成功落盘（HTTP 200 + saved 两条）",
          st == 200 and len(saved) == 2, "HTTP %s / %s" % (st, list(saved)))
    p1 = raw / "教材" / "讲义一.pdf"
    p2 = raw / "教材" / "讲义二.docx"
    check("上传：folder 字段生效（落进 raw\\教材\\ 而不是 raw\\ 根）",
          p1.exists() and p2.exists(), str(p1))
    check("上传：磁盘字节与上传内容逐字节一致",
          p1.exists() and p1.read_bytes() == data_a
          and p2.exists() and p2.read_bytes() == data_b,
          "%d / %d 字节（应为 %d / %d）"
          % (p1.stat().st_size if p1.exists() else -1,
             p2.stat().st_size if p2.exists() else -1, len(data_a), len(data_b)))

    # 行尾形态各异的文本：单行无换行 / 多行混合行尾 + 末尾空行。
    # 这两种正是"落盘退换行"退多退少的边界，必须逐字节一致。
    one_line = "只有一行，末尾没有换行符".encode("utf-8")
    multi = "第一行\n第二行\r\n第三行\n\n".encode("utf-8")
    st_m, res_m = post_multipart(port, [("单行.txt", one_line), ("多行.txt", multi)],
                                 [("folder", "")])
    q1, q2 = raw / "单行.txt", raw / "多行.txt"
    check("上传：单行无换行 / 多行混合行尾，落盘字节都逐字节一致",
          q1.exists() and q1.read_bytes() == one_line
          and q2.exists() and q2.read_bytes() == multi,
          "单行 %d 字节（应 %d）／多行 %d 字节（应 %d）"
          % (q1.stat().st_size if q1.exists() else -1, len(one_line),
             q2.stat().st_size if q2.exists() else -1, len(multi)))
    check("上传：返回的 size 与磁盘实际大小一致（前端据此显示）",
          (res_m.get("saved") or [{}])[0].get("size") == len(one_line)
          and (res_m.get("saved") or [{}, {}])[1].get("size") == len(multi),
          str([s.get("size") for s in (res_m.get("saved") or [])]))

    check("上传：返回 kind 供前端判定「可入库 / 格式不支持」",
          saved.get("讲义一.pdf", {}).get("kind") == "pdf"
          and saved.get("讲义二.docx", {}).get("kind") == "docx",
          str({k: v.get("kind") for k, v in saved.items()}))

    # ---- 2. 重名不覆盖，自动 _1 ----
    data_c = ("第二份同名内容 C " * 50).encode("utf-8")
    st2, res2 = post_multipart(port, [("讲义一.pdf", data_c)], [("folder", "教材")])
    name2 = (res2.get("saved") or [{}])[0].get("name")
    check("重名：再次上传同名文件自动改名 _1（不覆盖已有文件）",
          st2 == 200 and name2 == "讲义一_1.pdf" and (raw / "教材" / "讲义一_1.pdf").exists(),
          "第二个落盘名 %s" % name2)
    check("重名：原文件字节**未被改动**",
          p1.read_bytes() == data_a and (raw / "教材" / "讲义一_1.pdf").read_bytes() == data_c)
    check("重名：返回 renamed=true（前端据此提示已自动改名）",
          (res2.get("saved") or [{}])[0].get("renamed") is True)

    # ---- 3. 非课件类型：仍然落盘，但 kind 让前端能给出可操作提示 ----
    st3, res3 = post_multipart(port, [("实验数据.xls", b"a,b,c\n1,2,3\n")], [("folder", "")])
    s3 = (res3.get("saved") or [{}])[0]
    check("非课件类型：.xls 落盘且 kind=legacy（前端据此提示入库会跳过）",
          st3 == 200 and s3.get("kind") == "legacy" and (raw / "实验数据.xls").exists(),
          "HTTP %s / kind=%s / 落盘=%s"
          % (st3, s3.get("kind"), (raw / "实验数据.xls").exists()))
    st3b, res3b = post_multipart(port, [("怪文件.zzz", b"\x00\x01\x02")], [("folder", "")])
    s3b = (res3b.get("saved") or [{}])[0]
    check("非课件类型：.zzz 落盘且 kind=unknown",
          st3b == 200 and s3b.get("kind") == "unknown", "kind=%s" % s3b.get("kind"))

    # ---- 4. 超大文件：在后端读 body **之前**就用 400 + 可操作中文拦住 ----
    # 只发头、不发 body：后端先查 Content-Length，超限直接回 400。
    resp = raw_http(port, "POST", "/api/upload",
                    headers={"Content-Type": "multipart/form-data; boundary=xxxxxxxx"},
                    content_length=300 * 1024 * 1024)
    check("超大文件：超过上限直接 400，且提示写清下一步走哪条路",
          " 400 " in resp.split("\r\n")[0]
          and "上传体积过大" in resp and "从本机路径导入" in resp,
          resp.split("\r\n")[0])

    # ---- 5. 空请求体（Content-Length: 0）----
    resp5 = raw_http(port, "POST", "/api/upload", content_length=0)
    check("空请求体：400 且提示「请求体为空」",
          " 400 " in resp5.split("\r\n")[0] and "请求体为空" in resp5,
          resp5.split("\r\n")[0])

    # ---- 6. 回归：/api/import（方式一）行为不变 ----
    srcfile = tmp / "源文件.txt"
    srcfile.write_text("方式一回归：用本机路径导入\n", encoding="utf-8")
    st6, res6 = post_json(port, "/api/import", {"src": str(srcfile), "sub": "课件"})
    check("回归：POST /api/import 仍把本机文件拷进 raw\\（copied=1）",
          st6 == 200 and res6.get("copied") == 1
          and (raw / "课件" / "源文件.txt").exists(),
          "copied=%s dest=%s" % (res6.get("copied"), res6.get("dest")))

    # ---- 7. 落盘的文件在「待入库」清单里（未自动入库） ----
    with urllib.request.urlopen("http://127.0.0.1:%d/api/raw" % port, timeout=10) as r:
        rawinfo = json.loads(r.read().decode("utf-8"))
    files = {f["name"]: f for f in rawinfo.get("files", [])}
    check("落盘后仍处于「未入库」状态（没有自动入库）",
          files.get("讲义一.pdf", {}).get("state") == "new"
          and files.get("讲义一.pdf", {}).get("supported") is True,
          "state=%s" % files.get("讲义一.pdf", {}).get("state"))


# ---------------------------------------------------------------- main
def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true", help="保留临时数据根与看板进程")
    args = ap.parse_args()

    TMPROOT.mkdir(parents=True, exist_ok=True)
    if DATAROOT.exists():
        shutil.rmtree(DATAROOT, ignore_errors=True)
    DATAROOT.mkdir(parents=True, exist_ok=True)
    logfile = open(LOGPATH, "w", encoding="utf-8")

    static_checks()

    port = free_port()
    proc = None
    try:
        proc = subprocess.Popen(
            [sys.executable, str(ROOT / "tools" / "c0083_tmp_launch.py"),
             str(DATAROOT), str(port)],
            cwd=str(ROOT), stdout=logfile, stderr=subprocess.STDOUT)
        ok = wait_ready(port, proc)
        check("临时看板实例起来了（临时数据根 + 端口 %d）" % port, ok)
        if ok:
            live_checks(port, DATAROOT)
    finally:
        if proc is not None and not args.keep:
            proc.terminate()
            try:
                proc.wait(timeout=8)
            except Exception:
                proc.kill()
    if not args.keep:
        # Windows 上刚 terminate 的进程可能还攥着文件句柄，删一次不够
        for _ in range(4):
            shutil.rmtree(DATAROOT, ignore_errors=True)
            if not DATAROOT.exists():
                break
            time.sleep(0.4)

    logfile.write("\n".join(LOG_LINES) + "\n")
    logfile.close()

    passed = sum(1 for ok, _, _ in RESULTS if ok)
    total = len(RESULTS)
    print("卡 C-0083 验证：拖拽入库（multipart 真上传 → raw\\ 真落盘 → 不自动入库）")
    print("%s  %d/%d 项通过" % ("PASS" if passed == total else "FAIL", passed, total))
    print("C0083-VERIFY %s %d/%d" % ("PASS" if passed == total else "FAIL", passed, total))
    print("明细日志：%s" % LOGPATH)
    if passed != total:
        print("未通过项：" + "；".join(n for ok, n, _ in RESULTS if not ok))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
