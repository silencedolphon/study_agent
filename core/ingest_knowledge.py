"""入库完成后衔接课件知识点整理。"""
from __future__ import annotations

import shutil
import time
import uuid
from pathlib import Path

from . import config, kp_batch, kp_build


def run(conn, detail, *, db_path=None, log_dir=None, on_event=None, progress_on=False):
    status = str(detail.get("status") or "")
    doc_id = int(detail.get("doc_id") or 0)
    if status not in ("added", "updated", "skipped", "partial") or not doc_id:
        return {"status": "failed", "reason": "入库未完整完成，暂不整理知识点"}
    row = conn.execute("SELECT role, status FROM documents WHERE id=?", (doc_id,)).fetchone()
    if not row or str(row[0] or "courseware") != "courseware":
        return {"status": "skipped", "reason": "不是课件"}
    source_status = str(row[1] or "ok")
    if source_status not in ("ok", "partial"):
        return {"status": "failed", "reason": "入库结果不完整，请先检查"}
    incomplete = source_status == "partial" or status == "partial"
    if incomplete and not conn.execute(
            "SELECT 1 FROM chunks WHERE doc_id=? LIMIT 1", (doc_id,)).fetchone():
        return {"status": "failed", "reason": "入库未提取到可用内容，无法整理知识点"}
    warning = ("资料存在提取不完整或识图提示，整理基于现有内容，请对照原课件核实。"
               if incomplete or detail.get("warnings") else "")
    out = Path(log_dir or config.LOG_DIR) / "knowledge" / (str(doc_id) + kp_build.KP_SUFFIX)
    est = kp_build.estimate(doc_id, db_path=db_path)
    if not est.get("ok"):
        return {"status": "failed", "reason": str(est.get("error") or "无法估算知识点整理")}
    prev = kp_build.prev_info(doc_id, out_path=out)
    done, reason = kp_batch.done_state(est, prev)
    if done:
        return {"status": "skipped", "reason": reason, "warning": warning, "reused": True}
    # 即使支持续跑也先备份，以保留重入库前完整产物。
    backup = ""
    if prev.get("exists"):
        bak = out.with_name(out.name + ".bak-" + time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8])
        try:
            shutil.copy2(out, bak)
            backup = str(bak)
        except OSError as exc:
            return {"status": "failed", "reason": "备份旧知识点失败：" + str(exc)}
    result = kp_build.build(doc_id, db_path=db_path, out_path=out, force=False,
                            max_cost=0.8, progress_on=progress_on,
                            on_event=on_event, log=lambda _: None)
    state = str(result.get("status") or "")
    usage = (result.get("meta") or {}).get("usage_total_this_run") or {}
    try:
        cost = float(usage.get("cost_cny") or 0)
    except (TypeError, ValueError):
        cost = 0.0
    if not result.get("ok") or state not in ("done", "complete"):
        return {"status": "failed", "reason": str(result.get("error") or "知识点尚未全部完成"),
                "backup": backup, "warning": warning, "cost_cny": cost}
    return {"status": "done", "reason": "知识点已整理", "backup": backup,
            "warning": warning, "cost_cny": cost}
