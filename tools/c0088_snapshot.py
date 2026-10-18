#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""卡 C-0088 回归采集/比对器：把 `ingest_file` / `ingest_paths` 的产物录成快照。

为什么需要它（卡面第一硬要求）：
    `mode=""`（默认）时必须与改造前**逐字段相同**。光看 diff 不算证据——
    要拿同一份样本、同一套临时库结构，在**改造前后各录一遍**产物，
    再逐字段比。本脚本就是那台"录音机"，`compare` 子命令是那台"比对仪"。

隔离（为什么敢跑，红线"真库保护"）：
  · 库 / raw / logs / 设置文件全部指向临时根（用环境变量，`core/config.py`
    读的就是它们；`KB_SETTINGS_FILE` 把用户自己的 `config.local.json` 也挡在外面）；
  · `KB_TESTING=1` + `core/db.py::connect()` 的护栏：一旦误连真实库就抛错，
    而不是静默跑完；
  · `OUTLINE_ANALYSIS=0` 关掉 AI 结构分析、`with_figures=False` 关掉视觉识图、
    `with_ocr=False` 关掉 OCR —— 不联网、不花钱、不慢，且两次运行条件相同；
  · 真库只被 `mode=ro` 只读 URI 数一次行数（`real_db_counts`），不写一个字节。

归一化（**只有这几项**，其余全部逐字段比，见 `compare`）：
  · `abs_path` → `<TMP>`：两次运行的临时根不同；
  · `indexed_at` / `updated_at` / `created_at` → `<TS>`：时间戳天然不同；
  · 任意字符串里出现的临时根路径 → `<TMP>`（例如警告文案里带了路径）。

跑法（绝对路径，不依赖 cwd）：
    G:\\study_agent\\venv\\Scripts\\python.exe -X utf8 \\
        G:\\study_agent\\tools\\c0088_snapshot.py collect --out <json> [--samples a,b]
    G:\\study_agent\\venv\\Scripts\\python.exe -X utf8 \\
        G:\\study_agent\\tools\\c0088_snapshot.py compare <baseline.json> <after.json> [--samples a,b]
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import os
import shutil
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

FIX = ROOT / "_smoke_fixtures"
WORK = ROOT / "_tmp_c0088"
REAL_DB = ROOT / "index" / "study.db"


def _active_db() -> Path:
    """**当前活跃**学科库。

    为什么要它：`logs/subjects.json` 的 `current` 可以被看板随时切走，切走之后
    "真库"就不再是 `index/study.db` 了。若只数 default 那一个库，
    "真库行数不变"这句就可能在测一个已经没人用的库。两个都数：
    default 库（`index/study.db`）+ 当前学科库（`subjects/<current>/index/study.db`）。
    """
    try:
        reg = json.loads((ROOT / "logs" / "subjects.json").read_text(encoding="utf-8"))
        cur = str(reg.get("current") or "default")
    except (OSError, ValueError):
        return REAL_DB
    if cur in ("", "default"):
        return REAL_DB
    p = ROOT / "subjects" / cur / "index" / "study.db"
    return p if p.exists() else REAL_DB


def _counts_of(db: Path) -> dict:
    """一个库的只读行数（`mode=ro` URI ⇒ 不写一个字节，连 WAL 都不落）。"""
    out: dict = {"path": str(db), "exists": db.exists()}
    if not db.exists():
        return out
    uri = "file:" + str(db).replace("\\", "/") + "?mode=ro"
    try:
        c = sqlite3.connect(uri, uri=True, timeout=5)
        c.row_factory = sqlite3.Row
        for t in ("documents", "chunks", "embeddings", "figure_desc"):
            try:
                out[t] = int(c.execute(f"SELECT COUNT(*) n FROM {t}").fetchone()["n"])
            except sqlite3.Error as e:
                out[t] = f"err: {e}"
        c.close()
    except sqlite3.Error as e:
        out["error"] = str(e)
    return out


def real_db_counts() -> dict:
    """真库只读行数：default 库 + **当前活跃学科库**（两者不同时都记）。"""
    out = _counts_of(REAL_DB)
    act = _active_db()
    if act.resolve() != REAL_DB.resolve():
        out["active"] = _counts_of(act)
    return out

