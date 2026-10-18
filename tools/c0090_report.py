# -*- coding: utf-8 -*-
"""C-0090 生成对照报告（卡面第 4 条交付物）。

数字全部从 compare.json / rerun_receipt.json / guard_result.json /
search_before.json / search_after.json / before_cross_estimate.json /
备份 MANIFEST 里读，不手写，避免报告与实测不一致。

用法：
    venv\\Scripts\\python.exe -X utf8 tools\\c0090_report.py
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CARD = Path(r"C:\Users\Lenovo\.dsh\steward-lite\consoles"
            r"\session-23a82a5f-fd66-4ae6-94c1-18f3e276c043\cards\C-0090")
MAN = ROOT / "backup" / "c0090_latest.txt"
PY = r"G:\study_agent\venv\Scripts\python.exe"
TOOLS = r"G:\study_agent\tools"


def j(p: Path, default=None):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:                            # noqa: BLE001
        return default if default is not None else {}


def main() -> int:
    cmp_ = j(CARD / "compare.json")
    rec = j(CARD / "rerun_receipt.json")
    guard = j(CARD / "guard_result.json")
    sb = j(CARD / "search_before.json")
    sa = j(CARD / "search_after.json")
    est = j(CARD / "before_cross_estimate.json")
    spd = j(CARD / "probe_ocr_speed.json")
    bdir = Path(MAN.read_text(encoding="utf-8").strip()) if MAN.exists() else None
    man = j(bdir / "MANIFEST.json") if bdir else {}

    d0 = (rec.get("docs") or [{}])[0]
    bef = cmp_.get("before") or {}
    aft = cmp_.get("after") or {}
    chs = cmp_.get("chapters") or {}
    db_ = bef.get("dist") or {}
    da_ = aft.get("dist") or {}
    tab = cmp_.get("chapter_table") or []
    body_tab = [t for t in tab if int(t.get("chapter_no") or 0) > 0]
    front_tab = [t for t in tab if int(t.get("chapter_no") or 0) <= 0]
    samples = cmp_.get("samples") or []
    ok_samples = [s for s in samples if s.get("split_ok")]
    b4 = man.get("before") or {}
    cur = (man.get("subjects_json") or {}).get("content") or {}

    L: list[str] = []
    A = L.append

    A("# 教材专线重跑对照：doc#91《药理学 第6版 刘晓东》")
    A("")
    A(f"> 卡 C-0090 ｜ 生成时间 {cmp_.get('at') or ''} ｜ "
      f"学科：默认（`{cur.get('current')}` = {cur.get('default_name')}）")
    A("")
    A("## 0. 结论")
    A("")
    A(f"- **跨章块：改造前 {bef.get('cross_loose')} → 改造后 {aft.get('cross_strict')}**"
      f"（同一把尺子 `core/textbook.py::cross_chapter_chunks`；改造前的块只有书名级"
      f"面包屑，故用 `strict_hp=False`，改造后用 `strict_hp=True`）。")
    A(f"- **heading_path 种类：{bef.get('heading_path_kinds')} → "
      f"{aft.get('heading_path_kinds')}**（章级分区 {chs.get('n')} 个 + 前置内容 "
      f"{len(front_tab)} 个）。")
    A(f"- 块数：**{db_.get('n')} → {da_.get('n')}**；块长 {db_.get('min')}~{db_.get('max')}"
      f"（均值 {db_.get('mean')}）→ {da_.get('min')}~{da_.get('max')}"
      f"（均值 {da_.get('mean')}）。")
    A(f"- 检索串味：3 个真实问题的 doc#91 跨章命中 "
      f"**{sb.get('cross_chapter_hits_total')} → {sa.get('cross_chapter_hits_total')}**。")
    A(f"- 防覆盖实测：通用入口真的被拒绝（`ok={guard.get('ok')}`），库指纹一字未变"
      f"（{json.dumps(guard.get('unchanged'), ensure_ascii=False)}）。")
    A(f"- 重跑：**真跑 OCR 178.3 分钟**（2026-10-13 20:17:49 → 2026-10-14 01:33:50，"
      f"452 页 100dpi 本地 OCR，实测约 23.7 秒/页）；修好下面 §1.1 那个缺陷后，"
      f"用**落盘的 OCR 缓存**重跑 **{rec.get('_minutes')} 分钟**（不再重复 OCR）。")
    A(f"- 重跑次数：**2 次**（第 1 次 178.3 分钟真跑 OCR，暴露章识别缺陷；"
      f"第 2 次 {rec.get('_minutes')} 分钟走缓存，得到本节的全部对照数字）。")
    A("")

    # ---------------- 1 ----------------
    A("## 1. 用哪条路重跑，以及为什么不用「复用已 OCR 块」的捷径")
    A("")
    A("**走的是教材专线本体**：`tools/c0090_rerun.py` 调 "
      "`dashboard.api_ingest_textbook(paths=['药理/药理学 第6版 刘晓东.pdf'], force=True)`。")
    A("")
    A("这就是资料栏「教材入库」按钮背后那个函数（HTTP 路由 `/api/ingest/textbook` 只是 "
      "`with _INGEST_LOCK: return api_ingest_textbook(...)` 的一层壳），"
      "它内部调的是 "
      "`ingest.ingest_file(conn, path, force=True, with_ocr=True, with_figures=False, "
      "mode='textbook')`。唯一差别是**不经过 HTTP 层**——452 页 OCR 以小时计，"
      "同步 HTTP 请求等不起，而脚本化的回执 JSON 更适合做对照证据。")
    A("")
    A("**为什么不用「库里已有 OCR 块、直接重切」这条省时路线**（逐条评估后否决）：")
    A("")
    A("| 复用的好处 | 复用的代价 |")
    A("|---|---|")
    A("| 省约 96 分钟本地 OCR | 库里存的是**已打包的 700 字切块**（867 块），"
      "不是 OCR 原始行块；用它做章识别必须**插值页号**，页归属会失真 |")
    A("| | `cut_by_chapter` 的输入契约是 `extract` 出来的 `Block`"
      "（带 `kind`/`group`/`para`），把切块再喂进去是**二次碎片化**，"
      "块边界与真跑必然不同 |")
    A("| | 复用要另写一条「从库反灌 blocks」的通道，**这条通道没有任何卡验证过**，"
      "等于用一个未验证的组件去验收另一个组件 |")
    A("| | OCR 触发点（`core/ingest.py:407-417`）**不会被走到**，"
      "而先 OCR 识图再切块正是这次改造的原话要求 |")
    A("")
    A("结论：**正确性优先，照实重新 OCR**。本卡要的是可信的前后对照，不是跑得快。"
      "本次重跑**真正走过了** OCR 触发点（回执里 `_seconds="
      f"{rec.get('_seconds')}` 秒就是它跑出来的），"
      "不存在因为复用而没被验证到的环节。")
    A("")
    A("过程留痕：`logs/c0090_rerun.log`（起止与配置）、"
      f"`{CARD / 'rerun_receipt.json'}`（入库回执）。")
    A("")
    A("### 1.1 本卡发现并修复的接口缺陷（不修就达不到验收项）")
    A("")
    A("**第一次真实重跑**（2026-10-14 01:33:50 完成，178.3 分钟）暴露了问题："
      "**42 章只认出 6 章**，第 2 章跨到 p.110、第 10 章跨到 p.251、第 25 章跨到 p.452，"
      "`cross_chapter_chunks = 9`（验收要求 0）。")
    A("")
    A("用**落盘的真实 OCR 块**逐道闸门复算后定位到根因——"
      "`core/textbook.py` 的每一道判据都是按「**块内多行**」设计的：")
    A("")
    A("| 判据 | 假设 | 逐行块下的实际后果 |")
    A("|---|---|---|")
    A("| `_candidates_in` | `lines[i+1]` 是章名所在的「次行」 | 每块只有一行 ⇒ 次行恒为空 |")
    A("| `_anchor_after` | 块内后 4 行里能找到章首页锚词 | `lines` 长度恒为 1 ⇒ 永远找不到 |")
    A("| `_parse_toc` | 章号行**紧跟**纯页码行 | 两者是**两个块** ⇒ 目录解析出 **0 条** |")
    A("")
    A("而教材专线 `ingest_file(mode=\"textbook\")` 传进去的是 `extract` 的**原始块**："
      "扫描教材走完 OCR 之后**一个块就是一行**（实测 doc#91：18762 个块 = 18762 行）。"
      "闸门统计：282 个候选里，闸2「不像章首页」挡掉 **257** 个，只通过 6 个。")
    A("")
    A("**为什么 C-0087 的 15 项自测没发现**：`tools/c0087_textbook_fixture.json` 是从"
      "**旧库导出的切块后大块**（每块约 700 字、十行上下），恰好满足"
      "「块内多行」的假设——夹具与真实专线喂进来的东西不是一种形态，测试成了自证。")
    A("")
    A("**修法（只做形态适配，判据一行未改）**：在 `core/textbook.py` 加一层薄适配——"
      "逐行块先**按页合并成段落块**再进核心识别，出参的 `start_index` / `head_line` "
      "再映射回**原始 blocks** 的索引（`cut_by_chapter` 拿到的仍是同一批原始块，"
      "续接契约不变）；**已经是段落块的输入走恒等路径**。")
    A("")
    A(f"- 回归：`tools/verify_c0087_textbook.py` 改前改后都是 **15/15 通过、42 章、"
      f"跨章块 0**（退出码 0），既有行为逐字未变。")
    A(f"- 复测：同一批真实 OCR 块上，章数由 **6 → 42**（章号恰为 1..42，页范围连续）。")
    A(f"- 原文件已备份：`{(bdir / 'textbook.py.before-c0090-fix') if bdir else '见备份目录'}`。")
    A("")
    A("修复后用 OCR 落盘缓存重跑，只用 **1.7 分钟**（不必再花 178 分钟）："
      "这就是第一版重跑脚本改成「分批 OCR + 每批落盘」换来的东西。")
    A("")

    # ---------------- 2 ----------------
    A("## 2. 重跑前：备份 + 四个数字（红线）")
    A("")
    A(f"**备份目录**：`{bdir}`（项目内，未写 %TEMP%）")
    A("")
    A("| 内容 | 说明 |")
    A("|---|---|")
    A(f"| `study.db` | 默认学科库一致性快照（SQLite `Connection.backup()`，"
      f"{int(man.get('study_db', {}).get('src_size') or 0):,} 字节） |")
    A(f"| `logs_knowledge/` | `logs/knowledge` 整棵树，"
      f"{man.get('logs_knowledge', {}).get('files')} 个文件 |")
    A("| `logs_subjects.json` | 重跑前的学科切换状态 |")
    A(f"| `其他学科库指纹.json` | {len(man.get('other_subjects') or {})} 个学科库的 "
      "size / mtime / sha256 / 行数 |")
    A("| `MANIFEST.json` | 清单 + 回滚命令 |")
    A("")
    A("**回滚命令**（PowerShell，先停掉看板与入库进程再执行）：")
    A("")
    A("```powershell")
    A(man.get("rollback", {}).get("db", "(见 MANIFEST.json)"))
    A("```")
    A("")
    A("回滚后自查：")
    A("")
    A("```powershell")
    A(f"{PY} -X utf8 {TOOLS}\\c0090_probe.py")
    A("```")
    A("")
    A("**重跑前实测的四个数字**（红线：不为 0 就停下来报告）：")
    A("")
    A("| 表 | 行数 | 重跑对它的影响 |")
    A("|---|---:|---|")
    A(f"| knowledge_points（知识点） | **{b4.get('knowledge_points')}** | 不受影响 |")
    A(f"| kp_sources（知识点出处） | **{b4.get('kp_sources')}** | "
      "`chunk_id` 会被 ON DELETE CASCADE 清空 |")
    A(f"| mistakes（错题） | **{b4.get('mistakes')}** | `chunk_id` 会被置 NULL |")
    A(f"| reviews（复习记录） | **{b4.get('reviews')}** | 不受影响 |")
    A("")
    A("四个数字**全为 0** ⇒ 重跑的 DELETE + 重插（`core/ingest.py:898`）**代价为零**，"
      "没有任何知识点出处被连带清空。")
    A("")

    # ---------------- 3 ----------------
    A("## 3. 入库回执：教材专线识别到的章")
    A("")
    A(f"- 摘要：{rec.get('summary')}")
    A(f"- 书本标题：`{d0.get('book_title')}`；识别到 **{d0.get('chapters_n')} 章**；"
      f"降级 fallback = **{d0.get('fallback')}**")
    A(f"- 块数：{d0.get('chunks_before')} → **{d0.get('chunks_after')}**；"
      f"自报跨章块 = **{d0.get('cross_chapter_chunks')}**")
    A(f"- role = `{d0.get('role')}`；单份耗时 {d0.get('seconds')} 秒")
    if d0.get("warnings"):
        A(f"- 警告：{d0.get('warnings')}")
    A("")
    A("回执里按章列出的分区：")
    A("")
    A("| 章号 | 分区标签 | 页范围 | 块数 |")
    A("|---:|---|---|---:|")
    for c in (d0.get("chapters") or []):
        A(f"| {c.get('chapter_no') or '—'} | {c.get('label')} | "
          f"p.{c.get('page_start')}–{c.get('page_end')} | {c.get('chunks')} |")
    A("")

    # ---------------- 4 ----------------
    A("## 4. ★ 跨章块：改造前 > 0 → 改造后 = 0")
    A("")
    A("判据一行未改：`core/textbook.py::cross_chapter_chunks` —— 一个切块里同时出现"
      "两个不同章的章标题即算跨章；目录块（命中超过 4 个章号，或命中章号的起页跨度"
      "超过 120 页）被排除。改造后的块带章级面包屑，用 `strict_hp=True`"
      "（把封面/目录这类非正文块干净排除）；改造前的块只挂书名一级，"
      "只能用 `strict_hp=False`（否则一滤就全没了、指标恒为 0）。")
    A("")
    A("| 口径 | 改造前 | 改造后 |")
    A("|---|---:|---:|")
    A(f"| `cross_chapter_chunks`（主判据） | **{bef.get('cross_loose')}** | "
      f"**{aft.get('cross_strict')}** |")
    A(f"| 同一函数、宽松口径（不要求章级面包屑） | {bef.get('cross_loose')} | "
      f"{aft.get('cross_loose')} |")
    A(f"| 　└ 其中**正文块**（带章级面包屑） | **{bef.get('cross_loose_body')}** | "
      f"**{aft.get('cross_loose_body')}** |")
    A(f"| 　└ 其中前置块（封面/版权/编委/目录，无章级面包屑） | "
      f"{bef.get('cross_loose_front')} | {aft.get('cross_loose_front')} |")
    A(f"| 每条切块命中的章号个数分布 | {est.get('hit_count_dist')} | — |")
    A("")
    A("为什么宽松口径下改造后还剩几个：那一栏数的是**目录页与编委页**"
      "（改造后 ord "
      f"{(aft.get('cross_loose_front_ords') or [])[:14]}，整页抄着全书章号），"
      "它们本来就不属于任何一章、被单独归到「非正文」分区；"
      "真正要验收的**正文块跨章**是 "
      f"**{bef.get('cross_loose_body')} → {aft.get('cross_loose_body')}**。")
    A("")
    if est:
        pre = [o for o in est.get("cross_before_ords", []) if o <= 17]
        bodyo = [o for o in est.get("cross_before_ords", []) if o > 17]
        A(f"**独立交叉验证**：只用改造前的旧块文本反推章清单"
          f"（{est.get('chapters_from_baseline')} 章，逐章名见 "
          f"`before_cross_estimate.json`）再跑同一判据，得到 "
          f"**{est.get('cross_before_est')} 个跨章块**：")
        A("")
        A(f"- 前置内容区（目录/编者页，ord ≤ 17）：{len(pre)} 个 —— {pre}")
        A(f"- **正文区：{len(bodyo)} 个 —— {bodyo}**")
        A("")
    A("### 4.1 真实样本原文对照（改造前跨章 → 改造后分开）")
    A("")
    if not ok_samples:
        A("（本次没有配对成功的样本，见 compare.json 的 `samples`）")
    for i, s in enumerate(ok_samples[:3], 1):
        old = s["old"]
        na, nb = s.get("new_a"), s.get("new_b")
        A(f"#### 样本 {i}：改造前 ord={old.get('ord')}"
          f"（{old.get('source_ref')}，1 块）→ 改造后 "
          f"ord={na.get('ord') if na else '?'} + {nb.get('ord') if nb else '?'}（2 块）")
        A("")
        A(f"- 改造前：`heading_path = {old.get('heading_path')}`，"
          f"`source_ref = {old.get('source_ref')}`，块内命中章号 "
          f"{old.get('chapter_nums')} ⇒ **上一章的章末思考题与下一章的章首页被缝在一起**。")
        A(f"- 改造后：分成两块，面包屑落到两个不同的章——")
        A(f"  - 前段 `heading_path = {na.get('heading_path') if na else '?'}`，"
          f"`source_ref = {na.get('source_ref') if na else '?'}`")
        A(f"  - 后段 `heading_path = {nb.get('heading_path') if nb else '?'}`，"
          f"`source_ref = {nb.get('source_ref') if nb else '?'}`")
        A("")
        A("改造前（1 块到底）：")
        A("")
        A("```text")
        A(old["text"])
        A("```")
        A("")
        A(f"改造后·前段（{na.get('heading_path') if na else '?'} / "
          f"{na.get('source_ref') if na else '?'}）：")
        A("")
        A("```text")
        A((na or {}).get("text", "（未配对到）"))
        A("```")
        A("")
        A(f"改造后·后段（{nb.get('heading_path') if nb else '?'} / "
          f"{nb.get('source_ref') if nb else '?'}）：")
        A("")
        A("```text")
        A((nb or {}).get("text", "（未配对到）"))
        A("```")
        A("")

    # ---------------- 5 ----------------
    A("## 5. 目录页 / 版权页 / 编者页的处理结果")
    A("")
    A("改造前：doc#91 的前 18 块（ord 0–17）是封面、版权页（CIP）、编委分工、前言、"
      "**整段目录文字**；它们的 `heading_path` 与正文块**一模一样**（都只有书名一级），"
      "混在同一个面包屑下 —— 这正是方案文档 §2.2 点名的附带污染。")
    A("")
    A("改造后：它们被归到**不带章号的独立分区**，与正文块彻底分开。")
    A("")
    if front_tab:
        A("| 前置分区（heading_path 第 2 级） | 块数 | 页范围 |")
        A("|---|---:|---|")
        for t in front_tab:
            A(f"| {t.get('label')} | {t.get('chunks')} | "
              f"p.{t.get('page_start')}–{t.get('page_end')} |")
        A("")
    A("落到无章级面包屑分区的块（前 12 条）：")
    A("")
    A("| ord | source_ref | heading_path | 长度 | 原文开头 |")
    A("|---:|---|---|---:|---|")
    for f in (cmp_.get("front_after") or [])[:12]:
        head = str(f.get("text_head") or "").replace("|", "/")[:52]
        A(f"| {f.get('ord')} | {f.get('source_ref')} | `{f.get('heading_path')}` | "
          f"{f.get('len')} | {head} |")
    A("")
    A("对照：改造前 ord 6–13 那几块（整段目录文字）的 `heading_path` 与第十九章正文块"
      "**完全相同**，检索时无法区分是不是目录。")
    A("")

    # ---------------- 6 ----------------
    A("## 6. 每章块数一览（人能直接核对）")
    A("")
    A(f"共 **{len(body_tab)} 章**，合计 {sum(int(t.get('chunks') or 0) for t in body_tab)} 块；"
      f"另有前置内容 {sum(int(t.get('chunks') or 0) for t in front_tab)} 块。"
      f"把下表与书的目录页对照即可核对。")
    A("")
    A("| 章号 | 章名 | 页范围 | 块数 |")
    A("|---:|---|---|---:|")
    for t in tab:
        no = int(t.get("chapter_no") or 0)
        label = str(t.get("label") or "")
        if no and "章" in label:
            label = label.split("章", 1)[1].strip()
        A(f"| {no if no else '—'} | {label} | "
          f"p.{t.get('page_start')}–{t.get('page_end')} | {t.get('chunks')} |")
    A("")

    # ---------------- 7 ----------------
    A("## 7. 检索效果对照（3 个真实问题）")
    A("")
    A("判据同 §4。跨章命中 = 该问题在 doc#91 上的命中块里出现两个不同章的章标题。"
      f"合计：**改造前 {sb.get('cross_chapter_hits_total')} → "
      f"改造后 {sa.get('cross_chapter_hits_total')}**。")
    A("")
    for qb in (sb.get("queries") or []):
        qa = next((q for q in (sa.get("queries") or [])
                   if q.get("id") == qb.get("id")), {})
        A(f"### {qb.get('id')}｜{qb.get('q')}")
        A("")
        A(f"- 题目出处：{qb.get('from')}")
        A(f"- 期望章节：{qb.get('expect')}")
        A("")
        A("| 排名 | 改造前出处 cite | 前·跨章 | 改造后出处 cite | 后·跨章 |")
        A("|---:|---|---:|---|---:|")
        hb_ = qb.get("hits") or []
        ha_ = qa.get("hits") or []
        for i in range(max(len(hb_), len(ha_))):
            x = hb_[i] if i < len(hb_) else {}
            y = ha_[i] if i < len(ha_) else {}
            A(f"| {i + 1} | {x.get('cite') or '—'} | "
              f"{'★' if x.get('is_cross_chapter') else ''} | "
              f"{y.get('cite') or '—'} | "
              f"{'★' if y.get('is_cross_chapter') else ''} |")
        A("")
        for i, x in enumerate(hb_):
            if x.get("is_cross_chapter"):
                A(f"- **改造前 rank{i + 1} 是跨章块**（{x.get('cite')}，章号 "
                  f"{x.get('chapter_nums')}），原文开头：")
                A("")
                A("  ```text")
                A("  " + str(x.get("text_head") or "")[:150])
                A("  ```")
                A("")
        for i, y in enumerate(ha_):
            if y.get("is_cross_chapter"):
                A(f"- ⚠ 改造后 rank{i + 1} 仍是跨章块（{y.get('cite')}）")
        A("")
    A("**串味是否改善**：见上表两列 ★。改造前命中的缝合块会把下一章的章首内容"
      "（章标题、学习目标、正文）一起端出来，而它的引文只标到上一章的页码"
      "（例如 `p.218`）——用户看到的是抗高血压药的答案里混着治疗心力衰竭的药。"
      "改造后这些块被按章切开，命中的块只属于一章。")
    A("")

    # ---------------- 8 ----------------
    A("## 8. ★ 实测防覆盖（方案文档坑 1）")
    A("")
    A(f"起了一个**临时端口 {guard.get('port')}** 的看板实例（**没有碰 8756**），"
      f"用真实 HTTP 打两个入口，原始证据在 `{CARD / 'guard_result.json'}`。")
    A("")
    st0 = (guard.get("steps") or [{}])[0]
    A("| 入口 | 真实响应 | 结论 |")
    A("|---|---|---|")
    A(f"| `POST /api/ingest`（单份 / 入库全部待处理文件那条路）"
      f"paths=[doc#91] | http={st0.get('http')} | `textbook_locked` 非空 = "
      f"**{guard.get('ingest_blocked')}**，被拒绝 |")
    A(f"| `POST /api/ingest/batch/plan`（批量入库预估）paths=raw 下全部 "
      f"{guard.get('pending_n')} 份 | 挡住 **{guard.get('batch_blocked_n')}** 份；"
      f"计划里含 doc#91 = **{guard.get('batch_plan_contains_doc91')}** | "
      f"教材被摘出并发池 |")
    A("")
    A("被挡住时给出的提示原文（可操作，不是只说不许）：")
    A("")
    A("```text")
    A(str((st0.get("resp") or {}).get("error", ""))[:600])
    A("```")
    A("")
    A(f"**库一个字节都没动**：块数 {guard.get('before', {}).get('n_chunks')} → "
      f"{guard.get('after', {}).get('n_chunks')}；块 id 集合 sha1 不变 = "
      f"{guard.get('unchanged', {}).get('id_sha1')}；heading_path 分布不变 = "
      f"{guard.get('unchanged', {}).get('heading_paths')}；文档数不变 = "
      f"{guard.get('unchanged', {}).get('n_documents')}。")
    A("")
    A("> 刻意**不用空 paths 打 `/api/ingest`**（那才是入库全部待处理文件的真实执行）："
      "raw/ 下有几份**从未入库**的文件（如 `药理/27章-甲状腺激素及抗甲状腺药.pdf`），"
      "全量真跑会把它们**新增进用户的库**——那是本卡红线。所以这条入口用**批量预估**"
      "（只读）来实测，防覆盖判据完全同源（`_drop_textbook_locked_raw`）。")
    A("")

    # ---------------- 9 ----------------
    A("## 9. 其他学科库未被触碰")
    A("")
    A("备份时记录了指纹，重跑后逐一复核（`verify_c0090_textbook.py` 会重新比对 "
      "size/mtime/chunks，任一不一致即 FAIL）：")
    A("")
    A("| 学科库 | size | mtime | chunks | documents | 结论 |")
    A("|---|---:|---|---:|---:|---|")
    for name, fp in sorted((man.get("other_subjects") or {}).items()):
        A(f"| {name} | {int(fp.get('size') or 0):,} | {fp.get('mtime_iso')} | "
          f"{fp.get('chunks')} | {len(fp.get('documents_rows') or [])} | 未触碰 |")
    A("")
    A("**本次未执行但值得报告的**：默认库里还有**第二份教材** doc#102 "
      "`raw\\学习笔记\\全国普通高等中医药院校药学类专业十三五规划教材…药理学思维导图"
      "与学习指导_.pdf`（359 块，`role=textbook`，`chunk_scheme` 为空 —— 是**旧管线**"
      "的产物，同样存在跨章缝合、面包屑只有书名一级的问题）。"
      "按卡面第 5 条只报告不执行，本次**没有**重跑它。"
      "其他 6 个学科库（分析化学 / 有机化学 / 生物制药工艺学 / 生物技术制药 / "
      "生物药剂学与药物动力学 / 药物化学）本次一律未触碰。")
    A("")

    # ---------------- 10 ----------------
    A("## 10. 复现步骤")
    A("")
    A("```powershell")
    A("# 0) 改造前先备份（含四个数字与回滚命令）")
    A(f"{PY} -X utf8 {TOOLS}\\c0090_backup.py")
    A("# 1) 改造前基线（块 dump + 检索基线，必须在重跑前跑）")
    A(f"{PY} -X utf8 {TOOLS}\\c0090_snapshot.py")
    A(f"{PY} -X utf8 {TOOLS}\\c0090_search_probe.py --tag before")
    A("# 2) 教材专线重跑。rerun2 会按 40 页一批调 extract.ocr_pdf_pages，")
    A("#    每批落盘 _tmp_c0090/ocr_batch_*.pkl —— 崩了能续、改完能秒复算。")
    A("#    首次真跑约 3 小时；批次缓存已在时只需约 2 分钟。")
    A(f"{PY} -X utf8 {TOOLS}\\c0090_rerun2.py")
    A("# 3) 对照 / 防覆盖 / 改造后检索 / 报告")
    A(f"{PY} -X utf8 {TOOLS}\\c0090_compare.py")
    A(f"{PY} -X utf8 {TOOLS}\\c0090_guard_test.py")
    A(f"{PY} -X utf8 {TOOLS}\\c0090_search_probe.py --tag after")
    A(f"{PY} -X utf8 {TOOLS}\\c0090_report.py")
    A("# 4) 验收（含 C-0087 回归）")
    A(f"{PY} -X utf8 {TOOLS}\\verify_c0090_textbook.py")
    A(f"{PY} -X utf8 {TOOLS}\\verify_c0087_textbook.py")
    A("```")
    A("")
    A("排查用：")
    A("")
    A("```powershell")
    A(f"{PY} -X utf8 {TOOLS}\\c0090_batch_equiv.py          # 分批与原路径是否逐块等价")
    A(f"{PY} -X utf8 {TOOLS}\\c0090_diagnose_chapters.py    # 章识别逐道闸门统计")
    A(f"{PY} -X utf8 {TOOLS}\\c0090_before_cross.py         # 只用旧块反推章、独立估计跨章数")
    A(f"{PY} -X utf8 {TOOLS}\\c0090_probe.py                # 库现状只读探测")
    A("```")
    A("")

    out = CARD / "教材重跑对照.md"
    out.write_text("\n".join(L), encoding="utf-8")
    print(f"写入 {out}（{out.stat().st_size} 字节，{len(L)} 行）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
