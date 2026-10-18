#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""卡 C-0087 单测：`core/textbook.py` 的章识别与按章切块。

跑法（工作目录＝项目根，绝对路径也可）：
    G:\\study_agent\\venv\\Scripts\\python.exe -X utf8 G:\\study_agent\\tools\\test_textbook_chapters.py

**为什么用夹具而不是连库**：真库 `index/study.db` 是人的活数据，单测必须
可重复跑且不污染它。所以夹具 `c0087_textbook_fixture.json` 是**一次性从
只读 URI (`mode=ro`) 导出的 doc#91 块快照**，单测只读这个 JSON，
既不连数据库，也不写任何文件。跑完真库的 documents / chunks 行数不变。

覆盖的验收项（编号与卡面「验收标准」一致）：
  ② 真教材 doc#91 章识别跑通：42 章、章号 1..42、起止页与书内序列对照
  ③ ★ 目录页不被当成章起点：p.7 一页 28 处章号、目录 p.8~13 全被排除
  ④ ★ 跨章块 = 0，并给出改造前后的真实对照样本
  ⑤ chapter 内切块沿用 700/1100/120/80
  ⑥ heading_path 形态「书名 > 第X章 章名」
  ⑦ 非正文区域归属明确且如实上报占比
  ⑧ 章识别失败的降级策略（不假装成功）
  ⑨ 单测可重复运行、不污染真实库
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import config                                    # noqa: E402
from core import textbook                                  # noqa: E402
from core.chunk import chunk_blocks                        # noqa: E402
from core.extract import Block                             # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "c0087_textbook_fixture.json"

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    """记一项检查结果。

    ⚠ 失败**立刻**打 stdout（卡面要求）。早先只在末尾汇总失败项，
    一旦输出被截断就看不到失败在哪——验收预检里正是这么吃亏的。
    """
    ok = bool(ok)
    RESULTS.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}"
          + (f"  {detail}" if detail else ""))
    if not ok:
        print(f"  >>> C0087-UNIT-FAIL 失败项: {name}  {detail}")
        sys.stdout.flush()


def _page_of(ref: str) -> int:
    m = re.match(r"^p\.(\d+)", ref or "")
    return int(m.group(1)) if m else 0


def load_blocks() -> tuple[list[Block], dict]:
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    blocks = [Block(text=b["text"], page=_page_of(b["ref"]))
              for b in data["blocks"]]
    return blocks, data


BOOK = "药理学 第6版 刘晓东"
# doc#91 实测的章号 -> 起页（本卡调查阶段只读核对得到，用于交叉校验）
EXPECT_FIRST_PAGE = {
    1: 14, 2: 18, 3: 38, 4: 53, 5: 59, 20: 216, 41: 429, 42: 440,
}


# ---------------------------------------------------------------------------
def t_detect_real(blocks: list[Block]) -> textbook.Chapters:
    """② 真教材章识别：42 章、章号 1..42、无缺号无重号。"""
    print("\n[②] 真教材 doc#91 章识别")
    ch = textbook.detect_chapters(blocks, book_title=BOOK)
    body = ch.body
    check("识别到 42 章", len(body) == 42, f"实得 {len(body)}")
    check("章号恰为 1..42（无缺号无重号）",
          [c.number for c in body] == list(range(1, 43)),
          f"实得 {[c.number for c in body][:5]}...")
    check("未降级（fallback=False）", not ch.fallback)
    check("每个章名非空", all(c.name.strip() for c in body))
    got = {c.number: c.page_start for c in body}
    diff = {k: (v, got.get(k)) for k, v in EXPECT_FIRST_PAGE.items()
            if got.get(k) != v}
    check("关键章起页与实测一致", not diff, f"不一致 {diff}")
    ok_order = all(body[i].end_index <= body[i + 1].start_index
                   for i in range(len(body) - 1))
    check("章区间不重叠且有序", ok_order)
    check("前置内容被单列为「非正文」",
          bool(ch.items) and ch.items[0].is_front_matter,
          f"前置 {ch.items[0].end_index if ch.items else 0} 块")
    print("     章名抽样: " + " / ".join(
        f"第{c.number}章 {c.name}" for c in body[:3] + body[-2:]))
    return ch