# 样本清单：覆盖管线里**每一条**提取分支（txt / md / pdf / pptx / docx / image）。
# 元组 = (相对项目根的源文件, 在临时 raw 下的落点, 说明)。
# 不含 `formats/扫描版讲义.pdf`：它是扫描件，会触发本地 OCR（16 秒/页），
# 既超验收槽也把回归对照拖成"看运气"。
SAMPLES: list[tuple[str, str, str]] = [
    ("_smoke_fixtures/demo/概率论要点.txt", "demo/概率论要点.txt", "纯文本"),
    ("_smoke_fixtures/demo/深度学习基础-第三章.md",
     "demo/深度学习基础-第三章.md", "Markdown 标题层级"),
    ("_smoke_fixtures/formats/数据结构-第五章.pdf",
     "formats/数据结构-第五章.pdf", "PDF（有文字层）"),
    ("_smoke_fixtures/formats/计算机网络-第二章.pptx",
     "formats/计算机网络-第二章.pptx", "PPTX"),
    ("_smoke_fixtures/formats/操作系统-第四章.docx",
     "formats/操作系统-第四章.docx", "DOCX"),
    ("_smoke_fixtures/formats/公式卡片.png", "formats/公式卡片.png", "图片"),
    # ★ 这一份专门放 `raw/教材/` 下：`db.classify_role()` 的启发式会判它
    #   `textbook`（判据是 `f"{title} {path}"`，见 db.py:540-553）。
    #   它证明**通用入口**（`mode=""`）面对"教材目录里的文件"依旧走老路——
    #   这正是 C-0088 之后"教材专线只认显式 mode，不认路径"的回归面。
    ("_smoke_fixtures/formats/数据结构-第五章.pdf",
     "教材/数据结构-第五章.pdf", "PDF 落在 raw/教材/（启发式会判 textbook）"),
    # 真实课件（543KB）：块数上百，把切块/嵌入/写库的长度分支也压进对照
    ("raw/药理/2镇静催眠药.pptx", "课件/2镇静催眠药.pptx", "真实课件 PPTX（块多）"),
]
SAMPLE_MAP = {dest: desc for _src, dest, desc in SAMPLES}
SRC_MAP = {dest: src for src, dest, _desc in SAMPLES}
# 快档子集：验收槽 30 秒，只跑这三条最典型的提取路径 + 教材目录那一份。
FAST_SAMPLES = ["demo/概率论要点.txt", "demo/深度学习基础-第三章.md",
                "formats/数据结构-第五章.pdf", "formats/计算机网络-第二章.pptx",
                "教材/数据结构-第五章.pdf"]
TS_FIELDS = ("indexed_at", "updated_at", "created_at")
# 代码指纹：用来证明"代码确实变了，而产物没变"
CODE_FILES = ("core/ingest.py", "core/chunk.py", "core/textbook.py",
              "core/config.py", "core/db.py", "core/figures.py", "core/extract.py")


def _sha_file(p: Path) -> str:
    try:
        return hashlib.sha256(p.read_bytes()).hexdigest()
    except OSError:
        return ""


def _wipe(work: Path) -> None:
    """清掉临时根里的三个数据目录（**只在这三个子目录内**，见下断言）。"""
    work = work.resolve()
    # ★ 删除前把绝对路径验一遍：只允许删本项目的 `_tmp_c0088*` 目录。
    assert work.parent == ROOT.resolve(), f"拒绝操作：{work} 不在项目根下"
    assert work.name.startswith("_tmp_c0088"), f"拒绝操作：{work} 不是临时根"
    for sub in ("index", "raw", "logs", "settings.json"):
        t = work / sub
        if not t.exists():
            continue
        if t.is_dir():
            shutil.rmtree(t)
        else:
            t.unlink()
    work.mkdir(parents=True, exist_ok=True)


def _norm(obj, work: Path):
    """递归归一化：临时根路径与时间戳字段（其余原样，逐字段可比）。"""
    tmp = str(work.resolve())
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k in TS_FIELDS and v is not None:
                out[k] = "<TS>"
            else:
                out[k] = _norm(v, work)
        return out
    if isinstance(obj, (list, tuple)):
        return [_norm(v, work) for v in obj]
    if isinstance(obj, str):
        return obj.replace(tmp, "<TMP>").replace(tmp.replace("\\", "/"), "<TMP>")
    if isinstance(obj, float) and obj > 1e9:
        # 秒级时间戳（`db.now()`）：只可能出现在时间字段上，兜底归一
        return obj
    return obj


