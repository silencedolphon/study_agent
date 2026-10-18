"""会话接棒工具：给"跨多个会话的长期改进"提供**磁盘上的记忆**。

背景：对话上下文会被压缩/清空，跨天跨周开发时不可能靠"记得"。
本工具把「现在到哪一步了」变成一条 30 秒可读完的简报，并支持生成交接卡。

用法::

    # 1）新会话开场：先读简报（建议每次新会话第一条命令）
    venv\\Scripts\\python.exe -X utf8 tools\\session_brief.py

    # 2）一段工作结束时：生成交接卡（自动填入 git / 计划进度 / 环境事实）
    venv\\Scripts\\python.exe -X utf8 tools\\session_brief.py --checkpoint

    # 生成到指定目录（默认 docs/交接/）
    ... --checkpoint --out-dir _work\\handoff

简报内容：git 状态、计划进度、最近交接卡、运行中服务、最近验收结果、数据备份、下一步任务。
"""
from __future__ import annotations

import argparse
import json
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
HANDOFF_DIR = DOCS / "交接"


def sh(cmd: list[str], timeout: float = 30.0) -> str:
    try:
        p = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
        return (p.stdout or "").strip() + (("\n" + p.stderr.strip()) if p.stderr.strip() else "")
    except Exception as e:  # noqa: BLE001
        return f"<ERR {type(e).__name__}: {e}>"


def latest_plan() -> Path | None:
    files = sorted(DOCS.glob("长期改进计划-*.md"))
    return files[-1] if files else None


def plan_progress(plan: Path | None) -> dict:
    """解析计划里的「状态跟踪表」：| M0-1 | 任务 | M0 | 待办 | ... |

    同一个任务 ID 在 §3 的 WBS 表与附录的「状态跟踪表」各出现一次：后者才是
    进度真值（有状态列），所以按 ID 去重、**后者覆盖前者**。
    """
    out = {"total": 0, "done": 0, "doing": 0, "todo": 0, "next": [], "path": str(plan or "")}
    if not plan or not plan.exists():
        return out
    text = plan.read_text(encoding="utf-8", errors="replace")
    rows = re.findall(r"^\|\s*(M[0-3]-\d+)\s*\|([^|]*)\|([^|]*)\|([^|]*)\|", text, re.M)
    latest: dict[str, tuple[str, str, str]] = {}
    for tid, task, stage, status in rows:
        latest[tid.strip()] = (task.strip(), stage.strip(), status.strip())
    for tid in sorted(latest, key=lambda x: (x[:2], int(re.sub(r"\D", "", x.split("-")[1]) or 0))):
        task, stage, status = latest[tid]
        out["total"] += 1
        if "完成" in status or "✅" in status:
            out["done"] += 1
        elif "进行" in status or "🚧" in status:
            out["doing"] += 1
        else:
            out["todo"] += 1
            if len(out["next"]) < 5:
                out["next"].append(f"{tid} {task} [{stage}]")
    return out


def latest_handoff() -> Path | None:
    if not HANDOFF_DIR.exists():
        return None
    files = sorted((p for p in HANDOFF_DIR.glob("*.md")
                    if p.name not in ("README.md", "_模板.md")))
    return files[-1] if files else None


def listening(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.4)
        return s.connect_ex(("127.0.0.1", port)) == 0


def last_verify() -> str:
    rp = ROOT / "_work" / "verify" / "report.md"
    if not rp.exists():
        return "（还没有验收报告）"
    text = rp.read_text(encoding="utf-8", errors="replace")
    m = re.search(r"结论：\*\*([^*]+)\*\*", text)
    when = time.strftime("%m-%d %H:%M", time.localtime(rp.stat().st_mtime))
    summary = m.group(1).replace("\n", " ") if m else "见报告"
    return f"{summary}（{when}，_work/verify/report.md）"


def last_backup() -> str:
    dirs = sorted((ROOT / "_recovery").glob("data-backup-*"))
    if not dirs:
        return "（无）"
    d = dirs[-1]
    size = sum(p.stat().st_size for p in d.rglob("*") if p.is_file())
    return f"{d.name}（{size / 1048576:.1f} MB）"


