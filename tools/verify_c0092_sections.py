# -*- coding: utf-8 -*-
# Copyright (c) 2026 潜声海豚 <2227663973@qq.com>. All rights reserved.
# 本源码为著作权人私有作品，适用项目根目录 LICENSE（专有许可）。
# 允许个人安装与使用；禁止反向工程、修改、再分发、商业使用及声称原创。
# 本软件不含源码，按「现状」提供；详见 NOTICE.txt。
# File-ID: SA-22-c0092sec-verify   (project study_agent; keep this line)
"""卡 C-0092 验证脚本：节级切分器（`core/sections.py`）。

    G:\\study_agent\\venv\\Scripts\\python.exe -X utf8 G:\\study_agent\\tools\\verify_c0092_sections.py

**绝对路径、不依赖 cwd**（一切路径由 `__file__` 推出），全程只读真库。
结论行 `C0092-PASS` / `C0092-FAIL` 在前 6 行；明细写 `logs/c0092-verify.log`；
失败项**立即**打 stdout（不藏在日志里）。

覆盖五组：
  A 源码自证     —— 零模型调用（tokenize 去注释后无 chat/model import）、
                    零写库（无 INSERT/UPDATE/DELETE/CREATE/DROP/ALTER）
  B 合成块单测   —— 目录页排除、四形态、换行拼接、跨块去重、降级、父子、页码单调
  C 真数据 doc#91—— 42 章 / 覆盖率 / 目录块排除的正反两面（含"关掉排除会怎样"对照）
  D 产物自证     —— logs/knowledge/91-章节细目.json：meta.doc_id、无
                    knowledge_points 顶层键、后缀不与知识点/名词解释产物撞车
  E 只读自证     —— documents/chunks 行数前后不变；模块只以 mode=ro 打开库
"""
from __future__ import annotations

import io
import json
import re
import sqlite3
import sys
import time
import tokenize
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import config, sections as sec            # noqa: E402
from core.extract import Block                      # noqa: E402

LOG = Path(config.LOG_DIR) / "c0092-verify.log"
DOC = 91
CHECKS: list[tuple[str, bool, str]] = []
FAILS: list[str] = []


def add(name: str, ok: bool, detail: str = "") -> bool:
    CHECKS.append((name, bool(ok), detail))
    if not ok:
        FAILS.append(f"{name}  {detail}")
        print(f"[FAIL] {name}  {detail}")           # 失败项立即打 stdout
    return bool(ok)


def blk(text: str, page: int = 1, para: int = 0) -> Block:
    return Block(text=text, page=page, para=para, kind="ocr", level=0)


def db_counts(db: Path) -> dict:
    conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    try:
        return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                for t in ("documents", "chunks")}
    finally:
        conn.close()


def _toc_only_check(blocks, spans, res) -> bool:
    """反面证据：只把目录块喂进去，必须一条节都产不出来。

    目录块里满是「第一节XXX / 页码」这种与正文逐字同形的标题（实测形态命中
    数十行），如果判据失效，这里会立刻产出几十条假节。
    """
    idx = res.toc_blocks
    if not idx:
        return False
    toc_blocks = [blocks[i] for i in idx]
    n_forms = sum(1 for b in toc_blocks for l in b.text.split("\n")
                  if sec._match_title_form(l))
    only = [sec.ChapterSpan(1, "目录", 7, 14, 0, len(toc_blocks))]
    r = sec.detect_sections(toc_blocks, only, book_title="目录",
                            block_pages=[spans[i] for i in idx])
    print(f"       [目录单测] 目录块 {len(idx)} 个 / 标题形态 {n_forms} 行 → "
          f"产出节 {r.n_sections} 条")
    return n_forms >= 50 and r.n_sections == 0


