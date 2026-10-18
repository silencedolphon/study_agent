# -*- coding: utf-8 -*-
"""卡 C-0102：对着**临时实例**验 `/api/kp-textbook` 的分支与教材页图（只读、零模型调用）。

它验的是"前端要认的那几种情况，后端真的这么答"：
  · ok          doc#103 + KP-N03-1（真产物）→ 有 passages/terms/page_url
  · no_match    脱靶知识点（--nomatch-fixture 造）→ status=no_match + 明确 message
  · no_kp       不存在的 kp_id → status=no_kp + 明确 message
  · no_textbook 库副本里没有 role=textbook 时（--drop-textbook 的实例）→ 明确 message
  · 教材页图    用 ok 返回的 `page_url` 模板换页码 → HTTP 200 + PNG magic

用法（先起临时实例，再把端口给它）：
    G:\\study_agent\\venv\\Scripts\\python.exe -X utf8 G:\\study_agent\\tools\\c0102_probe_http.py \\
        --port 8795 --expect ok,no_match,no_kp
输出结尾固定打印 `C0102-HTTP-PASS` 或 `C0102-HTTP-FAIL`。
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")


def get(port: int, path: str) -> tuple[int, bytes, str]:
    url = "http://127.0.0.1:%d%s" % (port, path)
    try:
        with urllib.request.urlopen(url, timeout=60) as r:
            return r.status, r.read(), r.headers.get("Content-Type", "")
    except urllib.error.HTTPError as e:      # 明确的状态码也是结论
        return e.code, e.read(), e.headers.get("Content-Type", "")


def kp_textbook(port: int, doc_id: int, kp_id: str) -> dict:
    st, body, _ct = get(port, "/api/kp-textbook?doc_id=%d&kp_id=%s"
                        % (doc_id, urllib.parse.quote(kp_id)))
    try:
        return {"_http": st, **(json.loads(body.decode("utf-8")) or {})}
    except Exception as e:                    # noqa: BLE001
        return {"_http": st, "_raw": body[:200].decode("utf-8", "replace"), "_err": str(e)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--expect", default="ok,no_match,no_kp",
                    help="这个实例应当能验到哪几个分支（no_textbook 要 --drop-textbook 的实例）")
    a = ap.parse_args()
    expect = [x.strip() for x in a.expect.split(",") if x.strip()]
    fails: list[str] = []

    def check(ok, label, detail=""):
        print(("ok  " if ok else "NG  ") + label + ((" | " + str(detail)[:300]) if detail else ""),
              flush=True)
        if not ok:
            fails.append(label)
        return ok

    seen: dict = {}
    for want in expect:
        if want == "ok":
            d = kp_textbook(a.port, 103, "KP-N03-1")
            seen["ok"] = d
            tb = d.get("textbook") or {}
            ps = tb.get("passages") or []
            check(d.get("status") == "ok" and d.get("_http") == 200,
                  "ok 分支：doc#103 + KP-N03-1 → status=ok",
                  "status=%s http=%s" % (d.get("status"), d.get("_http")))
            check(bool(ps) and bool(tb.get("page_url")),
                  "ok 分支带 passages + page_url 模板",
                  "段数=%d page_url=%s" % (len(ps), tb.get("page_url")))
            check(bool(d.get("message")) and "仅供参考" in str(d.get("message")),
                  "ok 分支的 message 自带「仅供参考」与页码",
                  d.get("message"))
            if ps:
                check(all(("page" in p and "text" in p and "page_label" in p) for p in ps),
                      "每段都带 page / page_label / text（前端要用来做按钮与原文）",
                      "首段键=%s" % sorted(ps[0].keys())[:12])
        elif want == "no_match":
            d = kp_textbook(a.port, 103, "KP-C0102-NOMATCH")
            seen["no_match"] = d
            check(d.get("status") == "no_match" and d.get("_http") == 200,
                  "no_match 分支：脱靶知识点 → status=no_match（HTTP 200，不是错误）",
                  "status=%s http=%s" % (d.get("status"), d.get("_http")))
            check(bool(d.get("message")), "no_match 有明确 message", d.get("message"))
        elif want == "no_kp":
            d = kp_textbook(a.port, 103, "KP-不存在-C0102")
            seen["no_kp"] = d
            check(d.get("status") == "no_kp" and d.get("_http") == 200,
                  "no_kp 分支：不存在的 kp_id → status=no_kp（HTTP 200）",
                  "status=%s http=%s" % (d.get("status"), d.get("_http")))
            check(bool(d.get("message")), "no_kp 有明确 message", d.get("message"))
        elif want == "no_textbook":
            d = kp_textbook(a.port, 103, "KP-N03-1")
            seen["no_textbook"] = d
            check(d.get("status") == "no_textbook" and d.get("_http") == 200,
                  "no_textbook 分支：库副本里没有教材 → status=no_textbook（HTTP 200）",
                  "status=%s http=%s" % (d.get("status"), d.get("_http")))
            check(bool(d.get("message")), "no_textbook 有明确 message", d.get("message"))

    # ---- 教材页图：用 ok 分支给的模板换页码 ----
    tb = (seen.get("ok") or {}).get("textbook") or {}
    tpl = str(tb.get("page_url") or "")
    if tpl:
        page = int((tb.get("passages") or [{}])[0].get("page") or 0)
        url = tpl.replace("{page}", str(page))
        # 模板是相对路径（/api/teach-page?...），补上主机
        st, body, ct = get(a.port, url if url.startswith("/") else "/" + url)
        check(st == 200 and body[:8] == b"\x89PNG\r\n\x1a\n",
              "教材页图：page_url 换页码后 HTTP 200 + PNG（零模型调用）",
              "http=%s ct=%s bytes=%d page=%s" % (st, ct, len(body), page))
        check(len(body) > 20000, "页图不是空图（>20KB）", "bytes=%d" % len(body))
        st2, _b2, _c2 = get(a.port, "/api/teach-page?doc_id=103&page=%d" % page)
        check(st2 in (403, 404), "安全闸：非教材 doc_id 被拒（不许拿课件当教材渲染）",
              "doc#103 → HTTP %s" % st2)

    print("-" * 60)
    if fails:
        print("C0102-HTTP-FAIL 共 %d 条未过" % len(fails))
        for f in fails:
            print("  NG %s" % f)
        return 1
    print("C0102-HTTP-PASS 分支 = %s（临时实例端口 %d）" % (",".join(expect), a.port))
    return 0


if __name__ == "__main__":
    sys.exit(main())
