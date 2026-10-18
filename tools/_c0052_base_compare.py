# -*- coding: utf-8 -*-
"""C-0052 临时对照（验完即删）：HEAD 版 dashboard 与当前版的 /api/mindmap 基线。"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(r"G:\study_agent")
sys.path.insert(0, str(ROOT))
from core import config, db  # noqa: E402


def load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m


# HEAD 版的 dashboard.py（PowerShell 的 `>` 会写成 UTF-16，所以这里自己取）
src = subprocess.run(["git", "show", "HEAD:tools/dashboard.py"], cwd=str(ROOT),
                     capture_output=True).stdout.decode("utf-8")
tmp = Path(tempfile.mkdtemp(prefix="c0052_head_")) / "dashboard_head.py"
tmp.write_text(src, encoding="utf-8")
head = load(tmp, "dash_head")
cur = load(ROOT / "tools" / "dashboard.py", "dash_cur")
print("库路径：", config.DB_PATH, "（exists=%s）" % Path(config.DB_PATH).exists())
conn = db.connect()
db.init_db(conn)
for label, m in (("HEAD ", head), ("当前 ", cur)):
    for doc in (89, 5, 7, 103):
        try:
            d = m.api_mindmap(conn, doc)
            print("%s doc#%-3s state=%-8s n_nodes=%-4s lkp_total=%-4s msg=%s"
                  % (label, doc, d.get("artifact_state"), d.get("n_nodes"),
                     d.get("lkp_total"), str(d.get("artifact_message"))[:40]))
        except Exception as e:  # noqa: BLE001
            print("%s doc#%-3s ERR %s: %s" % (label, doc, type(e).__name__, e))
conn.close()