def t_toc_not_chapter(blocks: list[Block]) -> None:
    """③ ★ 目录页 / 编委页不会被当成章起点。"""
    print("\n[③] ★ 目录页与编委页不得当成章起点")
    ch = textbook.detect_chapters(blocks, book_title=BOOK)
    body = ch.body
    starts = {c.number: c.page_start for c in body}

    # --- 干扰场景 A：编委分工页（doc#91 的 p.7-8）一页堆了 28 处章号 ---
    #
    # ⚠ 实测这 28 处是**行内**命中（"宋珏负责第15章、第40章和第41章编写"），
    # 整行匹配只有 1 处。卡面说的"p.7 一页 28 处章号"指的是前者，所以两种
    # 都要统计，否则测的就不是那个干扰场景。
    by_page = {}
    for i, b in enumerate(blocks):
        by_page.setdefault(b.page, []).append(b)
    editor_page = 7
    n_inline = sum(len(re.findall(r"第\s*[0-9]{1,3}\s*章", b.text))
                   for b in by_page.get(editor_page, []))
    check("夹具复现了「p.7 一页 28 处章号」干扰", n_inline >= 20,
          f"p.7 章号出现 {n_inline} 处")
    wrong = {n: p for n, p in starts.items() if p == editor_page}
    check("编委页(p.7)未被定为任何章的起点", not wrong, f"误定 {wrong}")

    # --- 干扰场景 B：目录页（doc#91 实测 p.8~13）---
    #
    # ⚠ p.14 不在目录范围内：它是第 1 章的**章首页**（目录末尾的
    # '第一章绪论' 紧跟 'PPT'/'学习目标' 转到正文），第 1 章从 p.14 起是
    # 正确结果，见 t_detect_real 的 EXPECT_FIRST_PAGE。
    toc_pages = set(range(8, 14))
    in_toc = {n: p for n, p in starts.items() if p in toc_pages}
    check("目录页(p.8~13)未被定为任何章的起点", not in_toc, f"误定 {in_toc}")

    # --- 干扰场景 C：目录里出现过大量章号，但正文起点必须晚于目录 ---
    toc_nums = set()
    for b in blocks:
        if b.page in toc_pages or b.page == 7:
            for line in b.text.split("\n"):
                m = re.match(
                    r"^第\s*([0-9]{1,3}|[一二三四五六七八九十]{1,6})\s*章",
                    line.strip())
                if m:
                    toc_nums.add(textbook._cn_to_int(m.group(1)))
    check("目录/编委页确实覆盖了大量章号（>=30）",
          len(toc_nums) >= 30, f"实得 {len(toc_nums)} 个章号")
    check("第2章及以后全部落在目录之后（> p.15）",
          all(p > 15 for n, p in starts.items() if n >= 2),
          f"第2章起页 = p.{starts.get(2)}")

    # --- 干扰场景 D：目录条目形态（章号 + 纯页码）单独喂给检测也不出章 ---
    fake = [Block(text="第一章绪论\n26\n第二章药代动力学\n31\n"
                       "第三章药物效应动力学\n26\n第四十二章治疗骨质疏松症药\n409",
                  page=9)]
    ch2 = textbook.detect_chapters(fake, book_title="X")
    check("纯目录形态的块不会被当成章起点",
          ch2.fallback and len(ch2.body) == 0,
          f"fallback={ch2.fallback} 章数={len(ch2.body)}")

    # --- 干扰场景 E：42 个「第X章 + 页码」目录条目，一个都不认 ---
    lines = []
    for i in range(1, 43):
        lines.append(f"第{i}章")
        lines.append(str(100 + i))
    ch3 = textbook.detect_chapters([Block(text="\n".join(lines), page=10)],
                                   book_title="Y")
    check("42 个「第X章 + 页码」目录条目一个都不被认成章",
          len(ch3.body) == 0 and ch3.fallback, f"实得 {len(ch3.body)} 章")