def build_brief() -> str:
    head = sh(["git", "log", "--oneline", "-5"])
    status = sh(["git", "status", "--porcelain"])
    dirty = len([ln for ln in status.splitlines() if ln.strip()])
    branch = sh(["git", "rev-parse", "--abbrev-ref", "HEAD"]) or "?"
    plan = latest_plan()
    pp = plan_progress(plan)
    ho = latest_handoff()
    services = [f"{p}={'UP' if listening(p) else 'down'}" for p in
                (8756, 3080, 8901, 8902, 8903)]

    lines: list[str] = []
    lines.append("=" * 72)
    lines.append("study_agent 接棒简报 · " + time.strftime("%Y-%m-%d %H:%M"))
    lines.append("=" * 72)
    lines.append(f"仓库     : {ROOT}（分支 {branch}，未提交 {dirty} 项）")
    lines.append(f"计划文件 : {plan.name if plan else '（缺失）'}"
                 f"  进度 {pp['done']}/{pp['total']} 完成、{pp['doing']} 进行中、{pp['todo']} 待办")
    lines.append(f"最近交接 : {ho.name if ho else '（还没有交接卡）'}")
    lines.append(f"服务     : {' '.join(services)}")
    lines.append(f"最近验收 : {last_verify()}")
    lines.append(f"数据备份 : {last_backup()}")
    lines.append("")
    lines.append("— 最近 5 次提交 —")
    lines.append(head or "（无提交）")
    lines.append("")
    lines.append("— 下一步任务（取自计划状态表） —")
    for x in pp["next"] or ["（状态表里没有待办任务，检查计划表是否已更新）"]:
        lines.append("  [" + x + "]")
    lines.append("")
    lines.append("— 未提交改动 —")
    if dirty:
        for ln in status.splitlines()[:15]:
            lines.append("  " + ln)
    else:
        lines.append("  （工作区干净）")
    lines.append("")
    lines.append("开场必做：读 docs/交接 最新卡 → 跑 tools/healthcheck.py → "
                 "选计划里一个任务开工 → 完成后更新计划状态表并 commit")
    return "\n".join(lines)


def build_checkpoint(out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    head = sh(["git", "rev-parse", "--short", "HEAD"]) or "nohead"
    stamp = time.strftime("%Y-%m-%d-%H%M")
    path = out_dir / f"{stamp}-{head}.md"
    plan = latest_plan()
    pp = plan_progress(plan)
    status = sh(["git", "status", "--porcelain"])
    services = [f"{p}={'UP' if listening(p) else 'down'}" for p in (8756, 3080)]
    body = f"""# 交接卡 · {time.strftime('%Y-%m-%d %H:%M')}

> 由 `tools/session_brief.py --checkpoint` 生成。**下一会话先读这张卡 + 最新计划状态表。**

## 1. 当前状态（自动采集）
- git HEAD：`{head}`，未提交改动 {len([x for x in status.splitlines() if x.strip()])} 项
- 计划进度：{pp['doing']} 进行中 / {pp['todo']} 待办 / 共 {pp['total']}
- 服务：{' '.join(services)}
- 最近验收：{last_verify()}
- 数据备份：{last_backup()}

## 2. 本次会话做了什么（人工填写）
- 

## 3. 没做完 / 下一步（人工填写，写可执行的动作）
- 

## 4. 关键决策与理由（人工填写；写"为什么"而不是"做了什么"）
- 

## 5. 踩坑 / 注意事项（人工填写，含复现命令）
- 

## 6. 证据位置（截图/报告/API 输出）
- 

## 7. 下一会话开场命令
```powershell
cd D:\\study_agent
venv\\Scripts\\python.exe -X utf8 tools\\session_brief.py     # 先看简报
venv\\Scripts\\python.exe -X utf8 tools\\healthcheck.py       # 再确认环境
```
"""
    path.write_text(body, encoding="utf-8")
    return path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="study_agent 会话接棒简报/交接卡")
    ap.add_argument("--checkpoint", action="store_true", help="生成交接卡模板")
    ap.add_argument("--out-dir", default=str(HANDOFF_DIR), help="交接卡目录")
    ap.add_argument("--json", action="store_true", help="输出 JSON 摘要")
    args = ap.parse_args(argv)

    if args.checkpoint:
        p = build_checkpoint(Path(args.out_dir))
        print(f"已生成交接卡：{p}")
        print("请人工补充第 2–6 节（做了什么/下一步/决策/坑/证据），然后提交。")
        return 0

    brief = build_brief()
    if args.json:
        plan = latest_plan()
        ho = latest_handoff()
        print(json.dumps({
            "head": sh(["git", "rev-parse", "--short", "HEAD"]),
            "dirty": len([x for x in sh(["git", "status", "--porcelain"]).splitlines() if x.strip()]),
            "plan": str(plan) if plan else "",
            "progress": plan_progress(plan),
            "latest_handoff": str(ho) if ho else "",
            "services": {str(p): listening(p) for p in (8756, 3080, 8901, 8902, 8903)},
            "brief": brief,
        }, ensure_ascii=False, indent=2))
    else:
        print(brief)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