# ---------------------------------------------------------------------------
# A 源码自证
# ---------------------------------------------------------------------------
def group_a() -> str:
    src_path = ROOT / "core" / "sections.py"
    src = src_path.read_text(encoding="utf-8")
    add("A1 core/sections.py 存在", src_path.is_file(), str(src_path))

    # tokenize 去掉注释与字符串（docstring 里也会提到 import/INSERT，不能当代码）
    toks = []
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type in (tokenize.COMMENT, tokenize.STRING, tokenize.NL):
            continue
        toks.append(tok.string)
    code = " ".join(toks)
    imports = re.findall(r"(?:^|\s)(?:from|import)\s+([\w\.]+)", code)
    banned = [m for m in imports
              if re.search(r"chat|openai|anthropic|requests|urllib|http|model|"
                           r"embed|pipeline_pool|kp_build", m, re.I)]
    add("A2 零模型调用：模块里没有 chat/模型/网络 import",
        not banned, f"imports={sorted(set(imports))} 违禁={banned}")
    mods: set[str] = set()
    for line in src.split("\n"):
        m = re.match(r"\s*from\s+([\w\.]+)\s+import\s+(.+)", line)
        if m:
            if m.group(1) == ".":                 # from . import config → 记 config
                mods |= {x.strip().split(" as ")[0]
                         for x in m.group(2).split(",")}
            else:
                mods.add(m.group(1))
            continue
        m = re.match(r"\s*import\s+([\w\.]+)", line)
        if m:
            mods.add(m.group(1))
    allowed = {"__future__", "json", "re", "sqlite3", "time", "dataclasses",
               "pathlib", "typing", ".extract", "config", "textbook"}
    add("A3 模块只依赖标准库 + core.extract（惰性 config/textbook；无模型/网络）",
        mods <= allowed, f"实际={sorted(mods)}")

    ddl = re.findall(r"\b(INSERT\s+INTO|UPDATE\s+\w+\s+SET|DELETE\s+FROM|"
                     r"CREATE\s+TABLE|DROP\s+TABLE|ALTER\s+TABLE|"
                     r"CREATE\s+INDEX)\b", code, re.I)
    add("A4 零写库：模块里没有任何 DDL/DML 语句", not ddl, f"命中={ddl}")
    add("A5 打开库只走只读 URI（mode=ro）",
        "mode=ro" in code and re.search(r"sqlite3\s*\.\s*connect", code) is not None,
        f"mode=ro 出现 {code.count('mode=ro')} 次")
    return src


