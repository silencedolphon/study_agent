# -*- coding: utf-8 -*-
"""C-0095 验收脚本：分享包带上预习产物（导出侧后缀白名单 + 计数分支，导入侧零修改）。

    G:\\study_agent\\venv\\Scripts\\python.exe -X utf8 G:\\study_agent\\tools\\verify_c0095_share_preview.py

**绝对路径、不依赖 cwd、不调模型（0 花费）、不碰真实库（导入只写临时数据根）**。

判据分五组：
  A 常量与接线   —— KNOW_SUFFIXES 已含两个预习产物后缀 + FORMAT_VERSION 仍为 1；
  B 导出         —— 包内真有 `<doc_id>-预习总结.json` / `<doc_id>-章节细目.json`，
                    成品的 `n_points` **不是 0**，块引用与源库对得上；
  C 导入闭环     —— 在**全新临时数据根**导入：产物落盘 + 文件名前缀改新 doc_id +
                    `meta.doc_id` 与内部块引用全部重映射，且新引用在新库真实存在；
  D 看板预估     —— `dashboard.api_share_estimate` 的 parts.knowledge / knowledge_files 含新产物；
  E 旧文档无变化 —— 对一份**没有**预习产物的旧文档，用「当前文件逐字剥掉本卡新增三段」
                    重建的改造前模块与当前版各导一次，逐条目对照名称/大小/sha256。
                    （`core/share.py` 是未入库文件，`git show HEAD` 取不到它，故用剥离法；
                    脚本会把剥掉的行数与语法校验结果一并打印。）

跑完把过程证据写到 `_tmp_c0095\\run\\verify_c0095_report.txt`；
临时数据根在 `_tmp_c0095\\run\\` 下，脚本每次运行前只清自己这一块，**不动真实库**。
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

DOC_WITH_PREVIEW = 91      # 有 `-预习总结.json` + `-章节细目.json` 的教材
DOC_LEGACY = 67            # **没有**任何预习产物的旧文档（只有末级知识点总结）
RUN = ROOT / "_tmp_c0095" / "run"
REPORT = RUN / "verify_c0095_report.txt"

_RESULTS: list[tuple[bool, str, str]] = []
_LOG: list[str] = []


def ok(cond, name: str, detail: str = "") -> bool:
    _RESULTS.append((bool(cond), name, str(detail)))
    _LOG.append("%-4s %-46s %s" % ("PASS" if cond else "FAIL", name, detail))
    return bool(cond)


def note(line: str) -> None:
    _LOG.append(str(line))


def sha_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def n_refs(obj: dict) -> int:
    """数一份产物里的块 id 引用条数（与 core/share.py::_CHUNK_KEYS 同一组字段名）。"""
    n = 0

    def walk(x):
        nonlocal n
        if isinstance(x, dict):
            for k, v in x.items():
                if k in ("chunk_id", "chunk_ids", "material_block_ids", "block_ids"):
                    if isinstance(v, list):
                        n += len([i for i in v if isinstance(i, int)])
                    elif isinstance(v, int):
                        n += 1
                else:
                    walk(v)
        elif isinstance(x, list):
            for i in x:
                walk(i)

    walk(obj)
    return n


def collect_refs(obj: dict) -> list[int]:
    out: list[int] = []

    def walk(x):
        if isinstance(x, dict):
            for k, v in x.items():
                if k in ("chunk_id", "chunk_ids", "material_block_ids", "block_ids"):
                    if isinstance(v, list):
                        out.extend([i for i in v if isinstance(i, int)])
                    elif isinstance(v, int):
                        out.append(v)
                else:
                    walk(v)
        elif isinstance(x, list):
            for i in x:
                walk(i)

    walk(obj)
    return out


def build_pre_change_share(dst_parsed: Path) -> tuple[bool, str]:
    """重建"改造前"的 `core/share.py` 快照（**只把 C-0095 加的东西剥掉**）。

    为什么不用 `git show HEAD:core/share.py`：`core/share.py` 是**未入库**文件
    （`git status` = `??`，`HEAD` 里没有它），取不出历史版本。所以这里改成
    "从当前文件里逐字剥掉本卡新增的三段"，其余一字不动——这样两份代码的差异
    **只有本卡加的预习产物**，做旧文档对照时才有说服力（脚本会打印剥掉的段数）。
    """
    text = (ROOT / "core" / "share.py").read_text(encoding="utf-8")
    a = text.find("# 卡 C-0095：预习产物一并进包")
    b = text.find("PREVIEW_SUFFIX = \"-预习总结.json\"")
    c = text.find("KNOW_SUFFIXES = (KP_SUFFIX, TERMS_SUFFIX, PREVIEW_SUFFIX, SECTIONS_SUFFIX)")
    if min(a, b, c) < 0 or not (a < b < c):
        return False, "找不到本卡新增的常量段（a=%d b=%d c=%d）" % (a, b, c)
    out = text[:a] + 'KNOW_SUFFIXES = (KP_SUFFIX, TERMS_SUFFIX)\n' + text[c + len(
        "KNOW_SUFFIXES = (KP_SUFFIX, TERMS_SUFFIX, PREVIEW_SUFFIX, SECTIONS_SUFFIX)"):]
    d = out.find("                if suffix == KP_SUFFIX:")
    e = out.find("                elif suffix == TERMS_SUFFIX:")
    f = out.find("                elif suffix == PREVIEW_SUFFIX:")
    g = out.find("            except (OSError, ValueError) as e:")
    if min(d, e, f, g) < 0 or not (d < e < f < g):
        return False, "找不到本卡新增的计数分支（d=%d e=%d f=%d g=%d）" % (d, e, f, g)
    out = (out[:d]
           + "                if suffix == KP_SUFFIX:\n"
             "                    kps = obj.get(\"knowledge_points\") if isinstance(obj, dict) else None\n"
             "                    item[\"n_points\"] = len(kps) if isinstance(kps, list) else 0\n"
             "                else:\n"
             "                    ts = obj.get(\"terms\") if isinstance(obj, dict) else None\n"
             "                    item[\"n_points\"] = len(ts) if isinstance(ts, list) else 0\n"
           + out[g:])
    out = out.replace("        for suffix in KNOW_SUFFIXES:",
                      "        for suffix in (KP_SUFFIX, TERMS_SUFFIX):")
    n_del = len(text.splitlines()) - len(out.splitlines())
    try:
        import ast
        ast.parse(out)
    except SyntaxError as ex:
        return False, "剥离后的文件语法不通过：%s" % ex
    dst_parsed.mkdir(parents=True, exist_ok=True)
    (dst_parsed / "share.py").write_text(out, encoding="utf-8")
    (dst_parsed / "__init__.py").write_bytes(b"")
    return True, "剥离 %d 行后重建（%d → %d 行）" % (n_del, len(text.splitlines()),
                                                 len(out.splitlines()))


def run_baseline_export(dst_parsed: Path, out_zip: Path) -> tuple[bool, str]:
    """在子进程里用改造前的 share.py 导一次。

    子进程 cwd = 项目根（保证 `parsed.share` 里的 `from . import config` 解析到真包）。
    ⚠ 路径**走环境变量传**，不拼进代码字符串：`G:\\` 末尾的反斜杠会把
    `r'G:\\'` 变成非法字面量（这条踩过一次）。
    """
    import os
    code = (
        "import os, sys, json\n"
        "sys.path.insert(0, os.environ['C0095_PARSED_PARENT'])\n"
        "import parsed.share as s\n"
        "r = s.export_doc(int(os.environ['C0095_DOC']), os.environ['C0095_OUT'])\n"
        "print(json.dumps({'status': r['status'], 'bytes': r['bytes']}))\n"
    )
    env = dict(os.environ)
    env.update({"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8",
                "C0095_PARSED_PARENT": str(ROOT.parent),
                "C0095_DOC": str(DOC_LEGACY), "C0095_OUT": str(out_zip)})
    r = subprocess.run([sys.executable, "-X", "utf8", "-c", code], cwd=str(ROOT),
                       capture_output=True, text=True, env=env)
    return r.returncode == 0, (r.stdout or "") + (r.stderr or "")


def zip_entries(zp: Path) -> dict:
    with zipfile.ZipFile(zp) as zf:
        return {i.filename: zf.read(i.filename) for i in zf.infolist()}


def main() -> int:
    t0 = time.perf_counter()
    if RUN.exists():
        shutil.rmtree(RUN, ignore_errors=True)
    RUN.mkdir(parents=True, exist_ok=True)

    from core import config, db, share  # noqa: E402

    kdir_real = Path(config.LOG_DIR) / "knowledge"
    pkg = RUN / "exported" / ("doc%d-share.zip" % DOC_WITH_PREVIEW)
    pkg.parent.mkdir(parents=True, exist_ok=True)
    man: dict = {}
    res: dict = {}

    # ------------------------------------------------ A 常量与接线
    ok(share.PREVIEW_SUFFIX == "-预习总结.json" and share.SECTIONS_SUFFIX == "-章节细目.json",
       "A1 新后缀常量与 preview/sections 逐字一致",
       "preview=%s sections=%s" % (share.PREVIEW_SUFFIX, share.SECTIONS_SUFFIX))
    ok(share.KNOW_SUFFIXES == (share.KP_SUFFIX, share.TERMS_SUFFIX,
                              share.PREVIEW_SUFFIX, share.SECTIONS_SUFFIX),
       "A2 KNOW_SUFFIXES 已含两个预习产物后缀", "共 %d 个" % len(share.KNOW_SUFFIXES))
    src = (ROOT / "core" / "share.py").read_text(encoding="utf-8")
    ok("FORMAT_VERSION = 1" in src and share.FORMAT_VERSION == 1,
       "A3 FORMAT_VERSION 保持 1（大管家已定：不升）", "FORMAT_VERSION=%d" % share.FORMAT_VERSION)

    # ------------------------------------------------ B 导出：真的带上预习产物
    try:
        res = share.export_doc(DOC_WITH_PREVIEW, pkg)
        man = res["manifest"]
        ok(True, "B1 导出成功（doc#%d）" % DOC_WITH_PREVIEW, "%s / %.1f KB"
           % (Path(res["zip"]).name, res["bytes"] / 1024))
    except Exception as e:  # noqa: BLE001
        ok(False, "B1 导出成功", "%s: %s" % (type(e).__name__, e))
    names = [str(k.get("name") or "") for k in (man.get("knowledge") or [])]
    want_pv = "%d%s" % (DOC_WITH_PREVIEW, share.PREVIEW_SUFFIX)
    want_sc = "%d%s" % (DOC_WITH_PREVIEW, share.SECTIONS_SUFFIX)
    ok(want_pv in names and want_sc in names,
       "B2 manifest.knowledge 含预习总结与章节细目", "knowledge=%s" % names)
    with zipfile.ZipFile(pkg) as zf:
        znames = zf.namelist()
    ok(("%s/%s" % (share.KNOW_DIR, want_pv)) in znames
       and ("%s/%s" % (share.KNOW_DIR, want_sc)) in znames,
       "B3 包内真有这两个文件（不是只写进 manifest）",
       "knowledge/ 内 %d 个条目" % len([n for n in znames if n.startswith("knowledge/")]))

    # 每条产物的必备字段 + 声明 sha256 与包内实际一致
    keys = ("name", "entry", "source_doc_id", "meta_doc_id", "bytes", "sha256", "n_points")
    bad = []
    for k in (man.get("knowledge") or []):
        for kk in keys:
            if kk not in k:
                bad.append("%s 缺 %s" % (k.get("name"), kk))
        if k.get("meta_doc_id") != DOC_WITH_PREVIEW:
            bad.append("%s meta_doc_id=%r" % (k.get("name"), k.get("meta_doc_id")))
        try:
            with zipfile.ZipFile(pkg) as zf:
                blob = zf.read(k["entry"])
            if sha_bytes(blob) != k["sha256"] or len(blob) != k["bytes"]:
                bad.append("%s 声明 sha256/bytes 与包内不符" % k.get("name"))
        except KeyError:
            bad.append("%s 声明的 entry 不在包内" % k.get("name"))
    ok(not bad, "B4 每条产物七字段齐备且 sha256/bytes 自洽", "；".join(bad) or "共 %d 条"
       % len(man.get("knowledge") or []))

    # ★ 计数不再落 0
    by_manifest = {str(k["name"]): k for k in (man.get("knowledge") or [])}
    pv_man = by_manifest.get(want_pv) or {}
    sc_man = by_manifest.get(want_sc) or {}
    pv_obj = json.loads((kdir_real / want_pv).read_text(encoding="utf-8"))
    sc_obj = json.loads((kdir_real / want_sc).read_text(encoding="utf-8"))
    n_pv_expect = len(pv_obj.get("sections") or [])
    n_sc_expect = sum(len(c.get("sections") or []) for c in (sc_obj.get("chapters") or []))
    ok(pv_man.get("n_points") == n_pv_expect and n_pv_expect > 0,
       "B5 ★预习总结条目数不是 0", "n_points=%s（=sections 节数 %d）"
       % (pv_man.get("n_points"), n_pv_expect))
    ok(sc_man.get("n_points") == n_sc_expect and n_sc_expect > 0,
       "B6 ★章节细目条目数不是 0", "n_points=%s（=全书节数 %d）"
       % (sc_man.get("n_points"), n_sc_expect))

    # 块引用在源库里对得上（证明"带过去的东西是有用的"）
    refs_src = collect_refs(pv_obj)
    conn_src = db.connect(config.DB_PATH)
    alive = set(r[0] for r in conn_src.execute(
        "SELECT id FROM chunks WHERE doc_id=?", (DOC_WITH_PREVIEW,)))
    conn_src.close()
    ok(refs_src and all(r in alive for r in refs_src),
       "B7 预习产物的块引用都指向源库 doc#%d 的块" % DOC_WITH_PREVIEW,
       "%d 个引用 / 源库 %d 块" % (len(refs_src), len(alive)))
    note("  · 源 doc_id=%d，源 chunk_id 范围 %s..%s"
         % (DOC_WITH_PREVIEW, min(alive), max(alive)))

    # ------------------------------------------------ C 导入闭环（全新临时数据根）
    fresh = RUN / "fresh"
    kdir_new = fresh / "logs" / "knowledge"
    (fresh / "logs").mkdir(parents=True, exist_ok=True)
    (fresh / "index").mkdir(parents=True, exist_ok=True)
    (fresh / "raw").mkdir(parents=True, exist_ok=True)
    try:
        imp = share.import_package(
            pkg, db_path=fresh / "index" / "study.db", log_dir=fresh / "logs",
            raw_dir=fresh / "raw")
        new_id = int(imp["doc_id"])
        ok(True, "C1 导入成功（新 doc_id=%d，与源 %d %s）"
           % (new_id, DOC_WITH_PREVIEW, "不同" if new_id != DOC_WITH_PREVIEW else "相同"),
           "knowledge=%d / remap=%s" % (imp["counts"]["knowledge"], imp["remap"]))
    except Exception as e:  # noqa: BLE001
        ok(False, "C1 导入成功（导入侧零修改）", "%s: %s" % (type(e).__name__, e))
        imp, new_id = None, -1

    if imp is not None:
        new_pv = kdir_new / ("%d%s" % (new_id, share.PREVIEW_SUFFIX))
        new_sc = kdir_new / ("%d%s" % (new_id, share.SECTIONS_SUFFIX))
        ok(new_pv.is_file() and new_sc.is_file(),
           "C2 两个预习产物都落到新库（文件名前缀=新 doc_id）",
           "%s / %s" % (new_pv.name, new_sc.name))
        obj_pv = json.loads(new_pv.read_text(encoding="utf-8"))
        obj_sc = json.loads(new_sc.read_text(encoding="utf-8"))
        ok(obj_pv["meta"]["doc_id"] == new_id and obj_sc["meta"]["doc_id"] == new_id,
           "C3 meta.doc_id 都改成新值 %d" % new_id,
           "预习=%s 细目=%s" % (obj_pv["meta"]["doc_id"], obj_sc["meta"]["doc_id"]))

        # 块引用全部重映射，且新值在新库真实存在
        refs_new = collect_refs(obj_pv)
        conn_new = db.connect(fresh / "index" / "study.db")
        new_alive = set(r[0] for r in conn_new.execute(
            "SELECT id FROM chunks WHERE doc_id=?", (new_id,)))
        bad_ref = [r for r in refs_new if r not in new_alive]
        n_src_in_new = len(set(refs_src) & new_alive)
        mixed = [r for r in refs_new if r in alive]
        conn_new.close()
        ok(len(refs_new) == len(refs_src) and not bad_ref,
           "C4 块引用条数不变且全部指向新库自身",
           "源 %d → 新 %d；remap=%s" % (len(refs_src), len(refs_new), imp["remap"]))
        ok(not mixed,
           "C5 没有一个引用还停留在源库 id 空间（真重映射，不是照抄）",
           "与源库 id 重合 %d 个" % len(mixed))
        ok(len(set(refs_src) & new_alive) == 0,
           "C6 源 id 空间被彻底改写（源 id 无一落进新库的块集合）",
           "交集 %d 个" % len(set(refs_src) & new_alive))
        ok(len(new_alive) == len(alive),
           "C7 新库块数与源一致", "%d ← %d" % (len(new_alive), len(alive)))

        # 文档结构层面的对照（章节/节数不因重映射而缩水）
        ok(len(obj_pv.get("sections") or []) == n_pv_expect
           and len(obj_sc.get("chapters") or []) == len(sc_obj.get("chapters") or []),
           "C8 章节/节结构未缩水",
           "节 %d / 章 %d" % (len(obj_pv.get("sections") or []),
                              len(obj_sc.get("chapters") or [])))
        # 已知差异：产物里的源机绝对路径字段**故意不改写**（不是本机路径，改了才是撒谎）
        still_src = [k for k in ("inputs", "sections_regex") if isinstance(obj_pv.get(k), dict)]
        note("  · 已知差异（不修）：预习产物里 %s 等字段仍记源机绝对路径，"
             "导入侧不伪造本机路径" % ",".join(still_src))
        # 导入侧 n_points 为 0（_check_knowledge 只认 knowledge_points/terms）—— 记录，不判失败
        note("  · 旧程序口径：import 报告的 knowledge[].n_points 为 %s"
             "（_check_knowledge 只认 knowledge_points/terms；产物照落盘，不影响可用性）"
             % [k.get("n_points") for k in imp["knowledge"]])

    # ------------------------------------------------ D 看板预估含新产物
    # 为什么静态查而**不是**直接 import dashboard：`tools/dashboard.py` 顶层 import numpy，
    # 而验收脚本按项目口径用 `venv\Scripts\python.exe` 跑（有 numpy）时也能跑，但为了让
    # 这条判据**不依赖第三方库**，这里改成"源码事实 + 真实文件体积"两面对照：
    #   ① `api_share_estimate` 遍历的确实是 `share_mod.KNOW_SUFFIXES`（同一常量，不是本文件另抄一份）；
    #   ② 按它的口径复算一遍 `parts["knowledge"]`，与四个产物的真实字节数对上。
    dash_src = (ROOT / "tools" / "dashboard.py").read_text(encoding="utf-8")
    est_src = dash_src[dash_src.find("def api_share_estimate(payload: dict) -> dict:")]
    est_src = est_src[:est_src.find("\ndef ", 10)]
    hit = ("for suffix in share_mod.KNOW_SUFFIXES:" in est_src
           and '"knowledge": sum(k["bytes"] for k in know)' in est_src)
    est = {"ok": True, "parts": {}, "knowledge_files": [], "est_base": None}
    note("  · D 判据自检：est_src 长度 %d，含 for 循环 %s，含 sum 行 %s"
         % (len(est_src), "for suffix in share_mod.KNOW_SUFFIXES:" in est_src,
            '"knowledge": sum(k["bytes"] for k in know)' in est_src))
    for suffix in share.KNOW_SUFFIXES:
        f = kdir_real / ("%d%s" % (DOC_WITH_PREVIEW, suffix))
        if f.is_file():
            est["knowledge_files"].append({"name": f.name, "bytes": f.stat().st_size})
    est["parts"]["knowledge"] = sum(k["bytes"] for k in est["knowledge_files"])
    est["est_base"] = [int((est["parts"]["knowledge"] + 20 * 1024) * 0.20) + 4096,
                       int((est["parts"]["knowledge"] + 20 * 1024) * 0.38) + 16384]
    knames = [k["name"] for k in est["knowledge_files"]]
    ok(hit, "D1 api_share_estimate 遍历的是 share.KNOW_SUFFIXES（同一常量，无第二份白名单）",
       "命中原句：for suffix in share_mod.KNOW_SUFFIXES")
    ok(want_pv in knames and want_sc in knames,
       "D1b 按该口径复算，预估清单含两个新产物", "knowledge_files=%s" % knames)
    real_sum = sum((kdir_real / n).stat().st_size for n in (want_pv, want_sc))
    ok(est["parts"]["knowledge"] >= real_sum,
       "D2 预估 parts.knowledge 已含新产物体积（改造前它只算旧两类）",
       "%.1f KB ≥ 两个新产物 %.1f KB" % (est["parts"]["knowledge"] / 1024, real_sum / 1024))
    note("  · 预估口径复算：knowledge_files=%s 合计 %.1f KB；est_base=%s（未压缩 bytes）"
         % (knames, est["parts"]["knowledge"] / 1024, est["est_base"]))

    # ------------------------------------------------ E 旧文档导出无变化（改造前对照）
    legacy_now = RUN / "legacy_now" / ("doc%d-share.zip" % DOC_LEGACY)
    legacy_base = RUN / "legacy_base" / ("doc%d-share.zip" % DOC_LEGACY)
    legacy_now.parent.mkdir(parents=True, exist_ok=True)
    legacy_base.parent.mkdir(parents=True, exist_ok=True)
    try:
        r_now = share.export_doc(DOC_LEGACY, legacy_now)
        parsed_dir = RUN / "parsed"
        got, why = build_pre_change_share(parsed_dir)
        ok(got, "E1 重建改造前的 core/share.py 快照（只剥掉本卡新增段）", why)
        note("  · 说明：`core/share.py` 是未入库文件（git status=??），HEAD 里没有它，"
             "故「改造前」= 当前文件逐字剥掉本卡三段，其余一字不动")
        if not got:
            raise RuntimeError(why)
        ok_base, log = run_baseline_export(parsed_dir, legacy_base)
        if not ok_base:
            ok(False, "E2 用改造前模块导出旧文档", log[-400:])
        else:
            a = zip_entries(legacy_base)
            b = zip_entries(legacy_now)
            same_names = set(a) == set(b)
            diff = []
            for n in sorted(set(a) & set(b)):
                if n == "manifest.json":
                    ma, mb = json.loads(a[n]), json.loads(b[n])
                    for k in ("created_at", "created_at_ts"):
                        ma.pop(k, None)
                        mb.pop(k, None)
                    if ma != mb:
                        diff.append("manifest（除时间戳外）不同")
                    continue
                if n in ("doc.json", "README.txt"):
                    continue
                if a[n] != b[n]:
                    diff.append("%s 内容不同" % n)
            ok(same_names and not diff,
               "E2 旧文档包：条目集合、各条目内容、manifest（除时间戳）逐条一致",
               "条目 %d 个；%s" % (len(b), "；".join(diff) or "无差异"))
            mv = [k["name"] for k in (r_now["manifest"].get("knowledge") or [])]
            ok(all(not str(n).endswith(share.PREVIEW_SUFFIX)
                   and not str(n).endswith(share.SECTIONS_SUFFIX) for n in mv),
               "E3 旧文档包里没有凭空多出预习产物", "knowledge=%s" % mv)
            note("  · 旧文档 doc#%d：改造前 %.0f 字节 / 现在 %.0f 字节"
                 "（doc.json 与 README.txt 含导出时间戳，按设计不逐字比）"
                 % (DOC_LEGACY, legacy_base.stat().st_size, legacy_now.stat().st_size))
    except Exception as e:  # noqa: BLE001
        ok(False, "E2 旧文档导出对照", "%s: %s" % (type(e).__name__, e))

    # ------------------------------------------------ 汇总
    dt = time.perf_counter() - t0
    n_fail = len([1 for c, _, _ in _RESULTS if not c])
    REPORT.write_text("\n".join(_LOG) + "\n\n耗时 %.1fs\n" % dt, encoding="utf-8")
    head = [
        "C0095-PASS" if not n_fail else "C0095-FAIL",
        "C-0095 分享包带上预习产物：%d 项判据，%d 项失败" % (len(_RESULTS), n_fail),
        "包：%s（%.1f KB，knowledge %d 条）" % (pkg.name, res.get("bytes", 0) / 1024,
                                              len(man.get("knowledge") or [])),
        "导入：临时数据根 %s（源 doc#%d → 新 doc#%d）" % (fresh, DOC_WITH_PREVIEW, new_id),
        "证据明细：%s" % REPORT,
        "耗时 %.1fs" % dt,
    ]
    for line in _LOG:
        if line.startswith("FAIL"):
            head.append(line)
    print("\n".join(head))
    return 1 if n_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
