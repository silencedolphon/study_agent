# -*- coding: utf-8 -*-
"""卡 C-0050 只读接口探针：打临时实例的 /api/mindmap 与 /api/summary-kp。

不写文件、不删文件——清除用例在 `verify_c0050_clear.py` 里（那边自己备份/恢复）。
用法：python tools\\probe_c0050_api.py [port]
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.parse
import urllib.request

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8899
BASE = "http://127.0.0.1:%d" % PORT


def get(path: str) -> tuple[int, dict]:
    url = BASE + path
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:  # noqa: BLE001
            return e.code, {}
    except Exception as e:  # noqa: BLE001
        return -1, {"__err": "%s: %s" % (type(e).__name__, e)}


def brief(d: dict, keys) -> dict:
    return {k: d.get(k) for k in keys}


def main() -> int:
    out = {}

    st, lst = get("/api/mindmap")
    out["list"] = {"status": st, "n": lst.get("n"),
                   "data_source": lst.get("data_source"),
                   "states": sorted({x.get("artifact_state") for x in (lst.get("documents") or [])}),
                   "doc5": next((x for x in (lst.get("documents") or [])
                                 if x.get("doc_id") == 5), {})}

    st, d5 = get("/api/mindmap?doc_id=5")
    out["doc5"] = {"status": st, **brief(d5, (
        "artifact_state", "has_structure", "has_outline", "pipeline_version",
        "lkp_total", "lkp_ready", "lkp_pending", "lkp_leaf_nodes", "lkp_multi_nodes",
        "n_pages", "n_nodes", "model", "created_at", "cost_cny", "lkp_coverage",
        "message", "error"))}
    def walk(nodes, acc):
        for n in nodes:
            acc.append({"name": n.get("name"), "n_pages": n.get("n_pages"),
                        "page": n.get("page"), "kind": n.get("kp_node_kind"),
                        "role": n.get("lkp_role"), "kp": n.get("lkp_id"),
                        "ready": n.get("lkp_ready")})
            walk(n.get("children") or [], acc)
    acc: list = []
    walk(d5.get("tree") or [], acc)
    out["doc5"]["tree_nodes"] = len(acc)
    out["doc5"]["root"] = acc[0] if acc else None
    lv3 = [x for x in acc if x["kind"] == "leaf"]
    out["doc5"]["leaves_sample"] = lv3[:3]
    out["doc5"]["n_ready_nodes"] = sum(1 for x in acc if x["ready"])

    st, d1 = get("/api/mindmap?doc_id=1")
    out["doc1_legacy"] = {"status": st, **brief(d1, (
        "artifact_state", "has_structure", "has_outline", "artifact_message",
        "tree", "n_pages", "n_nodes", "message", "error"))}

    st, d99 = get("/api/mindmap?doc_id=99999")
    out["doc99999"] = {"status": st, **brief(d99, (
        "artifact_state", "has_structure", "artifact_message", "tree", "error"))}

    st, kp = get("/api/summary-kp?doc_id=5&kp_id=" + urllib.parse.quote("KP-N03-1"))
    out["kp_n03_1"] = {"status": st, **brief(kp, (
        "kp_id", "name", "path", "node_name", "level", "pages", "pages_label",
        "status", "prev_id", "next_id", "order_index", "order_total",
        "n_material_blocks", "cost_cny"))}
    out["kp_n03_1"]["summary_len"] = len(kp.get("summary") or "")
    out["kp_n03_1"]["points"] = (kp.get("points") or [])[:3]
    out["kp_n03_1"]["error"] = kp.get("error")

    st, bad = get("/api/mindmap?doc_id=abc")
    out["doc_abc"] = {"status": st, "error": bad.get("error"),
                      "n": bad.get("n"), "tree": bad.get("tree")}

    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())