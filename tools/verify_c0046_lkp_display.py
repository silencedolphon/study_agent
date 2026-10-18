"""卡 C-0046 验证：看板展示层对齐 C-0045「父节点页下放」新口径。

验的是四件事（对应卡上的验收条目）：
 1. `/api/mindmap?doc_id=5` 把「知识点条数」与「挂载节点数」两个口径**分开**给出，
    新增的覆盖率字段是 pages_all=34 / n_covered=34 / overlap_pages=[]；
 2. **旧口径产物不受影响**：fixture（真产物去掉 `meta.coverage`/`meta.parent_nodes`）
    在新代码与 `git HEAD` 版代码下的返回**逐字节相同**，且不冒出新键、不显示 0/0；
 3. 页面信息条文案（真浏览器，`--with-browser`）：doc#5 含「12 条」「34/34」、
    不含「已就绪 23 / 12」、不含「不建知识点」；旧口径页与改动前**逐字相同**；
 4. 全程**零模型调用**：只读 JSON + 只读 HTTP，本脚本不 import core.kp_build，
    也不调任何 /api/kp-build 路由。

用法：
    venv\\Scripts\\python.exe -X utf8 tools\\verify_c0046_lkp_display.py
    venv\\Scripts\\python.exe -X utf8 tools\\verify_c0046_lkp_display.py --with-browser \\
        --evidence-dir <卡目录>

⚠ 本脚本**不碰 8756**：它自己在空闲端口起临时实例，跑完全部收掉。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DASH = ROOT / "tools" / "dashboard.py"
BASELINE = ROOT / "tools" / "_c0046_baseline_dashboard.py"   # git HEAD 版，跑完删
KNOWLEDGE = ROOT / "subjects" / "有机化学" / "logs" / "knowledge"
TARGET_DOC = 5            # 卡 C-0045 的试点产物（pipeline_version=C-0045-v1）
NEW_KEYS = ("lkp_coverage", "lkp_downshift", "lkp_parent_nodes")

OK: list[str] = []
BAD: list[str] = []


def check(cond: bool, desc: str, detail: str = "") -> bool:
    (OK if cond else BAD).append(desc if cond else f"{desc}｜{detail}")
    print(("  [OK]   " if cond else "  [FAIL] ") + desc + ("" if cond else f"  ← {detail}"))
    return bool(cond)


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def wait_ready(port: int, timeout: float = 40.0) -> bool:
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/api/mindmap", timeout=3) as r:
                if r.status == 200:
                    return True
        except Exception:
            time.sleep(0.4)
    return False


def api(port: int, path: str) -> dict:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


class Instance:
    """在临时端口起一个看板；可选把 `_knowledge_dir()` 换成别的目录（fixture）。"""

    def __init__(self, script: Path, *, knowledge_dir: Path | None = None):
        self.script = script
        self.knowledge_dir = knowledge_dir
        self.port = free_port()
        self.tmp = Path(tempfile.mkdtemp(prefix="c0046_runner_"))
        self.proc: subprocess.Popen | None = None

    def start(self) -> "Instance":
        patch = ""
        if self.knowledge_dir is not None:
            # 看板启动时 subjects.restore() 会把 config.LOG_DIR 重绑到真学科目录，
            # 环境变量 KB_KNOWLEDGE_DIR 会被清掉（见 core/subjects._bind）。
            # 所以 fixture 只能**换成函数**：_lkp_load_dir() 走的是模块全局查找。
            patch = (f"m._knowledge_dir = lambda: Path(r'{self.knowledge_dir}')\n"
                     f"print('[fixture] knowledge_dir ->', r'{self.knowledge_dir}', flush=True)\n")
        runner = self.tmp / "run_dash.py"
        runner.write_text(
            "import importlib.util, sys\n"
            "from pathlib import Path\n"
            f"spec = importlib.util.spec_from_file_location('dash_under_test', r'{self.script}')\n"
            "m = importlib.util.module_from_spec(spec)\n"
            "sys.modules['dash_under_test'] = m\n"
            "spec.loader.exec_module(m)\n"
            f"{patch}"
            f"sys.argv = ['dashboard.py', '--port', '{self.port}', '--no-open']\n"
            "raise SystemExit(m.main())\n",
            encoding="utf-8")
        self.log = open(self.tmp / "server.log", "wb")
        self.proc = subprocess.Popen([sys.executable, "-X", "utf8", str(runner)],
                                     cwd=str(ROOT), stdout=self.log, stderr=subprocess.STDOUT)
        if not wait_ready(self.port):
            tail = ""
            try:
                tail = (self.tmp / "server.log").read_text(encoding="utf-8", errors="replace")[-800:]
            except OSError:
                pass
            raise RuntimeError(f"实例未就绪（port={self.port}）：{tail}")
        return self

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        try:
            self.log.close()
        except Exception:
            pass
        shutil.rmtree(self.tmp, ignore_errors=True)


def load_meta(name: str) -> dict:
    return (json.loads((KNOWLEDGE / name).read_text(encoding="utf-8"))
            .get("meta") or {})


def doc_meta_map() -> dict[int, dict]:
    """扫真产物目录：{doc_id: {"file","has_coverage","meta"}}。"""
    out: dict[int, dict] = {}
    for p in sorted(KNOWLEDGE.glob("*-末级知识点总结.json")):
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        meta = raw.get("meta") or {}
        try:
            did = int(meta.get("doc_id"))
        except (TypeError, ValueError):
            continue
        out[did] = {"file": p.name, "has_coverage": isinstance(meta.get("coverage"), dict),
                    "meta": meta, "n_kp": len(raw.get("knowledge_points") or [])}
    return out


def count_leaf_ready(tree: list[dict]) -> tuple[int, int, int]:
    """遍历返回的树，独立数一遍：末级节点数 / 挂上总结的末级节点数 / 父节点页数。"""
    leaf = ready = parent = 0

    def walk(nodes: list[dict]) -> None:
        nonlocal leaf, ready, parent
        for n in nodes:
            if n.get("lkp_role") == "leaf":
                leaf += 1
                ready += 1 if n.get("lkp_ready") else 0
            elif n.get("lkp_role") == "parent_page":
                parent += 1
            walk(n.get("children") or [])

    walk(tree)
    return leaf, ready, parent


def main() -> int:
    ap = argparse.ArgumentParser(description="卡 C-0046 验证")
    ap.add_argument("--with-browser", action="store_true",
                    help="额外用 headless Edge 抠信息条 DOM 文本（需 node）")
    ap.add_argument("--evidence-dir", default="",
                    help="浏览器证据（JSON + 截图）写到这个目录")
    ap.add_argument("--json-out", default="", help="本次验证汇总写到这里")
    args = ap.parse_args()

    insts: list[Instance] = []
    fixture_root: Path | None = None
    summary: dict = {"targetDoc": TARGET_DOC}
    try:
        metas = doc_meta_map()
        print(f"\n=== 0. 产物目录：{KNOWLEDGE}")
        print(f"    共 {len(metas)} 份产物；带 meta.coverage（C-0045 口径）的有 "
              f"{sum(1 for m in metas.values() if m['has_coverage'])} 份")

        # ---------------- 1. 新口径：doc#5 的接口口径 ----------------
        print("\n=== 1. /api/mindmap?doc_id=5（C-0045 新口径）")
        inst_main = Instance(DASH).start()
        insts.append(inst_main)
        d5 = api(inst_main.port, f"/api/mindmap?doc_id={TARGET_DOC}")
        meta5 = load_meta(f"{TARGET_DOC}-末级知识点总结.json")
        cov5 = meta5.get("coverage") or {}
        pn5 = meta5.get("parent_nodes") or []

        leaf, ready, parent = count_leaf_ready(d5.get("tree") or [])
        check(d5.get("lkp_total") == meta5.get("kp_actual") == len(
            json.loads((KNOWLEDGE / f"{TARGET_DOC}-末级知识点总结.json")
                       .read_text(encoding="utf-8")).get("knowledge_points") or []),
              "「知识点条数」lkp_total 与产物 knowledge_points 条数一致（12）",
              f"lkp_total={d5.get('lkp_total')} kp_actual={meta5.get('kp_actual')}")
        check(leaf == ready == d5.get("lkp_ready") == d5.get("lkp_leaf_nodes") == 23,
              "「挂载节点数」lkp_ready 与树上实数的末级节点数一致（23）",
              f"lkp_ready={d5.get('lkp_ready')} leaf={leaf} ready={ready}")
        check(d5.get("lkp_total") != d5.get("lkp_ready"),
              "两个口径**可区分**（条数 12 ≠ 节点数 23，旧文案会把它们并成一个分数）",
              f"{d5.get('lkp_total')} vs {d5.get('lkp_ready')}")
        check(parent == d5.get("lkp_parent_pages") == 11,
              "父节点页节点数 11（lkp_parent_pages 与树上实数一致）",
              f"{parent} / {d5.get('lkp_parent_pages')}")
        check(d5.get("lkp_coverage") == {"pages_all": 34, "n_covered": 34, "overlap_pages": []},
              "覆盖率字段 pages_all=34 / n_covered=34 / overlap_pages=[]",
              json.dumps(d5.get("lkp_coverage"), ensure_ascii=False))
        check(d5.get("lkp_coverage", {}).get("pages_all") == cov5.get("pages_all")
              and d5.get("lkp_coverage", {}).get("n_covered") == cov5.get("n_covered")
              and (cov5.get("overlap_pages") or []) == [],
              "覆盖率与产物 meta.coverage 同源（不是看板自己算的）")
        check(d5.get("lkp_parent_nodes") == len(pn5) == 11 and d5.get("lkp_downshift") is True,
              "parent_nodes 条数 11 + lkp_downshift=True（前端据此切下放话术）",
              f"lkp_parent_nodes={d5.get('lkp_parent_nodes')} downshift={d5.get('lkp_downshift')}")
        summary["doc5"] = {k: d5.get(k) for k in
                           ("lkp_total", "lkp_ready", "lkp_leaf_nodes", "lkp_parent_pages",
                            "lkp_parent_nodes", "lkp_downshift", "lkp_coverage",
                            "lkp_excluded_reason")}

        # ---------------- 2. 旧口径 fixture：新旧代码逐字节对照 ----------------
        print("\n=== 2. 旧口径产物（fixture）：新旧代码返回必须完全一致")
        fixture_root = Path(tempfile.mkdtemp(prefix="c0046_fixture_"))
        old_metas = {k: v for k, v in metas.items() if not v["has_coverage"]}
        copied = 0
        for did, v in old_metas.items():
            shutil.copy2(KNOWLEDGE / v["file"], fixture_root / v["file"])
            copied += 1
        if copied == 0:
            # 万一并行会话已把旧产物全升成 C-0045：退化成"去掉新键"的等价形态，
            # 判据只看 meta.coverage 在不在，形态与真旧产物一致。
            for did, v in list(metas.items())[:6]:
                raw = json.loads((KNOWLEDGE / v["file"]).read_text(encoding="utf-8"))
                raw.get("meta", {}).pop("coverage", None)
                raw.get("meta", {}).pop("parent_nodes", None)
                (fixture_root / v["file"]).write_text(
                    json.dumps(raw, ensure_ascii=False), encoding="utf-8")
                copied += 1
            print(f"    ⚠ 真目录里已没有 C-0035-v2 产物，改用等价形态 {copied} 份")
        fixture_docs = sorted(doc_meta_map_in(fixture_root))
        print(f"    fixture: {fixture_root}（{copied} 份旧口径产物，doc_id={fixture_docs[:8]}…）")

        old_doc = fixture_docs[0]
        for extra in (10, 2, 15):
            if extra in fixture_docs:
                old_doc = extra
                break

        subprocess.run(["git", "show", f"HEAD:tools/dashboard.py"],
                       cwd=str(ROOT), stdout=open(BASELINE, "wb"), check=True)
        inst_base = Instance(BASELINE, knowledge_dir=fixture_root).start()
        insts.append(inst_base)
        inst_new_fx = Instance(DASH, knowledge_dir=fixture_root).start()
        insts.append(inst_new_fx)

        base_snap, new_snap = {}, {}
        for did in fixture_docs:
            base_snap[did] = api(inst_base.port, f"/api/mindmap?doc_id={did}")
            new_snap[did] = api(inst_new_fx.port, f"/api/mindmap?doc_id={did}")
        check(all(v.get("has_outline") for v in new_snap.values()),
              f"fixture 的 {len(fixture_docs)} 份文档都能出导图（对照才有意义）")
        same = [d for d in fixture_docs if base_snap[d] == new_snap[d]]
        check(len(same) == len(fixture_docs),
              "旧口径产物：新代码返回与 git HEAD 版**逐字节相同**"
              f"（{len(same)}/{len(fixture_docs)} 份）",
              "差异文档：" + str([d for d in fixture_docs if base_snap[d] != new_snap[d]]))
        for did in fixture_docs[:3]:
            diff_keys = set(base_snap[did]) ^ set(new_snap[did])
            check(not diff_keys, f"doc#{did} 返回键集合无增减（无新增报错字段）",
                  str(diff_keys))
        check(not any(k in new_snap[old_doc] for k in NEW_KEYS),
              f"旧口径 doc#{old_doc} 不含 lkp_coverage / lkp_downshift / lkp_parent_nodes"
              "（前端因此整行不渲染覆盖率，不会显示 0/0）",
              str([k for k in NEW_KEYS if k in new_snap[old_doc]]))
        check(bool(new_snap[old_doc].get("lkp_excluded_pages"))
              and bool(new_snap[old_doc].get("lkp_excluded_reason")),
              f"旧口径 doc#{old_doc} 的下放字段原样保留（前端走老话术）",
              f"pages={new_snap[old_doc].get('lkp_excluded_pages')}")
        summary["backcompat"] = {
            "fixture_docs": len(fixture_docs), "identical": len(same),
            "old_doc_probed": old_doc,
            "old_doc_total": new_snap[old_doc].get("lkp_total"),
            "old_doc_ready": new_snap[old_doc].get("lkp_ready"),
        }

        # ---------------- 3. 真目录里现存的旧口径文档（同一进程内对照） ----------------
        # ⚠ 卡 C-0047 正在并行把这些产物逐份升成 C-0045 口径：文件随时会被改写。
        # 所以这里**只对三次采样都稳定的文件**下断言，正在被改写的如实报"跳过"，
        # 不把并行状态误判成本卡的问题（causality：阶段 2 的冻结副本已经证明兼容）。
        print("\n=== 3. 真目录里现存的 C-0035-v2 产物（顺带核一遍，跳开正在改写的）")
        inst_base_real = Instance(BASELINE).start()
        insts.append(inst_base_real)

        def _sig() -> dict:
            out = {}
            for d, v in doc_meta_map().items():
                if v["has_coverage"]:
                    continue
                try:
                    st = (KNOWLEDGE / v["file"]).stat()
                    out[d] = (st.st_mtime_ns, st.st_size)
                except OSError:
                    pass
            return out

        s0 = _sig()
        base_real = {d: api(inst_base_real.port, f"/api/mindmap?doc_id={d}") for d in s0}
        s1 = _sig()
        new_real = {d: api(inst_main.port, f"/api/mindmap?doc_id={d}") for d in s0}
        s2 = _sig()
        stable = [d for d in s0 if s0[d] == s1.get(d) == s2.get(d)]
        diff = [d for d in stable if base_real[d] != new_real[d]]
        if stable:
            check(not diff, f"真目录 {len(stable)} 份**未被并行改写**的旧口径产物新旧返回一致",
                  f"差异：{diff}")
        else:
            print("    （真目录的旧口径产物都在被并行改写，本轮无可对照项——"
                  "兼容性由阶段 2 的冻结副本负责证明）")
        summary["real_old_docs"] = {"still_old": sorted(s0), "stable": sorted(stable),
                                    "skipped_racing": sorted(set(s0) - set(stable))}

        # ---------------- 4. 真浏览器：信息条 DOM 文案 ----------------
        if args.with_browser:
            print("\n=== 4. headless Edge 真浏览器：抠信息条 DOM 文本")
            ev = Path(args.evidence_dir) if args.evidence_dir else Path(
                tempfile.mkdtemp(prefix="c0046_ev_"))
            ev.mkdir(parents=True, exist_ok=True)
            targets = [f"{inst_main.port}:{TARGET_DOC}",
                       f"{inst_new_fx.port}:{old_doc}",
                       f"{inst_base.port}:{old_doc}"]
            cmd = ["node", str(ROOT / "tools" / "check_c0046_lkp_bar.mjs")]
            for t in targets:
                cmd += ["--target", t]
            cmd += ["--outdir", str(ev / "shots"), "--json", str(ev / "bar.json"),
                    "--cdp", str(free_port())]
            (ev / "bar.json").unlink(missing_ok=True)   # 别读到上一轮的残留
            r = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True,
                               encoding="utf-8", timeout=180)
            check(r.returncode == 0, "浏览器探针跑通（node 退出码 0）",
                  (r.stderr or "")[-400:])
            bar = json.loads((ev / "bar.json").read_text(encoding="utf-8"))["targets"]
            by = {(t["port"], t["doc"]): t for t in bar}
            new5 = by[(str(inst_main.port), str(TARGET_DOC))]
            newold = by[(str(inst_new_fx.port), str(old_doc))]
            baseold = by[(str(inst_base.port), str(old_doc))]

            check(new5["picked"].get("ok") and new5["dom"]["hasTree"],
                  "doc#5 导图页渲染出来（下拉选中 + 树挂上）", str(new5["picked"]))
            bt = new5["dom"]["barText"]
            check("知识点 12 条" in bt and "已挂到 23 个末级节点" in bt,
                  "doc#5 信息条：知识点 12 条 · 已挂到 23 个末级节点", bt[:120])
            check("覆盖 34/34 页" in bt, "doc#5 信息条：覆盖 34/34 页", bt)
            check("无跨条重复计数" in bt and "下放 11 页" in bt,
                  "doc#5 信息条：无跨条重复计数 · 下放 11 页")
            check("已就绪" not in bt and "23 / 12" not in bt and "12 / 23" not in bt,
                  "doc#5 信息条不含旧口径「已就绪 23 / 12」")
            check("不建知识点" not in bt and "不建知识点" not in new5["dom"]["cardText"],
                  "doc#5 导图卡可见文本不再出现「不建知识点」（改为下放语义）")
            check("已下放给紧随其后的末级页" in bt.split("\n")[-1],
                  "doc#5 信息条末行用的是产物自带的 meta.excluded_reason（下放原文）",
                  bt.split("\n")[-1][:120])
            check("父节点页：本页不单独成条" in new5["dom"]["firstParentTitle"],
                  "父节点页节点的 title 改成下放语义",
                  new5["dom"]["firstParentTitle"])
            pop_txt = new5["popup"].get("text") or ""
            check(new5["popup"].get("ok") and "下放" in pop_txt
                  and "不建知识点" not in pop_txt,
                  "点父节点页节点弹出的说明是下放语义", pop_txt[:150])
            check(not new5["errors"], "doc#5 页面无 JS 报错", str(new5["errors"]))

            # ⚠ 旧口径页面有**一处有意差异**（卡 C-0046 第 1 点）：lkpLine 那一行。
            # 老文案 `末级知识点总结：已就绪 47 / 22 个` 把「挂载节点数」当分子、
            # 「知识点条数」当分母——旧产物同样会写出"分子大于分母"（doc#8 实测
            # 47 个节点 / 22 条）。卡要求"分子分母必须同口径"，所以这一行统一换成
            # `知识点 N 条 · 已挂到 M 个末级节点`：两个数字都在，信息不减。
            # 除这一行外，其余每一行必须与改动前**逐字相同**。
            nb = newold["dom"]["barText"].split("\n")
            bb = baseold["dom"]["barText"].split("\n")
            others_same = (len(nb) == len(bb)
                           and all(nb[i] == bb[i] for i in range(len(nb)) if i != 1))
            check(others_same,
                  f"旧口径 doc#{old_doc}：除 lkpLine 那一行外，信息条**逐字相同**",
                  f"NEW={nb}\nBASE={bb}")
            check(len(nb) > 1 and "条" in nb[1] and "已挂到" in nb[1]
                  and "个末级节点" in nb[1],
                  f"旧口径 doc#{old_doc} 的 lkpLine 也是同口径写法（唯一的有意差异）",
                  nb[1] if len(nb) > 1 else "")
            # 顶部说明是**静态模板**（渲染时还不知道是哪种口径），本卡把它改成
            # 对两种口径都成立的中性表述（不提"下放"、也不提"不建知识点"）。
            # 所以旧口径卡文本**只允许在这一段不同**：从信息条首行往后的东西
            # （含 lkpLine 之后的 excluded 行与整棵导图树）必须与改动前逐字相同。
            def _tail(card: str, bar_first: str) -> list[str]:
                return card.split(bar_first, 1)[-1].split("\n")

            nt = _tail(newold["dom"]["cardText"], nb[0])
            bt2 = _tail(baseold["dom"]["cardText"], bb[0])
            check(len(nt) == len(bt2) and all(nt[i] == bt2[i]
                                              for i in range(len(nt)) if i != 1),
                  f"旧口径 doc#{old_doc}：信息条以下的文本（含导图树）与改动前逐字相同",
                  f"NEW={nt}\nBASE={bt2}")
            head_new = newold["dom"]["cardText"].split(nb[0], 1)[0]
            check("不建知识点" not in head_new and "0/0" not in head_new,
                  "旧口径卡顶部通用说明（信息条之前）改中性后不含「不建知识点」、也没有 0/0",
                  head_new)
            # 那一行换了说法，但**两个数字一个都不能丢**：条数、节点数都还在。
            nums_old = re.findall(r"\d+", bb[1]) if len(bb) > 1 else []
            check(len(nb) > 1 and bool(nums_old) and all(n in nb[1] for n in nums_old),
                  f"旧口径 doc#{old_doc} lkpLine 换说法后数字没丢（{nums_old}）",
                  f"BASE={bb[1] if len(bb) > 1 else ''}｜NEW={nb[1] if len(nb) > 1 else ''}")
            obt = newold["dom"]["barText"]
            check("覆盖" not in obt,
                  f"旧口径 doc#{old_doc} 整条不出现覆盖率行（后端不给 lkp_coverage）", obt)
            check("下放" not in obt and "按当前口径不建知识点" in obt,
                  f"旧口径 doc#{old_doc} 仍走老话术（没说成下放）", obt)
            check(newold["popup"].get("text", "").find("不建知识点") >= 0
                  and "下放" not in newold["popup"].get("text", ""),
                  f"旧口径 doc#{old_doc} 点父节点页弹窗仍是老说法",
                  (newold["popup"].get("text") or "")[:150])
            check(not newold["errors"], "旧口径页面无 JS 报错", str(newold["errors"]))
            summary["browser"] = {
                "evidence": str(ev), "doc5Bar": bt,
                "oldDoc": old_doc, "oldDocBar": obt,
                "oldBarSameAsBaseline": newold["dom"]["barText"] == baseold["dom"]["barText"],
                "shots": [t.get("screenshot") for t in bar],
            }
            print(f"    证据目录：{ev}")
            if args.evidence_dir:
                (Path(args.evidence_dir) / "verify-summary.json").write_text(
                    json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        else:
            print("\n=== 4. 浏览器验证：跳过（未给 --with-browser）")

    except Exception as e:  # 任何异常都算验证失败，但要把已跑过的结论带出去
        import traceback
        traceback.print_exc()
        BAD.append(f"验证脚本异常：{e!r}")
    finally:
        for i in insts:
            try:
                i.stop()
            except Exception:
                pass
        try:
            if BASELINE.exists():
                BASELINE.unlink()
        except OSError:
            pass
        if fixture_root is not None:
            shutil.rmtree(fixture_root, ignore_errors=True)

    print("\n" + "=" * 62)
    print(f"通过 {len(OK)} 项，失败 {len(BAD)} 项")
    for b in BAD:
        print("  ✗ " + b)
    head = "C0046 验证全部通过" if not BAD else f"C0046 验证失败 {len(BAD)} 项"
    print(head)
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(
            {"head": head, "ok": OK, "bad": BAD, "summary": summary},
            ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if not BAD else 1


def doc_meta_map_in(d: Path) -> dict[int, dict]:
    """扫任意目录里的产物：{doc_id: meta}。"""
    out: dict[int, dict] = {}
    for p in sorted(d.glob("*-末级知识点总结.json")):
        try:
            meta = (json.loads(p.read_text(encoding="utf-8")).get("meta") or {})
            out[int(meta.get("doc_id"))] = meta
        except (OSError, ValueError, TypeError):
            continue
    return out


if __name__ == "__main__":
    raise SystemExit(main())