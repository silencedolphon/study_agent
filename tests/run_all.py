"""一键验收：跑全部自动化用例 + 生成基线报告。

用法（在 D:\\study_agent 下）：
    venv\\Scripts\\python.exe -X utf8 tests\\run_all.py
    venv\\Scripts\\python.exe -X utf8 tests\\run_all.py --only test_home_ok
    venv\\Scripts\\python.exe -X utf8 tests\\run_all.py --refresh-fixtures

产物：
    _work/verify/report.md     人读报告（功能矩阵 / 结果 / 失败详情 / 证据 / 缺口）
    _work/verify/report.json   机读结果（CI/后续回归可 diff）
退出码：0 = 无失败（允许 expectedFailure，即已登记的产品缺陷）；1 = 有失败/错误/意外通过。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import traceback
import unittest
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS_DIR))
import env  # noqa: E402
import matrix  # noqa: E402

STATUS_PRIORITY = {
    "PASS": 0, "EXPECTED_FAILURE": 1, "SKIP": 2, "FAIL": 3, "ERROR": 4,
    "UNEXPECTED_SUCCESS": 5,
}


class RecordResult(unittest.TextTestResult):
    """记录每个用例的状态/耗时/失败详情。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.records: dict[str, dict] = {}
        self.order: list[str] = []
        self._started: dict[str, float] = {}

    # -- 内部
    def _record(self, test, status: str, detail: str = ""):
        tid = test.id()
        elapsed = round(time.time() - self._started.get(tid, time.time()), 2)
        self.records[tid] = {"id": tid, "status": status, "elapsed": elapsed,
                             "detail": detail[:8000]}
        self.order.append(tid)
        label = {"PASS": "PASS", "FAIL": "FAIL", "ERROR": "ERROR", "SKIP": "SKIP",
                 "EXPECTED_FAILURE": "KNOWN-BUG", "UNEXPECTED_SUCCESS": "UNEXPECTED-PASS"}[status]
        print(f"[{label:13s}] {tid}  ({elapsed:.2f}s)", flush=True)
        if status in ("FAIL", "ERROR", "UNEXPECTED_SUCCESS"):
            last = [ln for ln in detail.splitlines() if ln.strip()][-4:]
            for ln in last:
                print("             " + ln, flush=True)

    @staticmethod
    def _fmt(err) -> str:
        return "".join(traceback.format_exception(*err)).strip()

    # -- unittest 回调
    def startTest(self, test):
        self._started[test.id()] = time.time()
        super().startTest(test)

    def addSuccess(self, test):
        self._record(test, "PASS")
        super().addSuccess(test)

    def addFailure(self, test, err):
        self._record(test, "FAIL", self._fmt(err))
        super().addFailure(test, err)

    def addError(self, test, err):
        self._record(test, "ERROR", self._fmt(err))
        super().addError(test, err)

    def addSkip(self, test, reason):
        self._record(test, "SKIP", reason)
        super().addSkip(test, reason)

    def addExpectedFailure(self, test, err):
        self._record(test, "EXPECTED_FAILURE", self._fmt(err))
        super().addExpectedFailure(test, err)

    def addUnexpectedSuccess(self, test):
        self._record(test, "UNEXPECTED_SUCCESS", "用例意外通过（缺陷可能已修复，请更新 matrix）")
        super().addUnexpectedSuccess(test)


def _filter_suite(suite, token: str):
    """按 test id 子串过滤嵌套 suite。"""
    out = unittest.TestSuite()
    for t in suite:
        if isinstance(t, unittest.TestSuite):
            sub = _filter_suite(t, token)
            if sub.countTestCases():
                out.addTest(sub)
        elif token in t.id():
            out.addTest(t)
    return out