def _report_dict(rep) -> dict:
    """`IngestReport` → 纯 dict（只取字段，不碰逻辑）。"""
    if dataclasses.is_dataclass(rep):
        return dataclasses.asdict(rep)
    return {"_repr": repr(rep)}


def _rows(conn: sqlite3.Connection, sql: str) -> list[dict]:
    return [{k: r[k] for k in r.keys()} for r in conn.execute(sql)]


def collect(work: Path, samples: list[str]) -> dict:
    """跑一遍样本，录下产物快照。"""
    _wipe(work)
    raw = work / "raw"
    files: list[Path] = []
    for dest in samples:
        src = ROOT / SRC_MAP[dest]
        if not src.exists():
            raise SystemExit(f"样本缺失：{src}")
        dst = raw / dest
        dst.parent.mkdir(parents=True, exist_ok=True)
        # copy2 连 mtime 一起复制 ⇒ `documents.mtime` 在两次运行里可比
        shutil.copy2(src, dst)
        files.append(dst)

    # ★ 环境变量必须在 import core 之前落地（config 在导入时就固化这些值）。
    #   `KB_DB` **故意不在这里设**：`core/db.py` 的护栏基准 `_REAL_DB_PATH`
    #   取的是"模块加载时的 config.DB_PATH"，若连它一起改成临时库，护栏就会
    #   把临时库误判成真库而拒绝（db.py:326-328 记着这个已踩过的坑）。
    #   所以库路径按既有测试脚本的姿势，import 之后再改写 `config.DB_PATH`。
    os.environ.update({
        "KB_RAW": str(raw),
        "KB_INDEX": str(work / "index"),
        "KB_LOG": str(work / "logs"),
        "KB_SETTINGS_FILE": str(work / "settings.json"),   # 不存在 ⇒ 全用内置默认
        "KB_TESTING": "1",
        "OUTLINE_ANALYSIS": "0",
    })
    from core import config, db, ingest            # noqa: PLC0415

    config.DB_PATH = work / "index" / "study.db"
    if Path(config.DB_PATH).resolve() == REAL_DB.resolve():
        raise SystemExit("隔离失败：DB_PATH 没改掉，拒绝继续（会写坏真库）")

    conn = db.connect(immediate=True)
    db.init_db(conn)

    out: dict = {
        "kind": "c0088-snapshot",
        "root": "<PROJ>",
        "samples": [],
        "env": {
            "OUTLINE_ANALYSIS": os.environ.get("OUTLINE_ANALYSIS"),
            "with_ocr": False, "with_figures": False,
            "KB_DB": "<TMP>/index/study.db",
            "kb_testing": os.environ.get("KB_TESTING"),
        },
        "code": {p: _sha_file(ROOT / p) for p in CODE_FILES},
        "config": {
            "CHUNK_TARGET_CHARS": config.CHUNK_TARGET_CHARS,
            "CHUNK_MAX_CHARS": config.CHUNK_MAX_CHARS,
            "CHUNK_OVERLAP_CHARS": config.CHUNK_OVERLAP_CHARS,
            "CHUNK_MIN_CHARS": config.CHUNK_MIN_CHARS,
            "FIGURE_MAX_CALLS_PER_DOC": int(config.FIGURE_MAX_CALLS_PER_DOC or 0),
        },
        "real_db_before": real_db_counts(),
    }

    for f in files:
        rel = f.relative_to(raw).as_posix()
        rec: dict = {"sample": rel, "desc": SAMPLE_MAP.get(rel, "")}
        # 入口 1：`ingest_paths`（看板 /api/ingest、批量池、serve_mcp、study.py、
        #          share 导入全部经它）——先是 added，再跑一次是 skipped。
        rep1 = ingest.ingest_paths(conn, [f], force=False, with_ocr=False,
                                   with_figures=False)
        rec["ingest_paths_added"] = _report_dict(rep1)
        rep2 = ingest.ingest_paths(conn, [f], force=False, with_ocr=False,
                                   with_figures=False)
        rec["ingest_paths_skipped"] = _report_dict(rep2)
        # 入口 2：`ingest_file`（reimport_all / find_missing_overviews /
        #          outline_deck 直接调它）——force=True，走 updated 分支。
        rec["ingest_file_force"] = ingest.ingest_file(
            conn, f, force=True, with_ocr=False, with_figures=False)
        out["samples"].append(rec)

    out["documents"] = _rows(conn, "SELECT * FROM documents ORDER BY path")
    for d in out["documents"]:
        if d.get("extra"):
            try:
                d["extra"] = json.loads(d["extra"])
            except (TypeError, ValueError):
                pass
    out["chunks"] = _rows(
        conn, "SELECT id, doc_id, ord, text, text_norm, heading_path, topic, "
              "source_ref, char_len, token_est, content_hash, created_at "
              "FROM chunks ORDER BY doc_id, ord")
    out["embeddings"] = [
        {"chunk_id": r["chunk_id"], "model": r["model"], "dim": r["dim"],
         "vec_sha256": hashlib.sha256(r["vec"] or b"").hexdigest()}
        for r in conn.execute(
            "SELECT chunk_id, model, dim, vec FROM embeddings ORDER BY chunk_id")
    ]
    out["table_counts"] = {
        t: int(conn.execute(f"SELECT COUNT(*) n FROM {t}").fetchone()["n"])
        for t in ("documents", "chunks", "embeddings", "figure_desc", "ingest_runs")
    }
    conn.close()
    out["real_db_after"] = real_db_counts()
    return _norm(out, work)