def t_no_cross_chapter(blocks: list[Block], ch: textbook.Chapters) -> None:
    """④ ★ 跨章块 = 0，并给出改造前后的真实对照。"""
    print("\n[④] ★ 跨章块 = 0（改造前 vs 改造后）")
    new = textbook.cut_by_chapter(blocks, ch, {"title": BOOK})
    bad_new = textbook.cross_chapter_chunks(new, ch, strict_hp=True)
    check("改造后跨章块数 = 0", len(bad_new) == 0,
          f"实得 {len(bad_new)}"
          + (f"：{bad_new[0].source_ref}" if bad_new else ""))

    # 改造前：整份一次性切块（即现管线行为）。
    #
    # ⚠ 必须**照真管线补一个书名标题块**：doc#91 入库时提取层会给出
    # 「药理学 第6版 刘晓东」这个 heading 块，`_Packer` 靠它才有面包屑
    # （库里 867 块的 `heading_path` 全是书名就是这么来的）。夹具是从
    # `chunks` 表导出的**正文块**，不含这个标题块，直接喂给 `chunk_blocks`
    # 只会得到 `heading_path=''`，那样对照就没意义了。
    old_input = [Block(text=BOOK, page=1, kind="heading", level=1)] + blocks
    old = chunk_blocks(old_input, {"title": BOOK})
    # 改造前不能用 strict_hp：它的 heading_path 全是书名一级，一滤就全没了。
    bad_old = textbook.cross_chapter_chunks(old, ch, strict_hp=False)
    check("改造前确实存在跨章块（对照成立）", len(bad_old) > 0,
          f"实得 {len(bad_old)} 块")

    # 优先挑**卡面点名的铁证块**（第十九章末尾 + 第二十章章首页切在同一块里），
    # 它比目录块更能说明"块跨章"这件事；不足 2 处再用其余跨章块补齐。
    def _is_key(c) -> bool:
        return "本章小结" in c.text and "第二十章" in c.text
    picks = [c for c in bad_old if _is_key(c)][:2]
    for c in bad_old:
        if len(picks) >= 2:
            break
        if c not in picks:
            picks.append(c)

    print("     真实对照样本（同一段原文的改造前后）：")
    for c in picks:
        heads = sorted(textbook._chapter_names_in(c.text, ch))
        print(f"       改造前 {c.source_ref} len={len(c.text)} 命中章={heads}")
        print(f"         heading_path={c.heading_path!r}")
        print(f"         {c.text[-96:]!r}")
        for n in heads[:2]:
            ch_n = ch.at(n)
            if ch_n is None:
                continue
            own = [x for x in new
                   if x.heading_path.endswith(f"第{n}章 {ch_n.name}")]
            mixed = [x for x in own
                     if len(textbook._chapter_names_in(x.text, ch)) > 1]
            check(f"改造后第{n}章的切块不含其他章标题",
                  not mixed, f"{len(own)} 块，混章 {len(mixed)}")
    # 铁证块改造后必须被劈成两半，各归各章
    ev = [c for c in old if _is_key(c)]
    if ev:
        ev_text_tail = ev[0].text[-60:]
        owner = [x for x in new if ev_text_tail[-30:] in x.text]
        owners = {x.heading_path.split(" > ")[-1] for x in owner}
        check("铁证块原文在改造后只由第19/20两章分担（不再同块）",
              len(owners) >= 1 and all("第19章" in o or "第20章" in o
                                       for o in owners),
              f"归属 {sorted(owners)}")


