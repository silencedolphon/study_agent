"""Local scan of exercise sections and printed answer keys."""
from __future__ import annotations

import re
import unicodedata
from collections import defaultdict

from . import textbook

_PREFIX = r"(?:[一二三四五六七八九十百\d]+[、.)．]\s*)?"
_TYPES = {
    "single": r"单(?:项)?选择题|单选题|A[1-4]?型题",
    "multiple": r"多(?:项)?选择题|多选题|X型题",
    "choice": r"选择题|不定项选择题|配伍题|B[1-4]?型题",
    "definition": r"名词解释(?:题)?|术语解释",
    "short": r"简答题|短答题", "written": r"问答题|论述题",
    "calculation": r"计算题", "judgment": r"判断(?:题)?|是非题",
    "blank": r"填空题", "case": r"案例(?:分析)?题|分析题",
}
_SECTION = re.compile(_PREFIX + "(" + "|".join(v for v in _TYPES.values()) +
                      r")(\s*(?:[（(][^）)]{1,32}[）)]|[:：]))?$")
_EXERCISES = re.compile(_PREFIX + r"(?:课后|章后|复习|自测|课堂|综合|本章)?"
                        r"(?:练习(?:题)?|习题|思考题|复习思考题|测试题|自测题|试题|单元测试)"
                        r"(?:及(?:参考)?答案)?[:：]?$")
_ANSWER_NAME = (r"(?:参考答案|标准答案|正确答案|答案|答题要点|答题参考|参考解答|参考解析|解题思路|试题解析|题解)"
                r"(?:[与及和、/]?\s*(?:解析|解答|详解|示例|提示|要点))?")
_ANSWER = re.compile(_PREFIX + r"(?:(?:全书|各章|本章|试题|试卷|习题|练习题|思考题|附录)\s*)?"
                     + _ANSWER_NAME + r"(?:\s*[:：]\s*(.*))?$")
_TYPED_ANSWER = re.compile(_PREFIX + "(" + "|".join(_TYPES.values()) + r")\s*" + _ANSWER_NAME + r"[:：]?$")
_STOP = re.compile(r"(?:本章小结|小结|参考文献|知识拓展|学习目标|知识要点|要点概览|知识梳理|正文)[:：]?$")
_PAPER = re.compile(r"(?:第\s*[一二三四五六七八九十百\d]+\s*套|(?:模拟|历年|期末|期中)(?:考试)?(?:试卷|试题|卷))"
                    r"[\s（一二三四五六七八九十百\d）()甲乙ABⅠⅡ-]*(?:参考答案|答案及解析|答案)?$")
_NUM = r"(?:题\s*)?(?:(\d{1,4})\s*[.、)．:：]|[（(]\s*(\d{1,4})\s*[）)])"
_NUMBER = re.compile(r"^\s*" + _NUM + r"(?!(?<=\.)\d)")
_PRINTED_ANSWER = re.compile(r"^\s*(\d{1,3})\s*[.、](?!\d)\s*(\S.*)$")
_PROSE_ANSWERS = {"definition", "short", "written", "calculation", "case", "blank"}
_KEY = re.compile(r"(?<![\w.])" + _NUM + r"\s*([A-H]{1,8}|正确|错误|[√×])(?=\s|$|[,，;；、])", re.I)
_KEY_RANGE = re.compile(r"(\d{1,4})\s*[-—~～–至]\s*(\d{1,4})\s*[:：.、]?\s*([A-H]+)", re.I)
_INQUIRY = re.compile(r"^\s*" + _NUM + r"\s*(?:请|试|简述|论述|说明|比较|计算|分析|阐述|指出|列举|何谓|如何|为什么)"
                      r"|^请(?:解释|回答|计算|比较|简述|列举)|[?？]\s*$|^\s*" + _NUM +
                      r".{3,}[（(]\s*[）)]\s*$|^\s*" + _NUM + r".*_{2,}")
_OPTIONS_LINE = re.compile(r"^[A-H][.、:)]\s*\S", re.I)


def _plain(line):
    return unicodedata.normalize("NFKC", line).strip().strip("#*【】[] ").strip()


def section(line):
    line = _plain(line)
    if _ANSWER.fullmatch(line):
        return "answers", ""
    typed = _TYPED_ANSWER.fullmatch(line)
    if typed:
        return "answers", next(key for key, pattern in _TYPES.items() if re.fullmatch(pattern, typed[1]))
    if _EXERCISES.fullmatch(line):
        return "questions", ""
    match = _SECTION.fullmatch(line)
    if match:
        kind = next(key for key, pattern in _TYPES.items() if re.fullmatch(pattern, match[1]))
        return ("answers" if match[2] and re.search(r"答案|解析|要点", match[2]) else "section"), kind
    if _STOP.fullmatch(line):
        return "body", ""
    return "", ""