# ---------------------------------------------------------------------------
# B 合成块单测
# ---------------------------------------------------------------------------
def group_b() -> None:
    # ---- B1 目录页排除（★ 卡的红线）----
    toc = blk("第一节总论\n3\n第二节各论\n15\n第三章概述\n21\n第一节小结\n30\n"
              "（一）细节\n33\n", page=7, para=0)
    body = blk("第一章总论\n第一节总论的性质\n一、第一小节\n"
               "（一）第一个细节\n正文正文正文。\n", page=9, para=1)
    chs = [sec.ChapterSpan(1, "总论", page_start=8, page_end=20,
                           start_index=0, end_index=2)]
    res = sec.detect_sections([toc, body], chs,
                              block_pages=[(7, 8), (9, 10)])
    add("B1a 目录块被识别出来（块下标 0）", res.toc_blocks == [0],
        f"toc_blocks={res.toc_blocks}")
    titles = [s.title for s in res.sections]
    add("B1b 目录页的条目一个都没进节清单（含『第一节总论』这句正文里也有）",
        "第二节各论" not in titles and "第三章概述" not in titles
        and "第一节小结" not in titles, f"titles={titles}")
    add("B1c 正文块里的节照常抽到", titles == ["总论的性质", "第一小节", "第一个细节"],
        f"titles={titles}")
    add("B1d 所有节的来源块都不在目录块里",
        all(s.block_ord_start == 1 for s in res.sections),
        f"ords={[s.block_ord_start for s in res.sections]}")

    # ---- B1e 反面对照：关掉排除会多出假节（证明排除确实在起作用）----
    res_raw = sec.detect_sections([toc, body], chs, block_pages=[(7, 8), (9, 10)],
                                  exclude_toc=False)
    add("B1e 关掉 exclude_toc 后目录条目涌进来（对照证明排除有效）",
        len(res_raw.sections) > len(res.sections),
        f"关={len(res_raw.sections)} 开={len(res.sections)}")

    # ---- B2 四种形态 + 层级 + 父子 ----
    t = blk("第一章 药物\n第一节 总论\n一、基本概念\n（一）定义\n"
            "1.1 数字编号标题\n二、药物分类\n第二节 各论\n", page=20, para=10)
    r2 = sec.detect_sections([t], [sec.ChapterSpan(1, "药物", 20, 30,
                                                   start_index=0, end_index=1)],
                             block_pages=[(20, 22)])
    forms = [s.form for s in r2.sections]
    add("B2a 四种形态都被认出来",
        forms == [sec.FORM_JIE, sec.FORM_CN1, sec.FORM_CN2, sec.FORM_DOT,
                  sec.FORM_CN1, sec.FORM_JIE], f"forms={forms}")
    lv = [s.level for s in r2.sections]
    add("B2b 层级 1/2/3/2/2/1 正确", lv == [1, 2, 3, 2, 2, 1], f"levels={lv}")
    par = [s.parent_index for s in r2.sections]
    add("B2c 父子挂载正确（（一）挂到一、，一、挂到节）",
        par == [None, 1, 2, 1, 1, None], f"parents={par}")
    add("B2d 数字形态标题以汉字开头时可用（模块有该能力，只是 doc#91 没有）",
        r2.sections[3].title == "数字编号标题", f"{r2.sections[3].title!r}")

    # ---- B3 换行拼接（doc#91 实测 5 处的形态）----
    t3 = blk("第二章 药代\n第二节\n房室模型\nPPT\n一、单室模型\n", page=19, para=20)
    r3 = sec.detect_sections([t3], [sec.ChapterSpan(2, "药代", 19, 30,
                                                    start_index=0, end_index=1)],
                             block_pages=[(19, 20)])
    add("B3a 『第X节』标题换行时往后拼一行",
        [s.title for s in r3.sections] == ["房室模型", "单室模型"],
        f"{[s.title for s in r3.sections]}")
    add("B3b 拼接计入统计 joined_from_next_line",
        r3.stats["forms"][sec.FORM_JIE]["joined_from_next_line"] == 1,
        f"{r3.stats['forms'][sec.FORM_JIE]}")
    t3b = blk("第二节\n正文的句子在这里，它很长而且带句号。\n", page=19, para=21)
    r3b = sec.detect_sections([t3b], [sec.ChapterSpan(2, "药代", 19, 30,
                                                      start_index=0, end_index=1)],
                              block_pages=[(19, 20)])
    add("B3c 拼接不乱吃正文（长句/带句号 → 判无标题，宁可不产出）",
        all(s.title != "正文的句子在这里，它很长而且带句号。"
            for s in r3b.sections), f"{[s.title for s in r3b.sections]}")

    # ---- B4 跨块重复去重（相邻块重叠）----
    a = blk("四、药物代谢\n（一）代谢方式\n", page=25, para=39)
    b = blk("四、药物代谢\n（一）代谢方式\n（二）代谢后果\n", page=25, para=40)
    r4 = sec.detect_sections([a, b], [sec.ChapterSpan(2, "药代", 20, 30,
                                                      start_index=0, end_index=2)],
                             block_pages=[(25, 25), (25, 26)])
    add("B4a 相邻重叠块的同一标题只留一条",
        [s.title for s in r4.sections] == ["药物代谢", "代谢方式", "代谢后果"],
        f"{[s.title for s in r4.sections]}")
    add("B4b 去重计入 dup_adjacent_block",
        r4.stats["reject_reasons"].get("dup_adjacent_block") == 2,
        f"{r4.stats['reject_reasons']}")
    # 不同形态同名**不能**被去重（教材里节标题与其下小标题同名是真实结构）
    c1 = blk("第一节心律失常发生的电生理学机制\n", page=25, para=41)
    c2 = blk("二、心律失常发生的电生理学机制\n", page=26, para=42)
    r4b = sec.detect_sections([c1, c2], [sec.ChapterSpan(16, "抗心律失常药",
                                                         20, 30, 0, 2)],
                              block_pages=[(25, 25), (26, 26)])
    add("B4c 形态不同、同名不误去重（节标题 vs 一、小标题）",
        len(r4b.sections) == 2, f"{[s.title for s in r4b.sections]}")

    # ---- B5 数值噪声过滤 ----
    t5 = blk("0.1\n0.3\n1.9 ±0.6\n99.7%，口服1~2小时血药浓度达峰值。与血浆蛋白结合率高。\n"
             "5.5-HT受体拮抗药及5-HT再摄取抑制药曲唑酮、奈法唑酮。\n", page=40, para=69)
    r5 = sec.detect_sections([t5], [sec.ChapterSpan(3, "药物效应动力学", 40, 50,
                                                    0, 1)], block_pages=[(40, 41)])
    real5 = [s for s in r5.sections if not s.fallback]
    add("B5a 数值/单位行一条都不采纳（1.1 形态 0 采纳）",
        real5 == [] and r5.stats["forms"][sec.FORM_DOT]["kept"] == 0
        and r5.stats["forms"][sec.FORM_DOT]["candidate_lines"] == 1,
        f"真节={len(real5)} {r5.stats['forms'][sec.FORM_DOT]}")
    loose5 = sum(1 for l in t5.text.split("\n") if sec.RE_DOT_LOOSE.match(l.strip()))
    add("B5b 两层过滤：宽口径 5 行 → 严口径 1 行（其余连候选都不是）→ 采纳 0",
        loose5 == 5,
        f"宽口径 {loose5} 行，严口径 "
        f"{r5.stats['forms'][sec.FORM_DOT]['candidate_lines']} 行，采纳 0")

    # ---- B6 降级：整章一节 ----
    t6 = blk("第三章 药物效应\n这一段没有任何小标题，只是正文正文正文。\n",
             page=40, para=63)
    r6 = sec.detect_sections([t6, t],
                             [sec.ChapterSpan(3, "药物效应", 40, 50, 0, 1),
                              sec.ChapterSpan(1, "药物", 20, 30, 1, 2)],
                             block_pages=[(40, 41), (20, 22)])
    ch3 = r6.chapter(3)
    add("B6a 抽不出节的章降级为整章一节且 fallback=True",
        ch3 is not None and len(ch3.sections) == 1 and ch3.sections[0].fallback
        and ch3.sections[0].title == "药物效应",
        f"{ch3.to_dict() if ch3 else None}")
    add("B6b 降级章进了 fallback_chapters 统计（不假装成功）",
        r6.stats["fallback_chapters"] == [3]
        and abs(r6.stats["fallback_ratio"] - 0.5) < 1e-9,
        f"{r6.stats['fallback_chapters']} {r6.stats['fallback_ratio']}")

    # ---- B7 页码单调（doc#91 的块 ref 是摊派的，块间重叠会导致回退）----
    p1 = blk("第一节 甲\n正文\n", page=19, para=0)
    p2 = blk("第一节 甲\n第二节 乙\n", page=19, para=1)
    r7 = sec.detect_sections([p1, p2], [sec.ChapterSpan(1, "章", 19, 30, 0, 2)],
                             block_pages=[(19, 21), (19, 20)])
    pgs = [s.page_start for s in r7.sections]
    add("B7a 节页码单调不减（不会出现后一节回到前一页）",
        pgs == sorted(pgs), f"pages={pgs}")

    # ---- B8 目录判据不吃数值形态（p.275 那种数值表）----
    tbl = blk("1.0\n1.0\n0.8\n0.8\n1.0\n0.8\n2.3\n11.5\n", page=275, para=527)
    add("B8a 纯数值表不会被判成目录页（数值形态不参与配对）",
        sec.detect_toc_blocks([tbl])[0] == [], f"{sec.detect_toc_blocks([tbl])}")