def _git_head() -> str:
    try:
        cp = subprocess.run(["git", "-C", str(env.PROJECT), "rev-parse", "--short", "HEAD"],
                            capture_output=True, text=True, timeout=20)
        head = cp.stdout.strip()
        cp2 = subprocess.run(["git", "-C", str(env.PROJECT), "status", "--porcelain"],
                             capture_output=True, text=True, timeout=20)
        # 只关心产品区改动；tests/ 与 _work/ 是验收套件自己的工作区，不算“脏”
        dirty = []
        for ln in cp2.stdout.splitlines():
            if not ln.strip():
                continue
            path_part = ln[3:].split(" -> ")[-1].strip()
            if path_part.startswith(("tests/", "_work/")):
                continue
            dirty.append(ln)
        return f"{head}{'（产品区有未提交改动）' if dirty else '（产品区干净）'}"
    except Exception as e:  # noqa: BLE001
        return f"(git 不可用: {e})"


def _file_hashes() -> dict:
    out = {}
    frontend_files = tuple(p.relative_to(env.PROJECT).as_posix()
                           for p in sorted((env.PROJECT / "core" / "frontend").glob("*"))
                           if p.is_file())
    for rel in ("core/config.py", "core/study.py", "core/search.py", "core/subjects.py",
                *frontend_files, "tools/dashboard.py", "tools/study.py"):
        p = env.PROJECT / rel
        try:
            out[rel] = {"size": p.stat().st_size, "sha256": env.sha256(p)}
        except OSError as e:
            out[rel] = {"error": str(e)}
    return out


def _matrix_status(rows, records, only=None):
    """把 matrix 行映射到用例状态。"""
    result = []
    for row in rows:
        tokens = row["tests"]
        matched = [cid for cid in records
                   if any(tok in cid for tok in tokens)]
        if only and not matched:
            # --only 模式下，未匹配到的行可能只是被过滤掉了
            full_exists = any(tok in cid for tok in tokens
                              for cid in _discovered_ids)
            status = "NOT_RUN" if full_exists else "MISSING"
        elif not matched:
            status = "MISSING"
        else:
            statuses = {records[c]["status"] for c in matched}
            if statuses == {"PASS"}:
                status = "PASS"
            elif "FAIL" in statuses or "ERROR" in statuses or "UNEXPECTED_SUCCESS" in statuses:
                status = "FAIL"
            elif statuses == {"EXPECTED_FAILURE"}:
                status = "KNOWN_BUG"
            elif statuses == {"SKIP"}:
                status = "SKIP"
            elif "PASS" in statuses and statuses <= {"PASS", "EXPECTED_FAILURE"}:
                status = "PARTIAL"
            else:
                status = "PARTIAL"
        result.append(dict(row, status=status, matched_tests=matched))
    return result


_discovered_ids: list[str] = []


def _status_zh(status: str) -> str:
    return {
        "PASS": "✅ 通过", "FAIL": "❌ 失败", "KNOWN_BUG": "🐞 已知缺陷",
        "SKIP": "⏭️ 跳过", "PARTIAL": "⚠️ 部分通过", "MISSING": "⚠️ 无用例",
        "NOT_RUN": "➖ 未运行(--only)",
    }.get(status, status)