def _chapter(line):
    match = textbook._CHAPTER_LINE.fullmatch(_plain(line))
    return str(textbook._cn_to_int(match[1])) if match else ""


def number_at_start(text):
    match = _NUMBER.match(unicodedata.normalize("NFKC", str(text or "")))
    return str(int(match[1] or match[2])) if match else ""


def number_for_stem(line, stem):
    match = _NUMBER.match(unicodedata.normalize("NFKC", line))
    prefix = re.sub(r"\s+", "", unicodedata.normalize("NFKC", stem))[:12]
    if match and len(prefix) >= 4 and _plain(match.string[match.end():]).replace(" ", "").startswith(prefix):
        return str(int(match[1] or match[2]))
    return ""


def _region(row, lines, mode, scope, kind):
    return {**row, "text": "\n".join(lines).strip(), "mode": mode, "scope": scope, "type": kind}


def regions(rows, material):
    """Remember question/answer boundaries across indexed chunks and OCR page headers."""
    chapter, paper, kind, mode, appendix = "", "", "", "body", False
    result, declared = [], False
    for row in rows:
        crumbs = str(row["heading"] or "").split(">")
        folder_chapter = next((value for value in map(_chapter, crumbs[1:]) if value), "")
        if folder_chapter and folder_chapter != chapter and not appendix:
            chapter, kind, mode = folder_chapter, "", "body"
        lines = list(row["text"].splitlines())
        clean = [_plain(line) for line in lines]
        buffer = []

        def flush():
            if not buffer:
                return
            # Catch stand-alone exercises outside a named section without scanning all prose with AI.
            scope = ":".join(filter(None, (paper, f"chapter-{chapter}" if chapter else "")))
            text_lines = [_plain(line) for line in buffer]
            has_choices = (any(number_at_start(line) for line in text_lines) and
                           sum(bool(_OPTIONS_LINE.match(line)) for line in text_lines) >= 2)
            if mode != "body" or has_choices or any(_INQUIRY.search(line) for line in text_lines):
                block = _region(row, buffer, mode if mode != "body" else "questions", scope, kind)
                if block["text"]:
                    result.append(block)
            buffer.clear()

        for raw_line, line in zip(lines, clean):
            next_mode, next_kind = section(line)
            next_chapter = _chapter(line)
            chapter_heading = textbook._CHAPTER_LINE.fullmatch(line) if next_chapter else None
            inline_answer = _ANSWER.fullmatch(line)
            if inline_answer and inline_answer[1] and mode == "questions" and not number_at_start(inline_answer[1]):
                buffer.append(raw_line)
                continue
            if chapter_heading and _ANSWER.fullmatch(chapter_heading[2].strip()):
                flush()
                chapter, kind, mode, declared = next_chapter, "", "answers", True
                continue
            if next_mode or next_chapter or _PAPER.fullmatch(line):
                flush()
            if next_chapter:
                if next_chapter != chapter:
                    chapter, kind = next_chapter, ""
                    mode = "answers" if appendix else "body"
            elif _PAPER.fullmatch(line):
                new_paper = re.sub(r"(?:参考答案|答案及解析|答案)$", "", line).strip()
                if new_paper != paper or "答案" in line:
                    paper, kind, chapter = new_paper, "", ""
                    mode = "answers" if "答案" in line else "body"
                    appendix = False
            elif next_mode:
                declared = declared or next_mode != "body"
                if next_mode == "answers":
                    appendix = appendix or not chapter and not paper or line.startswith(("全书", "各章", "附录"))
                    if appendix and line.startswith(("全书", "各章", "附录")):
                        chapter = ""
                elif next_mode == "questions":
                    appendix = False
                mode = ("answers" if mode == "answers" else "questions") if next_mode == "section" else next_mode
                kind = next_kind
                if inline_answer and inline_answer[1]:
                    # Classify the normalized label, retain the exact OCR answer text.
                    buffer.append(re.split(r"[:：]", raw_line, maxsplit=1)[1])
            else:
                buffer.append(raw_line)
        flush()
    if not declared and material in {"exam", "courseware"}:
        # A slide deck or a bare test paper may not label its questions at all.
        return [_region(row, [row["text"]], "questions", next((
            f"chapter-{value}" for value in map(_chapter, row["heading"].split(">")[1:]) if value), ""), "")
                for row in rows]
    return result