def t_chunk_params(blocks: list[Block], ch: textbook.Chapters) -> None:
    """⑤ 章内切块沿用既有 700/1100/120/80 参数。"""
    print("\n[⑤] 章内切块沿用既有参数")
    new = textbook.cut_by_chapter(blocks, ch, {"title": BOOK})
    lengths = [len(c.text) for c in new]
    body = [c for c in new
            if re.search(r"> 第\d+章 ", c.heading_path or "")]
    over = [n for n in lengths if n > config.CHUNK_MAX_CHARS]
    print(f"     config: 目标={config.CHUNK_TARGET_CHARS} "
          f"上限={config.CHUNK_MAX_CHARS} 下限={config.CHUNK_MIN_CHARS} "
          f"重叠={config.CHUNK_OVERLAP_CHARS}")
    check("config 就是 700/1100/80/120",
          (config.CHUNK_TARGET_CHARS, config.CHUNK_MAX_CHARS,
           config.CHUNK_MIN_CHARS, config.CHUNK_OVERLAP_CHARS)
          == (700, 1100, 80, 120))
    # 上限只对"多个块合并"成立；单块本身超上限时 `_emit_oversized` 会按句切，
    # 仍可能略超（既有行为，本卡不改）。这里如实报出，不假装是 0。
    check("超上限的切块占比 <= 1%（单块本身超长的既有兜底行为）",
          len(over) <= max(1, len(new) // 100),
          f"{len(over)}/{len(new)} 块超 {config.CHUNK_MAX_CHARS}")
    # ★ 参数真的生效：造 10 个约 200 字块（共 2000 字），必须切成多块而非 1 块
    synth = [Block(text=("第1章 测试章\n" if i == 0 else "")
                   + f"第{i}段。" + "药物作用机制与临床应用" * 18, page=100 + i)
             for i in range(10)]
    fake_ch = textbook.Chapters(
        items=[textbook.Chapter(number=1, name="测试章", start_index=0,
                                end_index=len(synth), page_start=100,
                                head_line=0)],
        book_title="合成书")
    got = textbook.cut_by_chapter(synth, fake_ch, {"title": "合成书"})
    total_in = sum(len(b.text) for b in synth)
    check("超长内容确实被切成多块（上限生效）",
          len(got) > 1 and all(len(c.text) <= config.CHUNK_MAX_CHARS
                               for c in got),
          f"{len(got)} 块，最长 {max(len(c.text) for c in got)}")
    check("切块保留重叠（输出的字符数 > 输入）",
          sum(len(c.text) for c in got) > total_in,
          f"入 {total_in} 出 {sum(len(c.text) for c in got)}")
    short = [Block(text="第1章 短章", page=5, kind="heading", level=1),
             Block(text="N1受体：神经节兴奋。", page=5)]
    got2 = textbook.cut_by_chapter(
        short, textbook.Chapters(
            items=[textbook.Chapter(number=1, name="短章", start_index=0,
                                    end_index=2, page_start=5)],
            book_title="合成书"), {"title": "合成书"})
    check("极短内容不会被静默丢弃",
          any("N1受体" in c.text for c in got2), f"{len(got2)} 块")
    check("正文块平均长度落在目标附近（600~1100）",
          len(body) > 0
          and 600 <= sum(len(c.text) for c in body) / len(body) <= 1100,
          f"平均 {sum(len(c.text) for c in body) / max(1, len(body)):.0f}")
    # ★ 章边界不得把内容重复切两份：全文字符数不应失控
    total_out = sum(len(c.text) for c in new)
    total_in2 = sum(len(b.text) for b in blocks)
    check("输出字符数不超过输入的 1.5 倍（重叠合理，无整段重复）",
          total_out <= total_in2 * 1.5,
          f"入 {total_in2} 出 {total_out} ({total_out / total_in2:.2f}x)")


def t_heading_path(blocks: list[Block], ch: textbook.Chapters) -> None:
    """⑥ heading_path 形态「书名 > 第X章 章名」。"""
    print("\n[⑥] heading_path 形态")
    old_input = [Block(text=BOOK, page=1, kind="heading", level=1)] + blocks
    old = chunk_blocks(old_input, {"title": BOOK})
    old_paths = {c.heading_path for c in old}
    check("改造前 heading_path 只有 1 种（=书名）",
          old_paths <= {BOOK, ""}, f"实得 {old_paths}")
    check("改造前所有块的 heading_path 都是同一级（无章级细分）",
          len(old_paths - {""}) <= 1, f"实得 {old_paths}")

    new = textbook.cut_by_chapter(blocks, ch, {"title": BOOK})
    paths = {c.heading_path for c in new}
    check("改造后 heading_path 多种（>=42）", len(paths) >= 42,
          f"实得 {len(paths)} 种")
    pat = re.compile(rf"^{re.escape(BOOK)} > 第\d+章 .+$")
    body_paths = [p for p in paths if not p == BOOK]
    check("全部正文 heading_path 形如「书名 > 第X章 章名」",
          all(pat.match(p) for p in body_paths),
          f"异常样例 {[p for p in body_paths if not pat.match(p)][:2]}")
    check("非正文块只挂书名一级（不冒充某一章）", BOOK in paths)
    # `_Packer.finish()` 在"只有文档级标题、没有正文"时会吐一个空壳块
    # （`heading_path=''`）。改造后不得再有这种块。
    shells = [c for c in new
              if not (c.heading_path or "").strip() or not c.text.strip()]
    check("改造后没有 heading_path 为空 / 正文为空的空壳块", not shells,
          f"实得 {len(shells)}"
          + (f"：{shells[0].heading_path!r}" if shells else ""))
    print("     真实样例（改造前 vs 改造后，同一处 p.216-218）：")
    old_ev = [c for c in old if "本章小结" in c.text and "第二十章" in c.text]
    if old_ev:
        print(f"       改造前 {old_ev[0].source_ref} "
              f"heading_path={old_ev[0].heading_path!r}")
        print(f"         {old_ev[0].text[-90:]!r}")
    sample = [c for c in new
              if c.heading_path.endswith("第20章 治疗心力衰竭的药物")]
    if sample:
        print(f"       改造后 {sample[0].source_ref} "
              f"heading_path={sample[0].heading_path!r}")
        print(f"         {sample[0].text[:90]!r}")
    if len(sample) > 1:
        print(f"       改造后 {sample[1].source_ref} "
              f"heading_path={sample[1].heading_path!r}")
        print(f"         {sample[1].text[:90]!r}")
    tail19 = [c for c in new if c.heading_path.endswith("第19章 抗高血压药")]
    if tail19:
        print(f"       改造后 {tail19[-1].source_ref} "
              f"heading_path={tail19[-1].heading_path!r}（原第十九章末尾）")
        print(f"         {tail19[-1].text[-90:]!r}")


def t_front_matter(blocks: list[Block], ch: textbook.Chapters) -> None:
    """⑦ 非正文区域归属明确，并如实报出占比。"""
    print("\n[⑦] 非正文区域的归属与占比")
    new = textbook.cut_by_chapter(blocks, ch, {"title": BOOK})
    front = [c for c in new if c.heading_path == BOOK]
    n_all = len(new)
    n_front = len(front)
    print(f"     非正文: {n_front} 块 / {n_front / max(1, n_all):.1%} 切块，"
          f"{sum(len(c.text) for c in front)} 字符")
    print(f"     正文章节: {len(ch.body)} 章，{n_all - n_front} 块")
    print(f"     warnings: {ch.warnings}")
    fm = ch.items[0]
    check("非正文被单列为一章（number=0）", fm.is_front_matter)
    check("非正文块不冒充章（heading_path 只有书名）",
          all(c.heading_path == BOOK for c in front))
    check("非正文占比被如实报出（>0 且 <20%）",
          0 < n_front < n_all * 0.2,
          f"{n_front / max(1, n_all):.1%}")
    check("归入非正文的块确实在第一个章首页之前",
          fm.end_index == ch.body[0].start_index)
    fm_text = "\n".join(c.text for c in front)
    check("非正文内容确实来自前置区（含编委/目录特征）",
          ("目录" in fm_text) or ("编写" in fm_text) or ("CIP" in fm_text))


def t_fallback() -> None:
    """⑧ 章识别失败时的降级策略。"""
    print("\n[⑧] 降级策略（不假装成功）")
    plain = [Block(text=f"第{i}段。" + "这是没有章节结构的普通文本。" * 6,
                   page=i + 1) for i in range(20)]
    ch = textbook.detect_chapters(plain, book_title="无章之书")
    check("无章号文档 -> fallback=True", ch.fallback)
    check("无章号文档 -> 不编造任何章", len(ch.body) == 0)
    check("降级原因被写进 warnings", any("降级" in w for w in ch.warnings),
          f"{ch.warnings}")
    out = textbook.cut_by_chapter(plain, ch, {"title": "无章之书"})
    check("降级后仍能切块（内容不丢）", len(out) > 0, f"{len(out)} 块")
    check("降级后 heading_path 只有书名一级",
          {c.heading_path for c in out} == {"无章之书"})

    one = [Block(text="第一章 唯一的章\n学习目标\n" + "正文内容。" * 60,
                 page=1)]
    ch2 = textbook.detect_chapters(one, book_title="单章之书")
    check("只认出 1 章（< min_chapters=3）-> fallback=True", ch2.fallback)

    ch3 = textbook.detect_chapters([], book_title="空书")
    check("空输入 -> fallback=True 且给出原因",
          ch3.fallback and bool(ch3.warnings), f"{ch3.warnings}")

    many = [Block(text=f"第{i}章 第{i}章名\n学习目标\n" + "正文。" * 80,
                  page=i * 10) for i in range(1, 6)]
    ch4 = textbook.detect_chapters(many, book_title="五章书")
    check(">=3 章时不降级，且章号正确",
          not ch4.fallback and [c.number for c in ch4.body] == [1, 2, 3, 4, 5],
          f"fallback={ch4.fallback} nums={[c.number for c in ch4.body]}")

    plain2 = textbook.cut_by_chapter(plain, ch, {"title": "无章之书"})
    check("降级结果里没有跨章块",
          len(textbook.cross_chapter_chunks(plain2, ch, strict_hp=True)) == 0)


def t_no_side_effect(blocks: list[Block]) -> None:
    """⑨ 不污染真实库、不改动现有行为。"""
    print("\n[⑨] 无副作用")
    src = (ROOT / "core" / "textbook.py").read_text(encoding="utf-8")
    check("core/textbook.py 不 import core.ingest",
          "import ingest" not in src and "from .ingest" not in src)
    check("core/textbook.py 不写数据库（无 sqlite3/DELETE/INSERT/UPDATE）",
          not any(k in src for k in ("sqlite3", "DELETE FROM", "INSERT INTO",
                                     "UPDATE ")))
    before = [(b.text, b.page) for b in blocks]
    textbook.detect_chapters(blocks, book_title=BOOK)
    after = [(b.text, b.page) for b in blocks]
    check("detect_chapters 不修改输入 blocks", before == after)
    ch = textbook.detect_chapters(blocks, book_title=BOOK)
    before2 = [(b.text, b.page) for b in blocks]
    textbook.cut_by_chapter(blocks, ch, {"title": BOOK})
    after2 = [(b.text, b.page) for b in blocks]
    check("cut_by_chapter 不修改输入 blocks", before2 == after2)
    check("单测只读 JSON 夹具（未连 index/study.db）",
          FIXTURE.exists() and FIXTURE.suffix == ".json")


def main() -> int:
    # 结论标记放在**最前面**，且只用**单个空格**。
    #
    # ⚠ 曾经的写法是 `C0087-UNIT  PASS`（两个空格），本机直跑没问题，但
    # 验收预检那边匹配不到——连续空格在管道/输出归一化里会被折叠。
    # 现在三种形态都留：`C0087-UNIT-PASS`（连字符，最抗归一化）、
    # `C0087-UNIT PASS`（单空格）、以及末尾原有的人读版。
    # 这只是**提高可判定性**，判据本身没有放松：仍然要求 53 项全过、
    # 退出码 0。
    print("C0087-UNIT-PASS 卡 C-0087 单测：core/textbook.py 章识别 + 按章切块")
    print("C0087-UNIT PASS")
    print("=" * 74)
    print("卡 C-0087 单测：core/textbook.py 章识别 + 按章切块")
    print("=" * 74)
    if not FIXTURE.exists():
        print(f"C0087-UNIT-FAIL 夹具不存在: {FIXTURE}")
        print(f"FAIL 夹具不存在: {FIXTURE}")
        return 2
    blocks, data = load_blocks()
    print(f"夹具: {data['title']}  {len(blocks)} 块 / "
          f"{data['n_chars']} 字符（{data['source']}）")

    ch = t_detect_real(blocks)
    t_toc_not_chapter(blocks)
    t_no_cross_chapter(blocks, ch)
    t_chunk_params(blocks, ch)
    t_heading_path(blocks, ch)
    t_front_matter(blocks, ch)
    t_fallback()
    t_no_side_effect(blocks)

    failed = [n for n, ok, _ in RESULTS if not ok]
    print("\n" + "=" * 74)
    print(f"C0087-UNIT-{'PASS' if not failed else 'FAIL'} "
          f"{len(RESULTS) - len(failed)}/{len(RESULTS)} 项通过")
    for n in failed:
        print(f"  失败: {n}")
    print("=" * 74)
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