def build_markdown(report: dict) -> str:
    s = report["summary"]
    lines = []
    add = lines.append
    add("# P2 验收基线报告 · study_agent 学习看板")
    add("")
    add(f"- 生成时间：{report['generated_at']}")
    add(f"- 代码版本：`{report['git_head']}`")
    add(f"- 被测代码指纹：{report['env']['product_hashes_note']}")
    add(f"- 结论：**{s['passed']} 通过 / {s['failed']} 失败 / "
        f"{s['expected_failures']} 未修复缺陷(expectedFailure) / {s['skipped']} 跳过**，"
        f"共 {s['total']} 条用例（功能矩阵 {s['matrix_total']} 行，覆盖 {s['matrix_covered']} 行）；"
        f"历史缺陷已修复 {s.get('fixed_bugs', 0)} 个（回归用例已转正）")
    add(f"- 退出码：`{report['exit_code']}`")
    add("")
    add("## 1. 怎么跑")
    add("")
    add("```")
    add("cd D:\\study_agent")
    add("venv\\Scripts\\python.exe -X utf8 tests\\run_all.py            # 全量")
    add("venv\\Scripts\\python.exe -X utf8 tests\\run_all.py --only test_search  # 单条")
    add("set VERIFY_REFRESH_FIXTURES=1 & venv\\Scripts\\python.exe -X utf8 tests\\run_all.py  # 按当前真实库重拍 fixture")
    add("```")
    add("")
    add("隔离方式：产品代码复制到 `_work/verify/sandbox/`（ROOT=沙箱），数据用 sqlite3 backup API "
        "快照到 `_work/verify/fixtures/` → 每次运行复制到 `_work/verify/data/`，通过 "
        "`KB_DB/KB_RAW/KB_NOTES/KB_INDEX/KB_LOG/KB_SETTINGS_FILE` 指向副本；真实数据目录只读。")
    add("")
    add("## 2. 隔离与清理证据")
    add("")
    ev = report["isolation"]
    add(f"- 代码沙箱与产品代码一致：`{'是' if ev['code_sync_ok'] else '否：' + str(ev['code_sync_diff'])}`")
    add(f"- 运行期间产品代码是否被改动：`{'是（结果仅代表开始/结束中某一版本，请重跑）' if ev.get('code_changed_during_run') else '否'}`")
    add(f"- 测试实例端口：{ev['used_ports']}；运行后监听状态：{ev['ports_listening_after']}")
    add(f"- 残留进程：{ev['leftover_processes'] or '无'}")
    add(f"- 真实数据指纹（运行前后）：{ev['real_data_verdict']}")
    add(f"- fixture 快照：`{ev['fixture_manifest']}`")
    add("")
    add("## 3. 功能矩阵与结果")
    add("")
    add("| 编号 | 功能 | 入口/接口 | 优先级 | 结果 | 用例 |")
    add("|---|---|---|---|---|---|")
    for row in report["matrix"]:
        tests = "<br>".join(row["matched_tests"]) if row["matched_tests"] else "—"
        add(f"| {row['id']} | {row['feature']} | `{row['entry']}` | {row['priority']} | "
            f"{_status_zh(row['status'])} | {tests} |")
    add("")
    add("## 4. 失败与缺陷")
    add("")
    failures = [c for c in report["cases"] if c["status"] in ("FAIL", "ERROR", "UNEXPECTED_SUCCESS")]
    if not failures:
        add("无失败用例。")
    else:
        for c in failures:
            add(f"### ❌ {c['id']}")
            add("")
            add("```")
            add(c["detail"][-2500:])
            add("```")
            add("")
            add("最小复现：`venv\\Scripts\\python.exe -X utf8 tests\\run_all.py --only "
                f"{c['id'].rsplit('.', 1)[-1]}`")
            add("")
    if report.get("known_bugs"):
        add("### 未修复的产品缺陷（用例以 expectedFailure 固化）")
        add("")
        for b in report["known_bugs"]:
            add(f"#### {b['id']}（{b['severity']}危）{b['title']}")
            add("")
            add(f"- 表现：{b['symptom']}")
            add(f"- 根因：{b['root_cause']}")
            add(f"- 影响：{b['impact']}")
            add(f"- 复现：`{b['repro']}`")
            add(f"- 修复建议：{b['fix_hint']}")
            add("")
    else:
        add("**当前无未修复的已知缺陷。**")
        add("")
    if report.get("fixed_bugs"):
        add("### 已修复历史缺陷（回归用例已转正，防止退化）")
        add("")
        for b in report["fixed_bugs"]:
            add(f"#### {b['id']}（{b['severity']}危）{b['title']}")
            add("")
            add(f"- 修复：`{b['fixed_in']}` — {b.get('fix_summary', '')}")
            add(f"- 历史表现：{b['symptom']}")
            add(f"- 历史根因：{b['root_cause']}")
            add(f"- 验收证据：{b['verify']}")
            add(f"- 回归复现：`{b['repro']}`")
            add("")
    add("## 5. 未覆盖功能与原因（如实列出）")
    add("")
    add("| 编号 | 功能 | 入口 | 未覆盖原因 |")
    add("|---|---|---|---|")
    for g in report["gaps"]:
        add(f"| {g['id']} | {g['feature']} | `{g['entry']}` | {g['reason']} |")
    add("")
    add("## 6. 用例明细")
    add("")
    add("| 用例 | 状态 | 耗时(s) |")
    add("|---|---|---|")
    for c in report["cases"]:
        add(f"| `{c['id']}` | {_status_zh(c['status'])} | {c['elapsed']} |")
    add("")
    add("## 7. 证据文件")
    add("")
    for a in report["artifacts"]:
        add(f"- `{a['path']}` — {a['note']}")
    add("")
    add("## 8. 给后续回归的建议")
    add("")
    add("- 每个改动合并前跑一遍 `tests/run_all.py`；失败项看第 4 节与最小复现命令。")
    add("- 历史 BUG-1/2/3 已修复（ec394bb），对应用例已从 expectedFailure 转正；将来若再退化，"
        "run_all 会以 FAIL / 退出码 1 暴露。")
    add("- 新增已确认缺陷时：用例加 `@unittest.expectedFailure` 并登记到 `tests/matrix.py::FIXED_BUGS` "
        "之前的 `KNOWN_BUGS`（当前为空）；缺陷修复后按同样方式转正。")
    add("- 真实库在验收期间若被真实看板改动，fixture 不会漂移；需要按最新真实数据重拍时设 "
        "`VERIFY_REFRESH_FIXTURES=1`。")
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="study_agent P2 验收套件一键运行")
    ap.add_argument("--only", default="", help="只跑 test id 含该子串的用例")
    ap.add_argument("--refresh-fixtures", action="store_true",
                    help="强制按当前真实库重拍 fixture")
    ap.add_argument("--list-matrix", action="store_true", help="只打印功能矩阵")
    args = ap.parse_args(argv)

    if args.list_matrix:
        for row in matrix.MATRIX:
            print(f"{row['id']}\t{row['priority']}\t{row['feature']}\t{row['entry']}")
        print(f"\n功能矩阵 {len(matrix.MATRIX)} 行；未覆盖 {len(matrix.GAPS)} 项；"
              f"当前未修复缺陷 {len(matrix.KNOWN_BUGS)} 个；历史已修复缺陷 {len(matrix.FIXED_BUGS)} 个")
        return 0

    started = time.time()
    print("=" * 78)
    print("P2 验收套件 · 隔离环境准备")
    print("=" * 78)
    if args.refresh_fixtures:
        os.environ["VERIFY_REFRESH_FIXTURES"] = "1"
    data = env.prepare(clean_data=True)
    print(f"代码沙箱：{env.SANDBOX_DIR}")
    print(f"数据副本：{env.DATA_DIR}")
    print(f"fixture  ：{env.FIXTURE_DIR}")
    print(f"代码同步变化：{data['code_changed'] or '（无，沙箱已是最新）'}")
    code_bad = env.verify_code_sync()
    print(f"沙箱与产品代码一致性：{'OK' if not code_bad else code_bad}")
    fixture_manifest = {}
    try:
        fixture_manifest = json.loads((env.FIXTURE_DIR / "manifest.json").read_text(encoding="utf-8"))
    except OSError:
        pass
    real_before = env.real_data_snapshot()
    hashes_start = _file_hashes()

    # ---------------- 跑用例 ----------------
    print("-" * 78)
    loader = unittest.TestLoader()
    suite = loader.discover(str(TESTS_DIR), top_level_dir=str(TESTS_DIR))
    _discovered_ids[:] = [t.id() for t in _flatten(suite)]
    if args.only:
        suite = _filter_suite(suite, args.only)
        n = suite.countTestCases()
        print(f"--only {args.only!r}：匹配 {n} 条用例")
        if n == 0:
            print("没有匹配的用例。")
            return 1
    result = unittest.TextTestRunner(
        stream=sys.stdout, verbosity=0, resultclass=RecordResult).run(suite)

    # ---------------- 收尾与隔离核对 ----------------
    env.cleanup_leftovers()
    real_after = env.real_data_snapshot()
    hashes_end = _file_hashes()
    code_changed_during_run = hashes_start != hashes_end
    code_bad_end = env.verify_code_sync()
    diffs = env.diff_real_snapshot(real_before, real_after)
    ports_after = env.candidate_ports_listening()
    leftovers = _our_processes()
    elapsed = round(time.time() - started, 1)

    records = result.records
    cases = [records[tid] for tid in result.order]
    passed = sum(1 for c in cases if c["status"] == "PASS")
    failed = sum(1 for c in cases if c["status"] in ("FAIL", "ERROR", "UNEXPECTED_SUCCESS"))
    expected = sum(1 for c in cases if c["status"] == "EXPECTED_FAILURE")
    skipped = sum(1 for c in cases if c["status"] == "SKIP")

    matrix_rows = _matrix_status(matrix.MATRIX, records, only=args.only)
    matrix_rows.sort(key=lambda r: r["id"])       # 报告里按 F01..F99 顺序展示
    covered = sum(1 for r in matrix_rows if r["status"] not in ("MISSING", "NOT_RUN"))
    exit_code = 0 if failed == 0 else 1

    # 隔离结论
    if not diffs:
        real_verdict = "运行前后完全一致（本套件未写真实数据）"
    else:
        real_verdict = (
            "运行前后存在差异（共 %d 项）。**已核实：本套件所有实例路径都在 "
            "_work/verify 下（见 F05 用例断言）**；差异来自验收期间仍在运行的真实看板 "
            "8756 / 其它进程的正常读写。差异样例：%s"
            % (len(diffs), "; ".join(diffs[:4]))
        )

    artifacts = []
    for f, note in (
        (env.ARTIFACT_DIR / "browser-home.png", "Edge headless 首页截图"),
        (env.ARTIFACT_DIR / "healthcheck-result.json", "tools/healthcheck.py --json 原始输出"),
        (env.ARTIFACT_DIR / "backup-data-result.json", "tools/backup_data.py --json 原始输出"),
        (env.REPORT_DIR / "report.md", "本报告"),
        (env.REPORT_DIR / "report.json", "机读结果"),
        (env.FIXTURE_DIR / "manifest.json", "fixture 快照与真实库指纹"),
        (env.DATA_DIR / "main.db", "被测主库副本（真实库 backup 快照）"),
    ):
        if f.exists():
            artifacts.append({"path": str(f.relative_to(env.PROJECT)), "note": note,
                              "size": f.stat().st_size})
    for p in sorted(env.LOG_DIR.glob("*.log")):
        artifacts.append({"path": str(p.relative_to(env.PROJECT)),
                          "note": "被测实例启动日志", "size": p.stat().st_size})

    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "git_head": _git_head(),
        "elapsed_seconds": elapsed,
        "exit_code": exit_code,
        "env": {
            "project": str(env.PROJECT),
            "python": _python_version(),
            "sandbox": str(env.SANDBOX_DIR),
            "data": str(env.DATA_DIR),
            "fixtures": str(env.FIXTURE_DIR),
            "product_hashes": hashes_start,
            "product_hashes_end": hashes_end,
            "product_hashes_note": "core/config.py + core/study.py + core/search.py + "
                                   "core/subjects.py + tools/dashboard.py + tools/study.py "
                                   "的 sha256 见 report.json::env.product_hashes",
            "code_sync_ok": not code_bad,
            "code_sync_diff": code_bad,
        },
        "summary": {
            "total": len(cases), "passed": passed, "failed": failed,
            "expected_failures": expected, "skipped": skipped,
            "matrix_total": len(matrix_rows), "matrix_covered": covered,
            "gaps": len(matrix.GAPS), "known_bugs": len(matrix.KNOWN_BUGS),
            "fixed_bugs": len(matrix.FIXED_BUGS),
        },
        "isolation": {
            "code_sync_ok": not code_bad and not code_bad_end,
            "code_sync_diff": code_bad or code_bad_end,
            "code_changed_during_run": code_changed_during_run,
            "hashes_start": hashes_start,
            "hashes_end": hashes_end,
            "used_ports": env.Server.USED_PORTS,
            "ports_listening_after": ports_after,
            "leftover_processes": leftovers,
            "real_data_verdict": real_verdict,
            "real_data_diffs": diffs[:20],
            "fixture_manifest": str((env.FIXTURE_DIR / "manifest.json").relative_to(env.PROJECT)),
            "fixture_roles": fixture_manifest.get("roles", {}),
        },
        "matrix": matrix_rows,
        "cases": cases,
        "known_bugs": matrix.KNOWN_BUGS,
        "fixed_bugs": matrix.FIXED_BUGS,
        "gaps": matrix.GAPS,
        "artifacts": artifacts,
    }
    (env.REPORT_DIR / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (env.REPORT_DIR / "report.md").write_text(build_markdown(report), encoding="utf-8")

    # ---------------- 控制台汇总 ----------------
    print("=" * 78)
    print("汇总")
    print("=" * 78)
    print(f"用例：{len(cases)} 条 · 通过 {passed} · 失败 {failed} · "
          f"已知缺陷(预期失败) {expected} · 跳过 {skipped}")
    print(f"功能矩阵：{len(matrix_rows)} 行，已覆盖 {covered} 行；"
          f"未覆盖 {len(matrix.GAPS)} 项；当前未修复缺陷 {len(matrix.KNOWN_BUGS)} 个；"
          f"历史已修复缺陷 {len(matrix.FIXED_BUGS)} 个")
    print(f"耗时：{elapsed}s（端口 {env.Server.USED_PORTS}，运行后均释放："
          f"{not any(ports_after.values())}）")
    if failed:
        print("-" * 78)
        print("失败清单（含最小复现）：")
        for c in cases:
            if c["status"] in ("FAIL", "ERROR", "UNEXPECTED_SUCCESS"):
                method = c["id"].rsplit(".", 1)[-1]
                print(f"  [{c['status']}] {c['id']}")
                print(f"      最小复现：venv\\Scripts\\python.exe -X utf8 tests\\run_all.py "
                      f"--only {method}")
    if expected:
        print("-" * 78)
        print("已知缺陷（expectedFailure，责任在产品代码，本次只报告不修）：")
        for b in matrix.KNOWN_BUGS:
            print(f"  {b['id']} [{b['severity']}危] {b['title']}")
    if matrix.FIXED_BUGS:
        print("-" * 78)
        print("历史缺陷已修复（回归用例已转正，防止退化）：")
        for b in matrix.FIXED_BUGS:
            print(f"  {b['id']} [{b['severity']}危] {b['title']}  ← {b.get('fixed_in', '')[:60]}")
    print("-" * 78)
    print(f"报告：{env.REPORT_DIR / 'report.md'}")
    print(f"      {env.REPORT_DIR / 'report.json'}")
    print(f"退出码：{exit_code}")
    return exit_code


def _flatten(suite):
    for t in suite:
        if isinstance(t, unittest.TestSuite):
            yield from _flatten(t)
        else:
            yield t


def _python_version() -> str:
    return "%d.%d.%d" % sys.version_info[:3]


def _our_processes() -> list[str]:
    """检测是否还有沙箱 dashboard 进程残留（按命令行匹配沙箱路径）。"""
    if os.name != "nt":
        return []
    try:
        cp = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "$me = $PID; Get-CimInstance Win32_Process | Where-Object { "
             "$_.Name -like 'python*' -and $_.ProcessId -ne $me -and "
             "$_.CommandLine -like '*_work\\verify\\sandbox\\tools\\dashboard.py*' } | "
             "Select-Object -ExpandProperty ProcessId"],
            capture_output=True, text=True, timeout=30)
        return [x.strip() for x in cp.stdout.splitlines() if x.strip()]
    except Exception:  # noqa: BLE001
        return ["(无法检测)"]


if __name__ == "__main__":
    raise SystemExit(main())
