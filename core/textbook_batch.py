"""教材章节批处理：一个队列依次调用现有单章管线。"""
import copy
import threading
import time
import uuid
from pathlib import Path

from . import config

_lock = threading.RLock()
_plans = {}
_state = {"active": False, "status": "", "items": []}


def snapshot():
    with _lock:
        return copy.deepcopy(_state)


def plan(mode, doc_id, chapters, estimate):
    if mode not in ("preview", "textbook"):
        raise ValueError("未知批处理类型")
    doc_id = int(doc_id)
    chapters = list(dict.fromkeys(int(c) for c in chapters))
    if doc_id <= 0 or not chapters or len(chapters) > 100 or min(chapters) <= 0:
        raise ValueError("请选择 1–100 个有效章节")
    items = []
    for chapter in chapters:
        e = estimate(mode, doc_id, chapter)
        if e.get("ok") is False:
            raise ValueError("第 %d 章：%s" % (chapter, e.get("error") or "估算失败"))
        items.append({"chapter": chapter, "estimate": e})
    token = uuid.uuid4().hex
    with _lock:
        now = time.time()
        for k in list(_plans):
            if now - _plans[k]["created"] > 1800:
                del _plans[k]
        _plans[token] = {"mode": mode, "doc_id": doc_id, "items": items,
                         "db_path": str(Path(config.DB_PATH)), "created": now}
    return {"ok": True, "token": token, "mode": mode, "doc_id": doc_id,
            "items": copy.deepcopy(items)}


def start(token, estimate, launch, progress, busy):
    with _lock:
        if _state.get("active") or busy():
            return {"ok": False, "error": "已有预习或教材总结任务在运行"}
        p = _plans.pop(str(token), None)
        if not p or time.time() - p["created"] > 1800 or p["db_path"] != str(Path(config.DB_PATH)):
            return {"ok": False, "error": "估算已过期或学科已切换，请重新估算"}
        _state.clear()
        _state.update(active=True, status="running", mode=p["mode"], doc_id=p["doc_id"],
                      total=len(p["items"]), done=0, items=[{"chapter": x["chapter"],
                      "status": "pending", "error": ""} for x in p["items"]])

    def run():
        try:
            for index, item in enumerate(p["items"]):
                chapter, old = item["chapter"], item["estimate"]
                with _lock:
                    _state["items"][index]["status"] = "running"
                    _state["chapter"] = chapter
                try:
                    fresh = estimate(p["mode"], p["doc_id"], chapter)
                    # 设置、材料、输出上限或预计调用数变化时不自动花费。
                    if fresh != old:
                        raise ValueError("教材内容、设置或预计调用量已变化，请重新估算")
                    result = launch(p["mode"], p["doc_id"], chapter, old)
                    if not result.get("started"):
                        raise ValueError(result.get("error") or "任务未启动")
                    while True:
                        state = progress(p["mode"])
                        if not state.get("active"):
                            break
                        time.sleep(0.5)
                    status = state.get("status")
                    if status not in ("done", "skipped"):
                        raise ValueError(state.get("error") or "结果未完成：" + str(status))
                    with _lock:
                        _state["items"][index]["status"] = "done"
                except Exception as exc:
                    with _lock:
                        _state["items"][index].update(status="failed", error=str(exc))
                with _lock:
                    _state["done"] = index + 1
        finally:
            with _lock:
                _state["active"] = False
                _state["status"] = "failed" if any(x["status"] == "failed" for x in _state["items"]) else "done"
    threading.Thread(target=run, name="textbook-batch", daemon=True).start()
    return {"ok": True, "started": True}