def local_keys(part):
    """Parse compact answer-key entries only inside a marked answer section."""
    answers, complete = [], True
    for row in part:
        for line in row["text"].splitlines():
            line = unicodedata.normalize("NFKC", line).strip()
            if not line:
                continue
            span = _KEY_RANGE.fullmatch(line)
            if span and row["type"] in {"", "single", "choice"}:
                first, last = int(span[1]), int(span[2])
                if last - first + 1 == len(span[3]):
                    answers.extend({"number": str(num), "answer": value.upper(), "scope": row["scope"],
                                    "type": row["type"], "answer_refs": [{"chunk_id": row["id"], "ref": row["ref"]}]}
                                   for num, value in enumerate(span[3], first))
                    continue
            matches = list(_KEY.finditer(line))
            remainder = _KEY.sub("", line).strip(" \t,，;；、")
            if not matches or remainder:
                complete = False
                continue
            for match in matches:
                answers.append({"number": str(int(match[1] or match[2])), "answer": match[3].upper(),
                                "scope": row["scope"], "type": row["type"], "answer_refs": [
                                    {"chunk_id": row["id"], "ref": row["ref"]}]})
    return answers, complete and bool(answers)


def numbered_answers(part):
    """Read fully numbered prose sections, retaining the chunks across page seams."""
    groups = []
    for row in part:
        key = row["scope"], row["type"]
        if not key[0] or key[1] not in _PROSE_ANSWERS:
            return []
        if not groups or key != groups[-1][0]:
            groups.append((key, []))
        lines = groups[-1][1]
        incoming = [(line.strip(), row) for line in row["text"].splitlines() if line.strip()]
        if not incoming:
            continue
        # An isolated printed page number at the start of a continuation is not answer text.
        if (key[1] != "calculation" and lines and len(incoming) > 1
                and re.fullmatch(r"[0-9]{1,4}", _plain(incoming[0][0]))
                and re.search(r"[\u3400-\u9fff]$", lines[-1][0])
                and re.match(r"[\u3400-\u9fff]", incoming[1][0])):
            incoming = incoming[1:]
        old = [_plain(line) for line, _ in lines]
        new = [_plain(line) for line, _ in incoming]
        overlap = next((n for n in range(min(len(old), len(new)), 0, -1) if old[-n:] == new[:n]), 0)
        lines.extend(incoming[overlap:])

    answers = []
    for (scope, kind), lines in groups:
        if not lines:
            return []
        current = None
        expected = 1
        for line, row in lines:
            match = _PRINTED_ANSWER.fullmatch(unicodedata.normalize("NFKC", line))
            if match:
                if int(match[1]) != expected:
                    return []
                expected += 1
                # Keep the source spelling and formulas, including full-width punctuation.
                current = {"number": str(int(match[1])), "lines": [re.split(r"[.．、]", line, maxsplit=1)[1].strip()],
                           "scope": scope, "type": kind, "refs": {}}
                answers.append(current)
            elif current is None:
                return []
            else:
                current["lines"].append(line)
            current["refs"][row["id"]] = {"chunk_id": row["id"], "ref": row["ref"]}
    return [{"number": a["number"], "scope": a["scope"], "type": a["type"],
             "answer": "\n".join(a["lines"]), "answer_refs": list(a["refs"].values())} for a in answers]


def link_answers(items, entries, blocked=()):
    """Only pair unambiguous numbered answers within the same source and matching scope/type."""
    numbered = defaultdict(list)
    for item in (*items, *blocked):
        value = str(item.get("number") or "")
        number = str(int(value)) if value.isascii() and value.isdigit() else number_at_start(value)
        if number:
            numbered[number].append(item)
    proposals = defaultdict(list)
    for answer in entries:
        scope, kind = answer["scope"], answer["type"]
        candidates = [q for q in numbered[answer["number"]]
                      if (not scope or scope == q.get("scope") or "scope" not in q) and
                      (not kind or kind == q.get("type") or "type" not in q or
                       kind == "choice" and q.get("type") in {"single", "multiple"})]
        if len(candidates) == 1:
            proposals[id(candidates[0])].append(answer)
    linked = 0
    for item in items:
        choices = proposals[id(item)]
        if item.get("answer") or len({_plain(a["answer"]) for a in choices}) != 1:
            continue
        item.update(answer=choices[0]["answer"], answer_refs=choices[0]["answer_refs"])
        linked += 1
    return linked