def _diff(a, b, path: str, acc: list[str], limit: int = 40) -> None:
    """逐字段递归找差异（路径 + 两边取值），最多收 `limit` 条。"""
    if len(acc) >= limit:
        return
    if type(a) is not type(b):
        acc.append(f"{path}: 类型不同 {type(a).__name__} != {type(b).__name__}")
        return
    if isinstance(a, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a:
                acc.append(f"{path}.{k}: 只在 after 里有 = {b[k]!r}")
            elif k not in b:
                acc.append(f"{path}.{k}: 只在 baseline 里有 = {a[k]!r}")
            else:
                _diff(a[k], b[k], f"{path}.{k}", acc, limit)
            if len(acc) >= limit:
                return
        return
    if isinstance(a, list):
        if len(a) != len(b):
            acc.append(f"{path}: 长度不同 {len(a)} != {len(b)}")
            return
        for i, (x, y) in enumerate(zip(a, b)):
            _diff(x, y, f"{path}[{i}]", acc, limit)
            if len(acc) >= limit:
                return
        return
    if a != b:
        sa, sb = repr(a), repr(b)
        if len(sa) > 160:
            sa = sa[:160] + "…"
        if len(sb) > 160:
            sb = sb[:160] + "…"
        acc.append(f"{path}: {sa} != {sb}")


def _key(s: str) -> str:
    """把任意路径写法归一成 `a/b/c` 形态，便于跨机器/跨分隔符比较。

    `documents.path` 是**相对项目根**的（`_tmp_c0088\\raw\\demo\\x.txt`，
    反斜杠），而样本名是相对临时 raw 的（`demo/x.txt`）。两者靠
    "以 `raw/<样本名>` 结尾"配对，不能用 `Path.parent` 去猜——
    早先那版就是在这里用了正斜杠集合，结果过滤出空集、比对静默通过。
    """
    return str(s or "").replace("\\", "/").strip("/")


def _fix_docid(obj, m: dict):
    """把返回值里的 `doc_id` 按 `m`（原始 id -> 位次）替换。

    与库产物那边同一个理由：`documents.id` 是自增主键，取值取决于"这份样本
    是第几个插进库的"。子集比对时两侧各自从空库开始、样本集不同，
    `doc_id` 必然不同，而那不是行为差异。**只改 doc_id 这一个键**，
    其余字段（含 warnings / figures / outline_pages…）仍逐字比。
    """
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k == "doc_id" and isinstance(v, int):
                out[k] = m.get(v, v)
            else:
                out[k] = _fix_docid(v, m)
        return out
    if isinstance(obj, list):
        return [_fix_docid(v, m) for v in obj]
    return obj


def compare(base: dict, after: dict, samples: list[str] | None) -> tuple[bool, list[str], list[str]]:
    """比两份快照。返回 (是否全等, 差异列表, 比对过的范围说明)。"""
    keep = set(samples) if samples else None
    diffs: list[str] = []
    scope: list[str] = []

    # 1) 三个入口的返回值
    b_by = {s["sample"]: s for s in base.get("samples", [])}
    a_by = {s["sample"]: s for s in after.get("samples", [])}
    used = sorted(keep or set(b_by))
    # 样本位次：子集比对时用它替换 doc_id（见 `_fix_docid`）
    pos = {name: i for i, name in enumerate(used)}

    def docid_map(by: dict) -> dict:
        m = {}
        for name, i in pos.items():
            rec = by.get(name) or {}
            did = (rec.get("ingest_file_force") or {}).get("doc_id")
            if isinstance(did, int):
                m[did] = i
        return m

    b_map, a_map = docid_map(b_by), docid_map(a_by)
    for name in used:
        if name not in b_by or name not in a_by:
            diffs.append(f"samples[{name}]: 一侧缺失 "
                         f"(baseline={name in b_by}, after={name in a_by})")
            continue
        x, y = b_by[name], a_by[name]
        if keep:
            x, y = _fix_docid(x, b_map), _fix_docid(y, a_map)
        _diff(x, y, f"samples[{name}]", diffs)
    scope.append(f"入口返回值 {len(used)} 个样本 × 3 条路径"
                 + ("（子集比对：doc_id 已按样本位次重编号）" if keep else ""))

    # 2) 库产物：先按"路径后缀"把样本配对到 documents 行，再顺着 doc_id
    #    收 chunks / embeddings（两类 id 各自取，避免拿 baseline 的 id 去
    #    过滤 after 的行）。
    def ids_of(snap: dict, dests: list[str]) -> set:
        want = [f"raw/{_key(d)}" for d in dests]
        out = set()
        for d in snap.get("documents", []):
            p = _key(d.get("path"))
            if any(p.endswith(w) for w in want):
                out.add(d.get("id"))
        return out

    def pick_docs(snap: dict, dests: list[str]) -> list[dict]:
        want = [f"raw/{_key(d)}" for d in dests]
        return [d for d in snap.get("documents", [])
                if any(_key(d.get("path")).endswith(w) for w in want)]

    b_docs, a_docs = pick_docs(base, used), pick_docs(after, used)
    b_ids, a_ids = ids_of(base, used), ids_of(after, used)
    b_ch = [c for c in base.get("chunks", []) if c.get("doc_id") in b_ids]
    a_ch = [c for c in after.get("chunks", []) if c.get("doc_id") in a_ids]
    b_cids = {c.get("id") for c in b_ch}
    a_cids = {c.get("id") for c in a_ch}
    b_em = [e for e in base.get("embeddings", []) if e.get("chunk_id") in b_cids]
    a_em = [e for e in after.get("embeddings", []) if e.get("chunk_id") in a_cids]

    if keep:
        # ★ 子集比对时把自增主键按**位次**重编号。
        #
        # 为什么必须：`documents.id` / `chunks.id` 是 AUTOINCREMENT，取值由
        # "这份样本是第几个被插进库的"决定。录基线时 8 个样本按顺序插入、
        # 本次只跑 5 个，同一份资料的主键自然不同（实测差别全在这里：
        # `documents[4].id: 7 != 5`），而那与"行为变没变"无关。
        # 重编号只动主键，**其余字段仍逐字比**；全量比对（`keep` 为空）时
        # 连主键都原样比，证明力不减。
        def renumber(docs: list[dict], ch: list[dict], em: list[dict]):
            docs = sorted(docs, key=lambda d: _key(d.get("path")))
            dmap = {d.get("id"): i for i, d in enumerate(docs)}
            docs2 = [dict(d, id=dmap[d.get("id")]) for d in docs]
            ch = sorted(ch, key=lambda c: (dmap[c.get("doc_id")], c.get("ord") or 0))
            cmap = {c.get("id"): i for i, c in enumerate(ch)}
            ch2 = [dict(c, id=i, doc_id=dmap[c.get("doc_id")])
                   for i, c in enumerate(ch)]
            em = sorted(em, key=lambda e: cmap[e.get("chunk_id")])
            em2 = [dict(e, chunk_id=cmap[e.get("chunk_id")]) for e in em]
            return docs2, ch2, em2

        b_docs, b_ch, b_em = renumber(b_docs, b_ch, b_em)
        a_docs, a_ch, a_em = renumber(a_docs, a_ch, a_em)

    _diff(b_docs, a_docs, "documents", diffs)
    scope.append(f"documents {len(b_docs)} 行 / {len(a_docs)} 行（配对样本："
                 f"{sorted(_key(d.get('path')).rsplit('raw/', 1)[-1] for d in b_docs)}）")
    _diff(b_ch, a_ch, "chunks", diffs)
    scope.append(f"chunks {len(b_ch)} 行 / {len(a_ch)} 行")
    _diff(b_em, a_em, "embeddings", diffs)
    scope.append(f"embeddings {len(b_em)} 行（vec 逐块 sha256）"
                 + ("；子集比对：主键已按位次重编号" if keep else "；全量比对：含主键"))

    # 3) 真库读数：**只报告、不计入差异**。它是"当时真库长什么样"的旁证，
    #    若看板在两次录制之间正常写库（用户在用），拿它当差异会假失败；
    #    "本次验证没碰真库"由调用方在同一进程内 before/after 自查（见 verify）。
    scope.append(f"真库读数（仅供参考）baseline={base.get('real_db_after')} "
                 f"after={after.get('real_db_after')}")
    return (not diffs), diffs, scope


def main() -> int:
    ap = argparse.ArgumentParser(description="卡 C-0088 回归采集/比对")
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("collect", help="跑样本、录快照")
    c.add_argument("--out", required=True)
    c.add_argument("--work", default=str(WORK))
    c.add_argument("--samples", default="", help="逗号分隔的样本子集")
    c.add_argument("--tag", default="", help="写进快照的备注")

    p = sub.add_parser("compare", help="比两份快照")
    p.add_argument("baseline")
    p.add_argument("after")
    p.add_argument("--samples", default="", help="只比这些样本（逗号分隔）")
    p.add_argument("--quiet", action="store_true")

    a = ap.parse_args()
    if a.cmd == "collect":
        names = [s for s in a.samples.split(",") if s] or list(SAMPLE_MAP)
        for n in names:
            if n not in SAMPLE_MAP:
                raise SystemExit(f"未知样本：{n}")
        snap = collect(Path(a.work), names)
        if a.tag:
            snap["tag"] = a.tag
        outp = Path(a.out)
        outp.parent.mkdir(parents=True, exist_ok=True)
        outp.write_text(json.dumps(snap, ensure_ascii=False, indent=1),
                        encoding="utf-8")
        print(f"已录快照：{outp}（{len(snap['samples'])} 个样本 / "
              f"{snap['table_counts']['chunks']} 块）")
        print("代码指纹：")
        for k, v in snap["code"].items():
            print(f"  {k}  {v[:16]}")
        print(f"真库读数：{snap['real_db_after']}")
        return 0

    base = json.loads(Path(a.baseline).read_text(encoding="utf-8"))
    aft = json.loads(Path(a.after).read_text(encoding="utf-8"))
    names = [s for s in a.samples.split(",") if s] or None
    same, diffs, scope = compare(base, aft, names)
    if not a.quiet:
        print(f"baseline 代码指纹：{ {k: v[:12] for k, v in base.get('code', {}).items()} }")
        print(f"after    代码指纹：{ {k: v[:12] for k, v in aft.get('code', {}).items()} }")
        print(f"比对范围：{'；'.join(scope)}")
        if base.get("code", {}).get("core/ingest.py") == \
                aft.get("code", {}).get("core/ingest.py"):
            print("⚠ core/ingest.py 指纹两份相同 —— 这份 baseline 不是改造前录的！")
        if same:
            print("C0088-REGRESSION-IDENTICAL 逐字段相同")
        else:
            print(f"C0088-REGRESSION-DIFF 共 {len(diffs)} 处差异：")
            for d in diffs[:40]:
                print("  -", d)
    return 0 if same else 1


if __name__ == "__main__":
    sys.exit(main())