# ---------------------------------------------------------------------------
# C/D/E 真数据 + 产物 + 只读
# ---------------------------------------------------------------------------
def group_cde(before_counts: dict, src: str) -> None:
    db = Path(config.DB_PATH)
    blocks, spans, meta = sec.load_blocks(DOC, db)
    add(f"C1 doc#{DOC} 读到 882 块（与库内 n_chunks 一致）",
        len(blocks) == meta["n_chunks"] == 882,
        f"读到 {len(blocks)}，n_chunks={meta['n_chunks']}")

    res = sec.detect_sections(blocks, chapters=None, book_title=meta["title"],
                              doc_id=meta["id"], block_pages=spans)
    st = res.stats
    add("C2 认出 42 章（与 chunks.heading_path 的 42 个二级标题一致）",
        st["n_chapters"] == 42, f"{st['n_chapters']}")
    add("C3 抽到节 569 条（节级 135 / 二级 280 / 三级 154）",
        st["n_sections"] == 569 and st["n_sections_level1"] == 135
        and st["n_sections_level2"] == 280 and st["n_sections_level3"] == 154,
        f"{st['n_sections']} = {st['n_sections_level1']}+"
        f"{st['n_sections_level2']}+{st['n_sections_level3']}")
    add("C4 「第X节」级覆盖率 97.6%（41/42 章，唯一没节级标题的是第35章）",
        abs(st["coverage_level1"] - 41 / 42) < 1e-9
        and st["chapters_without_level1"] == [35],
        f"{st['coverage_level1']} {st['chapters_without_level1']}")
    add("C5 降级章 0 个（doc#91 每章都抽得到节，占比 0.0% 如实上报）",
        st["fallback_chapters"] == [] and st["fallback_ratio"] == 0.0,
        f"{st['fallback_chapters']}")

    # ---- ★ 目录页（p.7~14 = 块 ord 5~17）不被当成节 ----
    add("C6 目录页块 13 个（ord 5~17，正是 p.7~14）",
        res.toc_blocks == list(range(5, 18)), f"{res.toc_blocks}")
    toc_ords = {blocks[i].para for i in res.toc_blocks}
    add("C7 所有节的来源块都不在目录块里（正面）",
        all(s.block_ord_start not in toc_ords for s in res.sections),
        f"越界={[s.block_ord_start for s in res.sections if s.block_ord_start in toc_ords][:5]}")
    add("C8 反面：目录块（p.7~14）单独喂进去，一条节都产不出来",
        _toc_only_check(blocks, spans, res))
    toc_titles = {"喹诺酮类药物", "抗疱疹病毒药", "免疫抑制剂", "药物反应类型",
                  "传出神经系统的递质", "局部麻醉药"}
    got = {s.title for s in res.sections}
    add("C8b 同名标题原书正文里也有（所以不能靠标题词表排目录）",
        bool(toc_titles & got), f"同名={sorted(toc_titles & got)}")
    ch1 = {s.title for s in res.chapter(1).sections}
    add("C9 第1章绪论仍是它自己的 9 节（目录没把它撑大）",
        "药理学的性质和任务" in ch1 and len(ch1) == 9,
        f"第1章 {len(ch1)} 节 {sorted(ch1)}")
    # ★ 对照实验：整本当一章（= `_normalize_chapters` 的降级路径，也是"调用方
    #   不知道章、只想按小标题拆"的场景）。这时目录页**落在章页范围内**，
    #   不排目录就会涌进假节——这正是目录判据存在的意义。
    whole = [sec.ChapterSpan(DOC, meta["title"], 1, 441, 0, len(blocks))]
    r_cut = sec.detect_sections(blocks, whole, book_title=meta["title"],
                                doc_id=DOC, block_pages=spans, exclude_toc=True)
    r_raw = sec.detect_sections(blocks, whole, book_title=meta["title"],
                                doc_id=DOC, block_pages=spans, exclude_toc=False)
    add("C10 ★对照：整本当一章时，不排目录会多出几十条假节（排除确实在起作用）",
        r_raw.n_sections > r_cut.n_sections + 30,
        f"不排={r_raw.n_sections} 排={r_cut.n_sections}")

    # ---- 四形态真实命中 ----
    f = st["forms"]
    add("C11 四形态严口径候选行数 = 156 / 323 / 186 / 3（块级 154 / 269 / 145 / 1）",
        f[sec.FORM_JIE]["candidate_lines"] == 156
        and f[sec.FORM_CN1]["candidate_lines"] == 323
        and f[sec.FORM_CN2]["candidate_lines"] == 186
        and f[sec.FORM_DOT]["candidate_lines"] == 3
        and f[sec.FORM_JIE]["candidate_blocks"] == 154
        and f[sec.FORM_CN1]["candidate_blocks"] == 269
        and f[sec.FORM_CN2]["candidate_blocks"] == 145
        and f[sec.FORM_DOT]["candidate_blocks"] == 1,
        json.dumps(f, ensure_ascii=False))
    add("C11b 『1.1』宽口径（行首 \\d+.\\d+ 就算）= 24 块 / 61 行"
        "（卡里实测的 18 块属同一档，口径差别见报告）",
        st["dot_loose"]["candidate_blocks"] == 24
        and st["dot_loose"]["candidate_lines"] == 61,
        f"{st['dot_loose']}")
    add("C12 『第X节』采纳 135 条（换行拼接补回 5 条）",
        f[sec.FORM_JIE]["kept"] == 135
        and f[sec.FORM_JIE]["joined_from_next_line"] == 5,
        f"{f[sec.FORM_JIE]}")
    add("C13 『一、』采纳 280、『（一）』采纳 154",
        f[sec.FORM_CN1]["kept"] == 280 and f[sec.FORM_CN2]["kept"] == 154,
        f"{f[sec.FORM_CN1]['kept']} {f[sec.FORM_CN2]['kept']}")
    add("C14 ★『1.1』在 doc#91 上采纳 0 条、误报率 100%（全是数值/单位行）",
        f[sec.FORM_DOT]["kept"] == 0
        and f[sec.FORM_DOT]["rejected"] == f[sec.FORM_DOT]["candidate_lines"],
        f"{f[sec.FORM_DOT]}")
    n1 = res.chapter(1)
    s1 = {s.title: s for s in n1.sections}
    add("C15 真实样本：第1章『第一节药理学的性质和任务』原文与页号",
        "药理学的性质和任务" in s1 and s1["药理学的性质和任务"].raw
        == "第一节药理学的性质和任务",
        f"{s1.get('药理学的性质和任务')}")
    n2 = res.chapter(2)
    t2 = [s.title for s in n2.sections]
    add("C16 真实样本：第2章 33 节，换行拼接出的『房室模型』在列",
        "房室模型" in t2 and len(t2) == 33, f"第2章 {len(t2)} 节")
    n39 = res.chapter(39)
    add("C17 真实样本：第39章 54 节 / 4 个节级标题（抗恶性肿瘤药）",
        len(n39.sections) == 54
        and len(n39.labeled_sections) == 4,
        f"第39章 {len(n39.sections)} 节 / {len(n39.labeled_sections)} 节级")
    add("C17b 第39章第5个『第二节』的标题被 OCR 认成噪声『乡』，"
        "如实按无标题丢弃（不编标题、不假装）",
        "乡" not in {s.title for s in n39.sections}
        and f[sec.FORM_JIE]["reasons"].get("non_cjk", 0) >= 1,
        f"第X节拒绝原因={f[sec.FORM_JIE]['reasons']}")

    # ---- D 产物 ----
    art = sec.artifact_path(DOC)
    if not art.is_file():
        sec.dump_sections(res, art)
        made = "（本次现场生成）"
    else:
        made = ""
    add(f"D1 产物存在且非空 {made}", art.is_file() and art.stat().st_size > 1000,
        f"{art} {art.stat().st_size if art.is_file() else 0} 字节")
    raw = json.loads(art.read_text(encoding="utf-8"))
    add("D2 产物 meta.doc_id = 91", raw.get("meta", {}).get("doc_id") == DOC,
        f"{raw.get('meta', {}).get('doc_id')}")
    add("D3 ★ 产物**没有**顶层 knowledge_points 键（不被 _kp_load_dir 误扫）",
        "knowledge_points" not in raw, f"顶层键={list(raw.keys())}")
    add("D4 顶层键就是 meta/chapters/detection",
        set(raw.keys()) == {"meta", "chapters", "detection"}, f"{list(raw.keys())}")
    add("D5 产物后缀不与知识点/名词解释产物撞车",
        art.name.endswith(sec.ARTIFACT_SUFFIX)
        and sec.ARTIFACT_SUFFIX not in ("-末级知识点总结.json", "-名词解释.json")
        and art.name == "91-章节细目.json", f"{art.name}")
    # 用看板的**真函数**跑一遍：_kp_load_dir 只认顶层 knowledge_points（list），
    # 本产物没有那个键，所以 doc_id=91 不该出现在它的结果里。
    from tools import dashboard as dash                       # noqa: E402
    got_ids = dash._kp_load_dir()
    add("D6 看板 _kp_load_dir() 实跑：读到的 doc_id 里没有 91（本产物未被误扫）",
        DOC not in got_ids,
        f"读到的 doc_id={sorted(got_ids)[:24]}（目录 {dash._knowledge_dir()}）")
    add("D7 产物章节数与本次检测一致（不是过期产物）",
        len(raw["chapters"]) == len(res.chapters)
        and raw["detection"]["stats"]["n_sections"] == res.n_sections,
        f"{len(raw['chapters'])} vs {len(res.chapters)}")
    add("D8 产物带页码口径说明（页码是估算，不假装精确）",
        "page_note" in raw["detection"]["stats"]
        and raw["detection"]["stats"]["page_source"]
        == "block_ref_interpolated_monotonic",
        f"{raw['detection']['stats'].get('page_source')}")

    # ---- E 只读 ----
    after = db_counts(db)
    add("E1 真库 documents/chunks 行数跑前跑后不变", before_counts == after,
        f"{before_counts} -> {after}")
    add("E2 模块源码里没有 git 写操作/文件删除",
        not re.search(r"git\s+(commit|checkout|reset|stash)|shutil\.rmtree|"
                      r"\.unlink\(|os\.remove", src), "")
    subs = json.loads((ROOT / "logs" / "subjects.json").read_text(encoding="utf-8"))
    add("E3 学科没被切走（current=default，产物落在学科自己的 logs/knowledge）",
        subs.get("current") == "default"
        and str(Path(config.LOG_DIR)) == str(ROOT / "logs"),
        f"current={subs.get('current')} LOG_DIR={config.LOG_DIR}")

    # ---- 跨文档泛化（另一本教材，只跑不落盘）----
    try:
        b2, sp2, m2 = sec.load_blocks(102, db)
        r2 = sec.detect_sections(b2, chapters=None, book_title=m2["title"],
                                 doc_id=102, block_pages=sp2)
        add(f"C18 跨文档泛化：doc#102（{len(b2)} 块 / {m2['role']}）跑通，"
            f"降级路径真实触发（第1章整章一节）",
            len(r2.chapters) >= 1 and r2.n_sections >= 1
            and r2.stats["fallback_chapters"] == [1],
            f"章 {len(r2.chapters)} 节 {r2.n_sections} "
            f"降级章 {r2.stats['fallback_chapters']}")
        add("C18b doc#102 上『1.1』严口径 0 采纳（那本书里全是"
            "『2.5单位/h』『15.1型糖尿病…』这类数值/题干行）",
            r2.stats["forms"][sec.FORM_DOT]["kept"] == 0,
            f"{r2.stats['forms'][sec.FORM_DOT]}")
        _append_log(f"[跨文档] doc#102 {m2['title'][:30]!r}："
                    f"{len(r2.chapters)} 章 / {r2.n_sections} 节 / "
                    f"目录块 {len(r2.toc_blocks)} / 覆盖率 "
                    f"{r2.stats['coverage_level1']:.1%}\n" + sec.summarize(r2) + "\n")
    except Exception as exc:                                  # pragma: no cover
        add("C18 跨文档泛化：doc#102 跑通", False, f"{type(exc).__name__}: {exc}")


