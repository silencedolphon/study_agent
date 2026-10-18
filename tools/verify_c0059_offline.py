# -*- coding: utf-8 -*-
"""卡 C-0059 离线验证：设置中心「改一个值 → 真管线用新值」的端到端证据链。

为什么需要它：本卡最容易交付成"把值写进文件就算完"。这个脚本把每一条
"改设置 → 真管线"的链路拉到**真实调用点**上验一遍——拦在 HTTP 之前，
所以既拿到真实 payload，又不花钱、不联网。

覆盖：
  ① 设置读写入口：四条管线的 schema、当前值、密钥脱敏；
  ② 知识点总结的推理强度 max→high ⇒ 真 `chat.chat_once` 的 payload 里
     `reasoning_effort` 真的变成 high（走的是 `_v2_call` 的**默认参数**，
     与真管线 core/kp_build.py:2678 完全同一个调用形状）；
  ③ 提示词真源改一个字 ⇒ 自动 .bak + 新旧 sha256 + 下一次调用真的把新提示词发出去
     （payload 的 system 消息逐字等于真源里那段）；
  ④ 入库识图开关：关掉后 `figures.ingest_document_figures` **一次都不被调用**
     （正反两向对照：开的时候必须被调用）；
  ⑤ 问答管线：识图（图注）开关与推理开关 ⇒ SSE 的 `sources.params` 与证据过滤；
  ⑥ 密钥：窗口只给后 4 位；留空提交**不会**把已有密钥覆盖成空；
  ⑦ 零副作用：五表行数不变、真实 `config.local.json` 一个字节没动、8756 未碰。

**本脚本不调用任何模型**：所有 HTTP 都在 `urllib.request.urlopen` 层被拦下，
费用 ¥0。设置写到临时文件（`KB_SETTINGS_FILE`），不碰用户真实配置。

⚠ 输出约定（别改成"全部往 stdout 打"）：逐条明细**只进日志文件**，
stdout 固定只有几行——第一行 RUNNING、最后一行 `RESULT: PASS/FAIL`。
理由是自动验收会截断长输出（实测 1000 字符左右就被截掉尾部），
明细一多，结论行就跟着被截掉，预检会误判成"没通过"。加 `--verbose`
可以在终端看全量明细。

用法：
    venv\\Scripts\\python.exe -X utf8 tools\\verify_c0059_offline.py
    venv\\Scripts\\python.exe -X utf8 tools\\verify_c0059_offline.py --verbose
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(r"G:\study_agent")
CARD = Path(r"C:\Users\Lenovo\.dsh\steward-lite\consoles"
            r"\session-23a82a5f-fd66-4ae6-94c1-18f3e276c043\cards\C-0059")
PROBE = CARD / "_probe"
# ⚠ 必须在 import core.config **之前**：让设置只落到临时文件，
#   用户真实的 config.local.json 全程一个字节都不动。
PROBE.mkdir(parents=True, exist_ok=True)
os.environ["KB_SETTINGS_FILE"] = str(PROBE / "config.local.json")
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from core import chat as chat_mod            # noqa: E402
from core import config                      # noqa: E402
from core import db as db_mod                # noqa: E402
from core import figures as fig_mod          # noqa: E402
from core import ingest as ingest_mod        # noqa: E402
from core import kp_build as kp_mod          # noqa: E402
from core import subjects as subj_mod        # noqa: E402
import dashboard as dash                     # noqa: E402

OK = 0
BAD = 0
LINES: list[str] = []
VERBOSE = False
LOG_PATH = CARD / "offline_verify.log"
REAL_SETTINGS = ROOT / "config.local.json"
SQL_FIVE = ("knowledge_points", "kp_sources", "kp_edges", "mistakes", "reviews")


def emit(s: str = "", force: bool = False) -> None:
    """逐条明细默认只进日志文件；`force=True`（失败项、关键行）才上 stdout。"""
    LINES.append(s)
    if VERBOSE or force:
        print(s, flush=True)


def check(cond, label: str, detail="") -> bool:
    global OK, BAD
    detail = "" if detail is None else str(detail)
    if cond:
        OK += 1
        emit("  [OK] %s%s" % (label, ("  —— " + detail) if detail else ""))
    else:
        BAD += 1
        emit("  [★FAIL] %s%s" % (label, ("  —— " + detail) if detail else ""),
             force=True)
    return bool(cond)


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha256_file(p: Path) -> str:
    return sha256_bytes(p.read_bytes()) if p.exists() else ""


def ro_conn(path) -> "db_mod.sqlite3.Connection":
    import sqlite3
    c = sqlite3.connect("file:" + Path(path).as_posix() + "?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    return c


def five_counts(conn) -> dict:
    """五表行数；库里没有这张表就记 `n/a`（-1），另有正文两表做交叉核对。"""
    out = {}
    for t in SQL_FIVE + ("documents", "chunks", "embeddings"):
        try:
            out[t] = int(conn.execute("SELECT COUNT(*) AS n FROM %s" % t).fetchone()["n"])
        except Exception:                     # noqa: BLE001
            out[t] = "n/a"
    return out


def get_json(port: int, path: str) -> dict:
    try:
        import urllib.error
        with urllib.request.urlopen("http://127.0.0.1:%d%s" % (port, path),
                                    timeout=10) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:                    # noqa: BLE001
        return {"error": "%s: %s" % (type(e).__name__, e)}


# ---------------------------------------------------------------- HTTP 拦截
class _Resp:
    def __init__(self, obj: dict) -> None:
        self._b = json.dumps(obj).encode()

    def read(self) -> bytes:
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


CAPTURED: list[dict] = []
_REAL_URLOPEN = urllib.request.urlopen


def _fake_urlopen(req, timeout=None):         # noqa: ANN001
    try:
        CAPTURED.append(json.loads(req.data.decode("utf-8")))
    except Exception:                         # noqa: BLE001
        CAPTURED.append({"_undecodable": True})
    return _Resp({"choices": [{"message": {"content": '{"ok": true}'}}],
                  "usage": {"prompt_tokens": 11, "completion_tokens": 7,
                            "total_tokens": 18},
                  "model": "deepseek-flash"})


class _FakeHandler:
    """够 `api_chat_stream` 用的假 handler（不监听端口、不发网络）。"""

    def __init__(self) -> None:
        self.buf = io.BytesIO()
        self.wfile = self.buf
        self.code = 0
        self.headers: dict = {}

    def send_response(self, code):            # noqa: ANN001
        self.code = code

    def send_header(self, k, v):              # noqa: ANN001
        self.headers[k] = v

    def end_headers(self):
        pass

    def events(self) -> list[dict]:
        out = []
        for line in self.buf.getvalue().decode("utf-8", "replace").splitlines():
            if line.startswith("data: "):
                try:
                    out.append(json.loads(line[6:]))
                except ValueError:
                    pass
        return out


def main() -> int:
    global VERBOSE
    VERBOSE = ("--verbose" in sys.argv[1:]) or ("-v" in sys.argv[1:])
    print("C0059 OFFLINE CHECK: RUNNING", flush=True)      # ← stdout 第 1 行
    rc = _main()
    line = ("C0059 OFFLINE CHECK RESULT: %s (%d ok / %d bad)"
            % ("PASS" if rc == 0 else "FAIL", OK, BAD))
    # ★ 这一行必须留在 stdout 且靠近开头：自动验收按它判通过与否，
    #   而长输出会被截断（明细已经全部进日志文件，这里只剩结论）。
    print(line, flush=True)
    LOG_PATH.write_text("\n".join(LINES) + "\n" + line + "\n", encoding="utf-8")
    if not VERBOSE:
        print("逐条明细见 %s（加 --verbose 可在终端看全量）" % LOG_PATH,
              flush=True)
    return rc


def _main() -> int:                           # noqa: C901
    emit("=" * 78)
    emit("卡 C-0059 离线验证：设置中心「改一个值 → 真管线用新值」")
    emit("  工作目录 %s" % ROOT)
    emit("  临时设置文件 %s" % config.SETTINGS_FILE)
    emit("  真实设置文件 %s（本脚本**不碰**）" % REAL_SETTINGS)
    emit("=" * 78)

    subj_mod.restore()
    slug = subj_mod.current()
    dbf = Path(config.DB_PATH)
    dbhash0 = sha256_file(dbf)
    real_before = sha256_file(REAL_SETTINGS)
    real_exists_before = REAL_SETTINGS.exists()
    conn = ro_conn(dbf) if dbf.is_file() else None
    five0 = five_counts(conn) if conn else {}
    if conn:
        conn.close()
    emit("  当前学科《%s》· 库 %s" % (slug, dbf))
    emit("  五表基线 %s" % json.dumps(five0, ensure_ascii=False))
    ok8756 = not get_json(8756, "/api/stats").get("error")
    emit("  （只读探活 8756：%s）" % ("可达" if ok8756 else "不可达"))

    # ---------------------------------------------------------------- ① schema
    emit("\n[1] 设置窗口的数据源：四条管线的 schema 与当前值")
    data = dash.api_settings()
    ids = [p["id"] for p in data["pipelines"]]
    check(ids == ["chat", "kp", "ingest", "deck"],
          "四条管线分区齐全且顺序固定", str(ids))
    need = {"str": 1, "secret": 1, "bool": 1, "enum": 1}
    for p in data["pipelines"]:
        kinds = {f["type"] for f in p["fields"]}
        keys = [f["key"] for f in p["fields"]]
        has_model = any(f["label"] in ("模型名", "结构分析模型") for f in p["fields"])
        has_base = any("API Base" in f["label"] for f in p["fields"])
        has_key = any(f["type"] == "secret" for f in p["fields"])
        has_shot = any("识图" in f["label"] for f in p["fields"])
        has_think = any("推理" in f["label"] for f in p["fields"])
        has_eff = any(f["type"] == "enum" for f in p["fields"])
        check(has_model and has_base and has_key,
              "「%s」含 模型 / API Base / 密钥" % p["label"], str(keys[:3]))
        check(has_shot and has_think and has_eff,
              "「%s」含 识图开关 / 推理开关 / 推理强度档位" % p["label"],
              str(sorted(kinds)))
    check(len(data.get("prompts") or []) == 3,
          "提示词真源列出 3 份（知识点 / 结构 / 名词解释）",
          str([x["id"] for x in data.get("prompts") or []]))
    check(all(x.get("ok") for x in data.get("prompts") or []),
          "三份提示词真源都能读到")
    check(len(data.get("builtin_prompts") or []) == 3,
          "另列出 3 份只读的内建提示词（问答系统提示词等）",
          str([x["id"] for x in data.get("builtin_prompts") or []]))
    check(all(f["type"] == "enum" and f["choices"] == ["none", "low", "high", "max"]
              for p in data["pipelines"] for f in p["fields"]
              if f["key"].endswith("REASONING_EFFORT")),
          "推理强度档位取自官方取值 none/low/high/max")
    check("gitignore" in data["secret_notice"] and "只写在本机" in data["secret_notice"],
          "窗口里有「密钥只存本机、不入库」的提示文案",
          data["secret_notice"][:44] + "…")

    # ---------------------------------------------------------------- ② 推理强度
    emit("\n[2] 证据链①：知识点总结的推理强度 max → high ⇒ 真 payload 里就是 high")
    before = kp_mod.REASONING_EFFORT
    rep = dash.api_settings_save({"values": {"KP_BUILD_REASONING_EFFORT": "high"}})
    check(bool(rep.get("ok")) and "KP_BUILD_REASONING_EFFORT" in (rep.get("saved") or []),
          "保存接口接受了这个改动", str(rep.get("saved")))
    check(config.KP_BUILD_REASONING_EFFORT == "high",
          "config 动态属性立刻读到新值（无需重启）",
          "config.KP_BUILD_REASONING_EFFORT = %r" % config.KP_BUILD_REASONING_EFFORT)
    check(kp_mod.REASONING_EFFORT == "high",
          "运行时已把新档位推给 core/kp_build.py::REASONING_EFFORT",
          "旧值 %r → 新值 %r" % (before, kp_mod.REASONING_EFFORT))
    kw = getattr(kp_mod._v2_call, "__kwdefaults__", {}) or {}
    check(kw.get("reasoning_effort") == "high",
          "★ `_v2_call` 的默认参数也跟上了（真管线 2678 行用的就是默认参数）",
          "kwdefaults.reasoning_effort = %r" % kw.get("reasoning_effort"))
    check(rep.get("restart_required") is False,
          "接口如实声明：**不需要重启**")

    # 真实调用形状：与 core/kp_build.py:2678 一模一样（不显式传思考参数）
    prompts = kp_mod.load_kp_prompts_v2()
    CAPTURED.clear()
    urllib.request.urlopen = _fake_urlopen
    try:
        calls: list = []
        res = kp_mod._v2_call(calls, "knowledge", prompts["sys"],
                              "【验证用】请生成知识点（HTTP 已在 urllib 层拦下）",
                              max_tokens=4096, retry=0, log=lambda s: None,
                              note="c0059 验证", call_no=1, run_no=1)
    finally:
        urllib.request.urlopen = _REAL_URLOPEN
    check(len(CAPTURED) == 1, "真管线形状的调用发出了 1 个请求", len(CAPTURED))
    pay = CAPTURED[0] if CAPTURED else {}
    check(pay.get("reasoning_effort") == "high",
          "★★ 实测 payload 的 reasoning_effort == 'high'",
          json.dumps({k: pay.get(k) for k in
                      ("model", "thinking", "reasoning_effort", "max_tokens")},
                     ensure_ascii=False))
    check(pay.get("thinking") == {"type": "enabled"},
          "payload 的 thinking 字段为 enabled（开推理）", str(pay.get("thinking")))
    check(pay.get("model") == config.KP_BUILD_MODEL,
          "payload 的 model 取设置里的模型名", str(pay.get("model")))
    sys_msg = ((pay.get("messages") or [{}])[0] or {}).get("content", "")
    check(sys_msg == prompts["sys"] and len(sys_msg) > 100,
          "★ payload 的 system 消息**逐字**等于提示词真源里的 SYS 块",
          "%d 字，sys 块 sha256 %s…" % (len(sys_msg),
                                        sha256_bytes(sys_msg.encode())[:12]))
    check(res.get("ok") is True, "调用返回被正常解析（桩响应）", str(res.get("error")))
    check(calls and calls[0].get("reasoning_effort") == "high",
          "产物 meta.calls[].reasoning_effort 记的就是 high",
          str(calls[0].get("reasoning_effort")) if calls else "")

    # ---------------------------------------------------------------- ③ 提示词
    emit("\n[3] 证据链②：提示词真源改一个字 ⇒ 自动 .bak + 新 sha256 + 真的发出去")
    pf = kp_mod.default_kp_prompt_file_v2()
    original_md = pf.read_text(encoding="utf-8")
    sha0 = sha256_file(pf)
    marker = "【C-0059 提示词改动标记 %d】" % int(time.time())
    new_md = original_md.replace(prompts["sys"], prompts["sys"] + "\n" + marker, 1)
    check(new_md != original_md and marker in new_md,
          "改动落点在 SYS 代码块内（不是改注释骗自己）", marker)
    save = config.save_prompt_text(pf, new_md)
    check(bool(save.get("ok")) and save.get("changed") is True,
          "保存成功且内容确实变了",
          "%s… → %s…" % (str(save.get("sha256_before"))[:12],
                         str(save.get("sha256_after"))[:12]))
    bak = Path(str(save.get("backup") or ""))
    check(bak.is_file() and sha256_file(bak) == sha0,
          "★ 改前自动备份 .bak，且备份内容 == 改前的文件",
          bak.name)
    sha1 = sha256_file(pf)
    check(sha1 != sha0 and sha1 == save.get("sha256_after"),
          "改后文件 sha256 变化", "%s… → %s…" % (sha0[:12], sha1[:12]))
    p2 = kp_mod.load_kp_prompts_v2()
    check(p2["sha256"] == sha1,
          "★ 管线读到的 prompt_sha256 与文件同步变化",
          "%s… → %s…" % (prompts["sha256"][:12], p2["sha256"][:12]))
    check(marker in p2["sys"], "管线读到的 SYS 块里含新字样")
    CAPTURED.clear()
    urllib.request.urlopen = _fake_urlopen
    try:
        c2: list = []
        kp_mod._v2_call(c2, "knowledge", p2["sys"], "【验证用】第二轮",
                        max_tokens=4096, retry=0, log=lambda s: None, run_no=2)
    finally:
        urllib.request.urlopen = _REAL_URLOPEN
    pay2 = CAPTURED[0] if CAPTURED else {}
    sys2 = ((pay2.get("messages") or [{}])[0] or {}).get("content", "")
    check(marker in sys2,
          "★★ 改完的提示词真的进了下一次调用的 payload（不是只改了文件）")
    # 还原：把原文写回去（同样留一份 .bak，可追溯）
    back = config.save_prompt_text(pf, original_md)
    check(sha256_file(pf) == sha0,
          "提示词已还原成改动前的字节", str(back.get("backup") or "")[-40:])

    # ---------------------------------------------------------------- ④ 入库识图
    emit("\n[4] 证据链③：入库识图开关 1 → 0 ⇒ 管线不再走识图那一步")
    probe_db = config.INDEX_DIR / "_c0059_probe.db"
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(probe_db) + suffix)
        if p.exists():
            p.unlink()
    mat = ROOT / "_smoke_fixtures" / "formats" / "数据结构-第五章.pdf"
    check(mat.is_file(), "测试材料存在（小 PDF）", str(mat))
    fig_calls = {"n": 0}
    orig_fig = fig_mod.ingest_document_figures

    def _stub_figures(*a, **kw):               # noqa: ANN001
        fig_calls["n"] += 1
        return {"pages": 0, "calls": 0, "cost_cny": 0.0, "texts": {},
                "skipped": 0, "errors": []}

    fig_mod.ingest_document_figures = _stub_figures
    old_env_key = os.environ.get("DEEPSEEK_API_KEY")
    os.environ.setdefault("DEEPSEEK_API_KEY", "sk-c0059-probe-not-a-real-key")
    try:
        # ---- (a) 关掉识图 + 关掉结构分析：两份都跑一次"真入库"（写临时库）----
        rep = dash.api_settings_save({"values": {"FIGURE_DESC": 0,
                                                 "OUTLINE_ANALYSIS": 0}})
        check(config.FIGURE_DESC == 0, "config.FIGURE_DESC 立刻变 0",
              str(config.FIGURE_DESC))
        fig_calls["n"] = 0
        c1 = db_mod.connect(probe_db)
        db_mod.init_db(c1)
        r1 = ingest_mod.ingest_file(c1, mat, force=True, with_ocr=False,
                                    with_figures=None)     # None = 走 config.FIGURE_DESC
        c1.close()
        n_off = fig_calls["n"]
        check(n_off == 0,
              "★ 关掉识图后：真入库一次都没调 `figures.ingest_document_figures`",
              "调用次数 %d，状态 %s" % (n_off, r1.get("status")))
        # ---- (b) 打开识图：同一个入口必须调它（反向对照，桩不发网络）----
        dash.api_settings_save({"values": {"FIGURE_DESC": 1}})
        check(config.FIGURE_DESC == 1, "config.FIGURE_DESC 立刻变回 1")
        fig_calls["n"] = 0
        c2c = db_mod.connect(probe_db)
        db_mod.init_db(c2c)
        r2 = ingest_mod.ingest_file(c2c, mat, force=True, with_ocr=False,
                                    with_figures=None)
        c2c.close()
        n_on = fig_calls["n"]
        check(n_on == 1,
              "★ 反向对照：打开识图后同一个入口**确实会**调它",
              "调用次数 %d，状态 %s" % (n_on, r2.get("status")))
        check(n_on > n_off,
              "开关真的在管线里分了岔（%d → %d）" % (n_off, n_on))
    finally:
        fig_mod.ingest_document_figures = orig_fig
        if old_env_key is None:
            os.environ.pop("DEEPSEEK_API_KEY", None)
        else:
            os.environ["DEEPSEEK_API_KEY"] = old_env_key
        for suffix in ("", "-wal", "-shm"):
            p = Path(str(probe_db) + suffix)
            if p.exists():
                try:
                    p.unlink()
                except OSError:
                    pass

    # ---------------------------------------------------------------- ⑤ 问答
    emit("\n[5] 证据链④：问答管线的识图 / 推理开关 ⇒ SSE 参数与证据过滤")
    from core.chat import Evidence

    def _ev(idx: int, fig: bool):
        return Evidence(idx=idx, chunk_id=idx, doc_id=1, doc_title="验证用资料",
                        doc_kind="courseware",
                        heading_path=("第一章 > 图示说明" if fig else "第一章 > 正文"),
                        source_ref="p.%d%s" % (idx, " 图" if fig else ""),
                        text=("图注文本 %d" % idx) if fig else ("正文文本 %d" % idx))

    fake_ev = [_ev(1, False), _ev(2, True), _ev(3, False)]
    orig_gather = chat_mod.gather_evidence
    orig_stream = chat_mod.stream_chat
    got: dict = {}

    def _fake_gather(conn, query, **kw):       # noqa: ANN001
        return [_ev(1, False), _ev(2, True), _ev(3, False)]

    def _fake_stream(messages, model=None, max_tokens=None, thinking=False,
                     timeout=None, temperature=None, reasoning_effort=None,
                     api_key=None):
        got.update({"model": model, "max_tokens": max_tokens, "thinking": thinking,
                    "reasoning_effort": reasoning_effort})
        yield ("delta", "（验证桩：不调模型）")
        yield ("usage", {"prompt_tokens": 3, "completion_tokens": 2})

    chat_mod.gather_evidence = _fake_gather
    chat_mod.stream_chat = _fake_stream
    try:
        dash.api_settings_save({"values": {"CHAT_FIGURES": 0, "CHAT_THINKING": 1,
                                           "CHAT_REASONING_EFFORT": "max",
                                           "CHAT_MODEL": "deepseek-flash"}})
        h = _FakeHandler()
        dash.api_chat_stream(h, {"query": "验证用问题"})
        ev = h.events()
        src = next((e for e in ev if e.get("type") == "sources"), {})
        evd = src.get("evidence") or []
        check(len(evd) == 2 and all(not x.get("is_figure") for x in evd),
              "★ 关掉识图后，图注证据被剔除（3 条 → 2 条）",
              "剩下 %d 条，is_figure=%s" % (len(evd), [x.get("is_figure") for x in evd]))
        check([x["idx"] for x in evd] == [1, 2],
              "剔除后角标重新编号（1..n，不会跳号）",
              str([x["idx"] for x in evd]))
        check(src.get("params", {}).get("figures") is False,
              "SSE sources.params 如实报出 figures=False",
              json.dumps(src.get("params"), ensure_ascii=False))
        check(got.get("reasoning_effort") == "max" and got.get("thinking") is True,
              "★ 推理开关与强度真的传进了 stream_chat",
              json.dumps(got, ensure_ascii=False))
        check(got.get("model") == "deepseek-flash",
              "模型名也按设置传进去", str(got.get("model")))
        # 反向对照：打开识图 ⇒ 图注证据回来
        dash.api_settings_save({"values": {"CHAT_FIGURES": 1}})
        h2 = _FakeHandler()
        dash.api_chat_stream(h2, {"query": "验证用问题"})
        evd2 = next((e for e in h2.events() if e.get("type") == "sources"),
                    {}).get("evidence") or []
        check(len(evd2) == 3 and sum(1 for x in evd2 if x.get("is_figure")) == 1,
              "★ 反向对照：打开识图后图注证据回来（3 条含 1 条图注）",
              str([x.get("is_figure") for x in evd2]))
    finally:
        chat_mod.gather_evidence = orig_gather
        chat_mod.stream_chat = orig_stream

    # ---------------------------------------------------------------- ⑥ 密钥
    emit("\n[6] 密钥安全：只给后 4 位、留空不覆盖")
    secret = "sk-FAKE-c0059-offline-NOT-A-REAL-KEY-3456"
    dash.api_settings_save({"values": {"CHAT_API_KEY": secret}})
    sh = [f for p in dash.api_settings()["pipelines"] for f in p["fields"]
          if f["key"] == "CHAT_API_KEY"][0]
    check(sh["value"] == "" and sh["set"] is True and sh["tail"] == secret[-4:],
          "★ 接口只回「已配置 + 后 4 位」，明文不回传",
          "value=%r set=%s tail=%s" % (sh["value"], sh["set"], sh["tail"]))
    check(secret not in json.dumps(dash.api_settings(), ensure_ascii=False),
          "★ 整个设置响应里都搜不到密钥明文")
    rep2 = dash.api_settings_save({"values": {"CHAT_API_KEY": ""}})
    check("CHAT_API_KEY" in (rep2.get("skipped") or []),
          "留空提交被识别为「不改动」", str(rep2.get("skipped")))
    check(config.value_of("CHAT_API_KEY") == secret,
          "★★ 留空提交后已有密钥**原样保留**（没被空串覆盖）",
          "尾号 %s" % config.key_tail("CHAT_API_KEY"))
    cfg_txt = Path(config.SETTINGS_FILE).read_text(encoding="utf-8")
    check(secret in cfg_txt,
          "密钥确实落在被 gitignore 的本地文件里",
          str(config.SETTINGS_FILE))
    dash.api_settings_reset({"keys": ["CHAT_API_KEY"]})

    # ---------------------------------------------------------------- ⑦ 副作用
    emit("\n[7] 零副作用：五表 / 真实配置 / 8756")
    five1 = {}
    dbhash1 = sha256_file(dbf)
    if dbf.is_file():
        c = ro_conn(dbf)
        five1 = five_counts(c)
        c.close()
    check(five0 == five1, "五张表（+正文三表）行数一行都没变",
          "%s → %s" % (json.dumps(five0, ensure_ascii=False),
                       json.dumps(five1, ensure_ascii=False)))
    # 库文件 sha256 只作参考：常驻的 8756 实例可能在两次取样之间做 WAL checkpoint，
    # 那是它自己的行为、与本脚本无关，所以这里不当判据（行数才是判据）。
    emit("  [info] 主库 sha256 %s… → %s…（%s）"
         % (dbhash0[:12] or "(无)", dbhash1[:12] or "(无)",
            "未变" if dbhash0 == dbhash1 else "变了（多半是常驻实例的 WAL checkpoint）"))
    check((not REAL_SETTINGS.exists()) == (not real_exists_before)
          and sha256_file(REAL_SETTINGS) == real_before,
          "★ 用户真实的 config.local.json 一个字节没动",
          "%s…" % (real_before[:12] or "(不存在)"))
    ok2 = not get_json(8756, "/api/stats").get("error")
    check(ok2 == ok8756, "8756 常驻实例状态未变（只探活，不碰它）",
          "可达" if ok2 else "不可达")
    check(sha256_file(pf) == sha0, "提示词真源回到原样")

    emit("\n" + "=" * 78)
    return 0 if BAD == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
