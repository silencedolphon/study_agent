# -*- coding: utf-8 -*-
"""C-0094 验收脚本：预习管线（独立管线分区，默认关闭）。

    G:\\study_agent\\venv\\Scripts\\python.exe -X utf8 G:\\study_agent\\tools\\verify_c0094_preview.py

**绝对路径、不依赖 cwd、不调模型（0 花费）**：全部判据要么读文件、要么读库、
要么用假 `chat_once` 打桩实测记账/互斥这类行为。结论行在第一行。

判据分五组（共 24 项）：
  A 代码与接线：模块/提示词/CLI/看板后端入口 + `config` 三处映射 + 设置中心字段；
  B 默认关闭：开关默认值 0、CLI/线程/看板三层入口都拒绝并给可操作提示；
  C 模型与强度：deepseek-flash + thinking 开 + reasoning_effort=high（**不是** max）；
  D 产物：必带 meta 字段与 sources、顶层无 `knowledge_points`、不进知识点索引、
    单章 1 次调用、节级细拆零模型调用、块引用可回查真库；
  E 红线：`core/kp_build.py` 一字节未动（sha256 对照）+ 既有知识点产物 sha256 不变 +
    记账与 `_v2_call` 字段同构 + 与整理任务互斥 + 长任务不进全局 `_lock`。
"""
from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

CARD = Path(r"C:\Users\Lenovo\.dsh\steward-lite\consoles\session-23a82a5f-fd66-4ae6-94c1-18f3e276c043\cards\C-0094")
DOC_ID = 91
CHAPTER = 1

_RESULTS: list[tuple[bool, str, str]] = []