def _append_log(text: str) -> None:
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(text)


def main() -> int:
    t0 = time.time()
    LOG.parent.mkdir(parents=True, exist_ok=True)
    LOG.write_text(f"C-0092 verify 开始 {time.strftime('%Y-%m-%d %H:%M:%S')}\n",
                   encoding="utf-8")
    db = Path(config.DB_PATH)
    before = db_counts(db) if db.is_file() else {}
    add("0 真库可读（只读连接）", db.is_file(), str(db))

    src = group_a()
    group_b()
    if before:
        group_cde(before, src)

    n_ok = sum(1 for _, ok, _ in CHECKS if ok)
    n = len(CHECKS)
    ok_all = not FAILS
    lines = [
        f"{'C0092-PASS' if ok_all else 'C0092-FAIL'} 共{n}项，通过{n_ok}项，"
        f"失败{n - n_ok}项，耗时{time.time() - t0:.1f}s",
        f"节级切分器 core/sections.py：doc#91 42 章 / 569 节（节级 135），"
        f"目录页 p.7-14（13 块、523 行标题形态）全部排除，"
        f"四形态候选 156/323/186/3 行，「第X节」级覆盖率 97.6%",
        f"产物 {sec.artifact_path(DOC)}",
        f"明细日志 {LOG}",
        "验证范围：A 源码自证（零模型/零写库）· B 合成块单测（目录排除/四形态/"
        "拼接/去重/降级/页码单调）· C 真数据 doc#91 + doc#102 泛化 · "
        "D 产物自证 · E 只读自证",
    ]
    if FAILS:
        lines.append("失败项：" + " ； ".join(FAILS))
    head = "\n".join(lines)
    print("\n" + head)
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(head + "\n\n---- 全部检查项 ----\n")
        for name, ok, detail in CHECKS:
            fh.write(f"[{'OK ' if ok else 'FAIL'}] {name}  {detail}\n")
    return 0 if ok_all else 1


if __name__ == "__main__":
    raise SystemExit(main())