def ok(cond, name: str, detail: str = "") -> bool:
    _RESULTS.append((bool(cond), name, str(detail)))
    return bool(cond)


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main() -> int:
    t0 = time.perf_counter()
    from core import config, kp_build, preview  # noqa: E402

    # ---------------- A 代码与接线 ----------------
    mod_p = ROOT / "core" / "preview.py"
    cli_p = ROOT / "tools" / "preview_build.py"
    dash_p = ROOT / "tools" / "dashboard.py"
    prompt_p = ROOT / "logs" / "knowledge" / "提示词-预习总结.md"

    ok(mod_p.exists() and "def build_preview(" in mod_p.read_text(encoding="utf-8"),
       "A1 core/preview.py 存在且定义 build_preview")
    ok(cli_p.exists(), "A2 tools/preview_build.py（CLI）存在")
    ok(prompt_p.exists()
       and "PROMPT-PREVIEW-SYS-BEGIN" in prompt_p.read_text(encoding="utf-8")
       and "PROMPT-PREVIEW-USER-BEGIN" in prompt_p.read_text(encoding="utf-8"),
       "A3 提示词真源存在且收发块齐备")

    pipes = [p["id"] for p in config._PIPELINES]
    ok("preview" in pipes, "A4 _PIPELINES 登记 preview", "pipelines=%s" % pipes)
    maps = (config._PIPE_BASE_KEY.get("preview"), config._PIPE_MODEL_KEY.get("preview"),
            config._PIPE_KEY_KEY.get("preview"))
    ok(all(maps) and all(m in config._FIELDS_BY_KEY for m in maps),
       "A5 三个落位映射齐备且键都在字段表里", "→".join(str(m) for m in maps))
    act = config.activate("preview")
    ok("error" not in act and act.get("pipeline") == "preview",
       "A6 config.activate('preview') 不再报未知管线", json.dumps(act, ensure_ascii=False))

    pay = config.settings_payload()
    pv = [x for x in pay["pipelines"] if x["id"] == "preview"]
    keys = [f["key"] for f in (pv[0]["fields"] if pv else [])]
    ok(bool(pv) and {"PREVIEW_ENABLED", "PREVIEW_MODEL", "PREVIEW_BASE_URL",
                     "PREVIEW_API_KEY", "PREVIEW_THINKING", "PREVIEW_REASONING_EFFORT",
                     "PREVIEW_MAX_TOKENS"} <= set(keys),
       "A7 设置中心数据驱动地出现这条管线（前端零改动）", "fields=%d" % len(keys))

    src = dash_p.read_text(encoding="utf-8")
    ok(all(("def %s(" % f) in src for f in ("api_preview_start", "api_preview_options",
                                            "api_preview_progress")),
       "A8 看板后端三个入口齐备")

    ds = src.index('"/api/preview/progress"')
    lock_at = src.index("            with _lock:")
    ok(ds < lock_at and 'if u.path == "/api/preview":' in src,
       "A9 预习的进度与启动都在全局 _lock **之外**",
       "progress@%d < _lock@%d" % (ds, lock_at))

    # ---------------- B 默认关闭 ----------------
    spec = config._FIELDS_BY_KEY["PREVIEW_ENABLED"]
    ok(spec["type"] == "bool" and int(spec["default"]) == 0,
       "B1 PREVIEW_ENABLED 默认 0（默认关闭）",
       "type=%s default=%s" % (spec["type"], spec["default"]))
    # 本机若真把开关打开了，这一项按运行值判——但**默认值**那条永远按 B1 判。
    enabled_now = preview.enabled()
    rep = preview.build_preview(DOC_ID, CHAPTER, log=lambda *a: None)
    ok((rep.get("ok") is False and "未开启" in str(rep.get("error")))
       if not enabled_now else bool(rep.get("ok")),
       "B2 关着时 build_preview 拒绝（并给可操作提示）",
       "enabled_now=%s error=%s" % (enabled_now, rep.get("error") or rep.get("status")))
    if not enabled_now:
        ok("设置中心" in str(rep.get("hint") or ""),
           "B3 拒绝时给出**可操作**提示（去哪儿打开）", str(rep.get("hint") or "")[:60])
    else:
        ok(True, "B3 拒绝时给出可操作提示（本机已开启 → 略）", "enabled=1")
    sb = preview.start_background(DOC_ID, CHAPTER)
    ok(sb.get("started") is False if not enabled_now else sb.get("started") is True,
       "B4 线程入口（看板路径）同样受开关管", json.dumps(sb, ensure_ascii=False)[:120])

    # ---------------- C 模型与强度 ----------------
    ok(str(config._FIELDS_BY_KEY["PREVIEW_MODEL"]["default"]) == "deepseek-flash",
       "C1 模型默认 deepseek-flash")
    th = config._FIELDS_BY_KEY["PREVIEW_THINKING"]
    ok(th["type"] == "bool" and int(th["default"]) == 1, "C2 thinking 默认开")
    eff = config._FIELDS_BY_KEY["PREVIEW_REASONING_EFFORT"]
    ok(str(eff["default"]) == "high" and "high" in eff["choices"],
       "C3 reasoning_effort 默认 high", "default=%s choices=%s" % (eff["default"], eff["choices"]))
    ok(str(config._FIELDS_BY_KEY["KP_BUILD_REASONING_EFFORT"]["default"]) == "max"
       and str(eff["default"]) != str(config._FIELDS_BY_KEY["KP_BUILD_REASONING_EFFORT"]["default"]),
       "C4 预习是 high、知识点那条仍是 max（两条管线各自独立）")
    ok(preview.model_name() == "deepseek-flash" and preview.effort() == "high"
       and preview.thinking_on() is True,
       "C5 运行时读到的模型/强度与设置一致",
       "%s/%s/%s" % (preview.model_name(), preview.thinking_on(), preview.effort()))

    # ---------------- D 产物 ----------------
    out = preview.preview_out_file(DOC_ID)
    raw = {}
    if out.exists():
        raw = json.loads(out.read_text(encoding="utf-8"))
    meta = raw.get("meta") if isinstance(raw.get("meta"), dict) else {}
    ok(out.exists() and bool(meta), "D1 产物存在且是 JSON 对象", str(out))
    need = ("doc_id", "chapter", "heading_path", "pages")
    ok(all(k in meta for k in need), "D2 meta 必带 doc_id/chapter/heading_path/pages",
       str({k: meta.get(k) for k in need})[:120])
    srcs = raw.get("sources") if isinstance(raw.get("sources"), list) else []
    ok(bool(srcs) and all(isinstance(s, dict) and "chunk_id" in s and "page" in s
                          for s in srcs),
       "D3 顶层 sources 每条都有 chunk_id/page", "%d 条" % len(srcs))
    ok("knowledge_points" not in raw, "D4 顶层**没有** knowledge_points 键")
    ok(preview.PREVIEW_SUFFIX not in ("", kp_build.KP_SUFFIX, kp_build.TERMS_SUFFIX)
       and preview.PREVIEW_SUFFIX.endswith(".json"),
       "D5 产物后缀不与知识点/名词解释相撞", preview.PREVIEW_SUFFIX)
    ok(str(meta.get("preview_version") or "") == preview.PREVIEW_VERSION
       and "pipeline_version" not in meta,
       "D6 用 preview_version 而非知识点体系的 pipeline_version",
       str(meta.get("preview_version")))

    # 0 次模型调用的节级细拆
    se = meta.get("sections_regex") if isinstance(meta.get("sections_regex"), dict) else {}
    ok(int(se.get("n_sections") or 0) > 0 and "0 次模型调用" in str(se.get("cost") or "")
       and bool(se.get("read")),
       "D7 节级细拆来自卡 C-0092 正则产物、**零模型调用**",
       "n_sections=%s source=%s" % (se.get("n_sections"), se.get("source")))
    ur = meta.get("usage_this_run") if isinstance(meta.get("usage_this_run"), dict) else {}
    ok(int(ur.get("calls") or 0) == 1 and int(meta.get("params", {}).get("calls_per_chapter") or 0) == 1,
       "D8 整章**只做 1 次**总结调用", "usage_this_run.calls=%s" % ur.get("calls"))
    ck = [c for c in (meta.get("calls") or []) if isinstance(c, dict)]
    ok(bool(ck) and all(c.get("model") == "deepseek-flash" and c.get("thinking") is True
                        and c.get("reasoning_effort") == "high" for c in ck),
       "D9 调用记录里逐次写着 deepseek-flash/thinking/high",
       json.dumps([{k: c.get(k) for k in ("run", "model", "thinking", "reasoning_effort",
                                          "tokens_in", "tokens_out", "cost_cny")}
                   for c in ck], ensure_ascii=False)[:200])

    # 块引用回查真库
    import sqlite3
    conn = sqlite3.connect("file:" + Path(config.DB_PATH).as_posix() + "?mode=ro", uri=True)
    try:
        ids = {int(s["chunk_id"]) for s in srcs}
        got = set()
        for cid in ids:
            row = conn.execute("SELECT doc_id, source_ref FROM chunks WHERE id=?", (cid,)).fetchone()
            if row and int(row[0]) == DOC_ID:
                got.add(cid)
        ok(ids and ids == got, "D10 sources 的 chunk_id 都能回查真库且属于本文档",
           "%d/%d" % (len(got), len(ids)))
        hp = conn.execute("SELECT heading_path FROM chunks WHERE id=?", (sorted(ids)[0],)).fetchone()
    finally:
        conn.close()
    ok(bool(hp) and str(meta.get("heading_path") or "") in str(hp[0] or ""),
       "D11 meta.heading_path 与库里该章块的 heading_path 一致",
       "%s ⊂ %s" % (meta.get("heading_path"), hp[0] if hp else ""))

    # 不进知识点索引（真的调看板的判据函数）
    sys.path.insert(0, str(ROOT / "tools"))
    import dashboard as dash  # noqa: E402
    kpdir = dash._kp_load_dir()
    polluted = [v.get("source") for v in kpdir.values()
                if preview.PREVIEW_SUFFIX in str(v.get("source") or "")]
    ok(not polluted and DOC_ID not in kpdir,
       "D12 预习产物**不会**被 _kp_load_dir 当成知识点产物",
       "污染来源=%s doc91_in_index=%s" % (polluted, DOC_ID in kpdir))
    ok(str(dash._lkp_index(DOC_ID).get("state") or "") == "none",
       "D13 知识点页签把 doc#91 判为未整理（预习产物不计数）",
       str(dash._lkp_index(DOC_ID).get("state")))

    # ---------------- E 红线 ----------------
    want = (CARD / "kp_build_sha.txt").read_text(encoding="utf-8").strip()
    now_sha = _sha(ROOT / "core" / "kp_build.py")
    ok(now_sha == want, "E1 core/kp_build.py 一字节未动（sha256 对照）",
       "%s vs %s" % (now_sha[:16], want[:16]))
    before_p = CARD / "sha_before.json"
    if before_p.exists():
        before = json.loads(before_p.read_text(encoding="utf-8"))
        changed = [k for k, v in before.items()
                   if not (ROOT / "logs" / "knowledge" / k).exists()
                   or _sha(ROOT / "logs" / "knowledge" / k) != v["sha256"]]
        kp_n = len([k for k in before if k.endswith(kp_build.KP_SUFFIX)])
        ok(not changed, "E2 既有 %d 份知识点产物 sha256 前后一致（含全部 json 产物）" % kp_n,
           "变了：%s" % changed if changed else "全部一致")
    else:
        ok(False, "E2 找不到基线 sha_before.json", str(before_p))

    # 记账与 `_v2_call` 字段同构 + 走进程级闸门（假 chat 打桩，0 花费）
    from core import chat as chat_mod
    orig = chat_mod.chat_once

    def _fake(messages, model="m", max_tokens=1, thinking=False, timeout=1,
              temperature=0.0, reasoning_effort=None):
        return {"content": '{"ok": true}', "model": model,
                "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
                "elapsed": 0.01}

    try:
        chat_mod.chat_once = _fake
        c1: list = []
        kp_build._v2_call(c1, "k", "s", "u", max_tokens=64, retry=0,
                          log=lambda *a: None, call_no=1)
        g0 = kp_build.model_gate_stats()["acquired"]
        c2: list = []
        preview._preview_call(c2, "k", "s", "u", max_tokens=64, retry=0,
                              log=lambda *a: None, model="deepseek-flash", timeout=1)
        g1 = kp_build.model_gate_stats()["acquired"]
        ok(c1 and c2 and set(c1[0]) == set(c2[0]),
           "E3 _preview_call 与 _v2_call 的记账字段**逐字同构**",
           "差集=%s" % sorted(set(c1[0]) ^ set(c2[0])))
        ok(g1 > g0 and c2[0].get("tokens_in") == 11 and c2[0].get("cost_cny", 0) > 0,
           "E4 预习调用走**进程级模型闸门** model_slot（记账口径不破）",
           "acquired %d→%d" % (g0, g1))
    finally:
        chat_mod.chat_once = orig

    # 与整理任务互斥
    orig_run = kp_build.is_running
    try:
        kp_build.is_running = lambda: True
        r2 = preview.start_background(DOC_ID, CHAPTER, ignore_switch=True)
        ok(r2.get("started") is False and "整理" in str(r2.get("error") or ""),
           "E5 整理任务在跑时预习拒绝启动（互斥）", str(r2.get("error"))[:60])
    finally:
        kp_build.is_running = orig_run

    # 提示词能逐字提取
    pr = preview.load_preview_prompts()
    ok(bool(pr.get("sys")) and bool(pr.get("user")) and len(pr.get("sha256") or "") == 64,
       "E6 提示词真源能被逐字提取（sys/user + sha256）",
       "sys=%d user=%d" % (len(pr.get("sys") or ""), len(pr.get("user") or "")))

    # 降级路径①：没有《章节细目》时，节级细拆如实降级为空（不报错、不编节）
    orig_sof = preview.sections_out_file
    try:
        preview.sections_out_file = lambda d: ROOT / "_tmp_c0094" / "不存在的章节细目.json"
        deg = preview.chapter_sections(DOC_ID, {"no": 1, "name": "绪论"})
    finally:
        preview.sections_out_file = orig_sof
    ok(deg.get("read") is False and int(deg.get("n_sections") or 0) == 0
       and deg.get("fallback") is True and "note" in deg,
       "E7 抽不出节的章**如实降级并统计**（不编节、不报错）",
       str(deg.get("note"))[:70])

    # 降级路径②：材料超限时按"丢图注 → 截断页"两级降级，且逐条留痕
    orig_cap = preview.MATERIAL_CHARS_MAX
    try:
        preview.MATERIAL_CHARS_MAX = 200
        _ch, _info, _mat, _inv, st2 = preview.read_chapter_ch(DOC_ID, CHAPTER)
    finally:
        preview.MATERIAL_CHARS_MAX = orig_cap
    ok(st2.get("mode") in ("drop_figures", "truncate_pages"),
       "E8 材料超限有降级且如实标注（mode/dropped_fig_blocks/truncated_pages）",
       "mode=%s dropped_fig=%s trunc_pages=%s"
       % (st2.get("mode"), st2.get("dropped_fig_blocks"), len(st2.get("truncated_pages") or [])))

    # ---------------- 结论 ----------------
    bad = [r for r in _RESULTS if not r[0]]
    n, m = len(_RESULTS) - len(bad), len(_RESULTS)
    if bad:
        print("C0094-FAIL %d/%d 项通过（失败 %d 项，用时 %.1fs）"
              % (n, m, len(bad), time.perf_counter() - t0))
    else:
        print("C0094-PASS %d/%d 项全部通过（用时 %.1fs；0 次模型调用、0 花费）"
              % (n, m, time.perf_counter() - t0))
    for i, (good_, name, detail) in enumerate(_RESULTS, 1):
        print("[%02d] %s %s%s" % (i, "OK  " if good_ else "FAIL", name,
                                  ("  ← %s" % detail) if detail and not good_ else ""))
        if detail and good_ and len(detail) <= 90:
            print("      %s" % detail)
    return 1 if bad else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001
        print("C0094-FAIL 验证脚本自身异常：%s: %s" % (type(e).__name__, e))
        raise SystemExit(2)
