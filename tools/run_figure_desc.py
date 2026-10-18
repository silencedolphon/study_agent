# -*- coding: utf-8 -*-
"""整章语境识图驱动（R3 发布工具）：渲染整页图 → 在整章语境下逐页生成图示说明。

用途
----
把一份课件（pptx，走本机 PowerPoint COM）或 PDF（走 pymupdf）渲染成整页 PNG，
再把「整份课件的章节脉络 + 本页标题/正文 + 上下页标题」作为语境，逐页调用视觉
模型产出「图示说明」，落到 `logs/figure_desc/<文件名>/slide_XX.md`。

这些 md 与看板入库管线（`core/figures.py::ingest_document_figures`）**同源同口径**：
入库时会优先复用同一目录里已生成的 `slide_*.md`，也能按图片哈希复用整页图，
避免重复计费。本工具**不写数据库**，只写 `--out` 目录。

用法::

    set PY=<项目根>\\venv\\Scripts\\python.exe
    cd /d <项目根>
    %PY% -X utf8 tools\\run_figure_desc.py --src raw\\2镇静催眠药.pptx
    %PY% -X utf8 tools\\run_figure_desc.py --src raw\\xxx.pdf --pages 1,2,3
    %PY% -X utf8 tools\\run_figure_desc.py --src raw\\xxx.pptx --limit 5 --no-thinking
    %PY% -X utf8 tools\\run_figure_desc.py --src raw\\xxx.pptx --slides logs\\xxx_pages
    %PY% -X utf8 tools\\run_figure_desc.py --src raw\\xxx.pptx --dry-run
    %PY% -X utf8 tools\\run_figure_desc.py --src raw\\xxx.pptx --skip-text-heavy --text-heavy-chars 700
    %PY% -X utf8 tools\\run_figure_desc.py --src raw\\xxx.pptx --only-with-images

`--dry-run` 只做「渲染/选页/建语境」并落盘计划（`dryrun-plan.json` + `contexts/pXX.txt`），
**不调用任何视觉 API、不花钱**，用于离线检查参数与页选择。

说明：文字层解析不出文字的页（空白页/纯图页）会用「空正文语境」补上再识图——
只要整页图在，就不因为解析缺席而漏掉这一页。

退出码：0 = 全部选中页成功（或 dry-run 正常完成）；1 = 有页失败；2 = 参数/环境/渲染致命错误。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001 —— 输出被重定向成非 TextIOWrapper 时忽略
    pass

from core import config, figures  # noqa: E402

DEFAULT_TEXT_HEAVY = 700
MIN_DESC_CHARS = 30          # 与 figures._probe_page 的"空输出"判据同一个数

EXAMPLES = """\
示例：
  %(prog)s --src raw\\2镇静催眠药.pptx --dry-run          # 离线看会跑哪些页、花 0 元
  %(prog)s --src raw\\2镇静催眠药.pptx --pages 1,2,3      # 只跑前 3 页
  %(prog)s --src raw\\2镇静催眠药.pptx --limit 5          # 只跑前 5 页
  %(prog)s --src raw\\2镇静催眠药.pptx --slides logs\\2镇静催眠药_pages  # 复用已有整页图
产物：<out>/slide_XX.md（每页识图正文）+ <out>/results.json（汇总）；不写数据库。
"""


def _human(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    if seconds < 60:
        return "%.1fs" % seconds
    m, s = divmod(int(round(seconds)), 60)
    if m < 60:
        return "%dm%02ds" % (m, s)
    h, m = divmod(m, 60)
    return "%dh%02dm" % (h, m)


def _parse_pages(spec: str) -> list[int]:
    """把 "1,2,3"（容忍中文逗号/空格）解析成 [1,2,3]；非法输入抛 ValueError。"""
    pages: list[int] = []
    for part in spec.replace("，", ",").replace(" ", ",").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            pages.append(int(part))
        except ValueError:
            raise ValueError("--pages 里有非数字: %r" % part) from None
    return pages


def _default_outdir(src: Path) -> Path:
    """默认输出目录：与入库复用口径一致（logs/figure_desc/<清理后文件名>）。"""
    log_dir = Path(getattr(config, "LOG_DIR", ROOT / "logs"))
    return log_dir / "figure_desc" / figures._safe_dirname(src.stem)


def _load_slides_dir(sdir: Path) -> dict[int, Path]:
    """从已有整页图目录读 {页号: png}；文件名约定 slide_XX.png。"""
    slides: dict[int, Path] = {}
    if not sdir.is_dir():
        return slides
    for p in sorted(sdir.glob("slide_*.png")):
        try:
            page = int(p.stem.split("_")[1])
        except (IndexError, ValueError):
            continue
        slides[page] = p
    return slides


def _write_description_md(outdir: Path, page: int, title: str, body: str) -> None:
    """写一页的 md（格式与入库管线一致：首行 `# pN · 标题`）。

    入库侧 `figures.load_descriptions()` 靠首行取标题、正文取其余行，
    所以这里不能改格式；只写这一份文件，不落库。
    """
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / ("slide_%02d.md" % page)).write_text(
        "# p%d · %s\n\n%s\n" % (page, (title or "").strip(), (body or "").strip()),
        encoding="utf-8")


def _describe_one(img: Path, ctx: figures.PageContext, *, model: str | None,
                  thinking: bool) -> tuple[figures.FigureResult | None, float, int]:
    """识图一页；空输出时按更大 max_tokens 重试一次（与入库管线同判据）。

    返回 (最终结果, 本页总费用, 本页实际调用次数)。
    不抛异常：网络/解析错误由 describe_page 收在 FigureResult.error 里。
    """
    r = figures.describe_page(img, ctx, model=model, thinking=thinking)
    calls = 1
    cost = r.cost_cny
    if not r.ok or len((r.text or "").strip()) >= MIN_DESC_CHARS:
        return r, cost, calls
    caps = getattr(config, "FIGURE_RETRY_MAX_TOKENS", 32768)
    r2 = figures.describe_page(img, ctx, model=model, thinking=thinking,
                               max_tokens=caps)
    calls += 1
    cost += r2.cost_cny
    if r2.ok and len((r2.text or "").strip()) >= MIN_DESC_CHARS:
        return r2, cost, calls
    return r, cost, calls


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="run_figure_desc.py",
        description="整章语境识图驱动：渲染整页图 → 逐页生成图示说明（不写数据库）。",
        epilog=EXAMPLES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--src", required=True, help="原始文档：pptx 或 pdf（必填）")
    ap.add_argument("--slides", default="",
                    help="已有整页图目录（slide_XX.png）；留空则自动渲染")
    ap.add_argument("--pages", default="",
                    help="只跑这些页，逗号分隔；空=全部（如 1,2,3）")
    ap.add_argument("--limit", type=int, default=0,
                    help="只跑前 N 页（0=不限）")
    ap.add_argument("--no-thinking", action="store_true",
                    help="关闭视觉模型推理模式（更便宜、通常更短）")
    ap.add_argument("--out", default="",
                    help="输出目录；默认 logs/figure_desc/<文件名>")
    ap.add_argument("--model", default="",
                    help="视觉模型名；留空用设置中心里的 DEEPSEEK_VISION_MODEL")
    ap.add_argument("--skip-text-heavy", action="store_true",
                    help="跳过正文超过阈值的页（图上信息多半已在文字层）")
    ap.add_argument("--text-heavy-chars", type=int, default=DEFAULT_TEXT_HEAVY,
                    help="--skip-text-heavy 的正文阈值（默认 %d 字）" % DEFAULT_TEXT_HEAVY)
    ap.add_argument("--only-with-images", action="store_true",
                    help="只跑「这页有图片对象」的页（**仅 pptx 有效**；PDF 无可靠判据）")
    ap.add_argument("--dry-run", action="store_true",
                    help="只做渲染/选页/建语境并落盘计划，不调用任何视觉 API、不花钱")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    src = Path(args.src).expanduser()
    if not src.is_file():
        print("错误：源文件不存在: %s" % src, file=sys.stderr)
        print("      请检查路径（支持 pptx / pdf）。", file=sys.stderr)
        return 2
    # ★ 必须转绝对路径：pptx 渲染走 PowerPoint COM，COM 的 Presentations.Open
    #   不接受相对路径（实测报 0x80070003 "The system cannot find the path specified"），
    #   Export 的落盘路径同理。PDF 分支不受影响，但统一转掉更省心。
    src = src.resolve()

    kind = "pdf" if src.suffix.lower() == ".pdf" else "pptx"
    outdir = (Path(args.out).expanduser() if args.out else _default_outdir(src)).resolve()

    print("源文件 : %s" % src)
    print("格式   : %s（%s）" % (kind, "PDF 内置渲染" if kind == "pdf" else "PowerPoint COM 渲染"))
    print("输出   : %s" % outdir)
    print("模式   : %s" % ("dry-run（不调用视觉 API）" if args.dry_run else "实跑（逐页调用视觉模型）"))
    print()

    # ---- 1. 整章语境 ----
    t_start = time.perf_counter()
    try:
        deck_title, stexts = figures.extract_pages(src, kind, fill_empty_pages=True)
        ctxs = figures.build_contexts(stexts, deck_title)
    except Exception as e:  # noqa: BLE001
        print("错误：读取/解析 %s 失败：%s: %s" % (src.name, type(e).__name__, e),
              file=sys.stderr)
        return 2
    if not ctxs:
        print("错误：这份资料读不出任何一页文字/页数（kind=%s），无从识图。" % kind,
              file=sys.stderr)
        print("      若是扫描件，请先确认文件完好；PDF 可尝试装本地 OCR 后重入库。",
              file=sys.stderr)
        return 2
    print("课件   : %s" % deck_title)
    print("页数   : %d（建立了 %d 个页面语境）" % (len(stexts), len(ctxs)))

    # ---- 2. 整页图（--slides 复用 / 否则本机渲染）----
    # ★ 先渲染再选页：PPT 里可能有一页文字层抽不出来（空白页/纯图页），
    #   这类页在文字层解析里缺席，但**整页图仍在**——识图工具不该因此漏掉它。
    #   第 3 步会给这些页补一个空正文语境。
    render_error = ""
    slides: dict[int, Path] = {}
    if args.slides:
        sdir = Path(args.slides).expanduser()
        slides = _load_slides_dir(sdir)
        if not slides:
            render_error = "整页图目录里没有 slide_*.png: %s" % sdir
            print("⚠ %s" % render_error)
        else:
            print("复用整页图：%s（%d 张）" % (sdir, len(slides)))
    else:
        print("渲染整页图 → %s ..." % outdir)
        t0 = time.perf_counter()
        try:
            outdir.mkdir(parents=True, exist_ok=True)
            slides = figures.render_pages(src, outdir, kind=kind)
        except Exception as e:  # noqa: BLE001
            render_error = "%s: %s" % (type(e).__name__, e)
            print("⚠ 整页渲染失败：%s" % render_error)
            print("  " + figures.render_backend_problem(render_error))
            slides = {}
        else:
            print("  渲染完成 %d 页，耗时 %s" % (len(slides), _human(time.perf_counter() - t0)))

    # ---- 3. 给「有整页图、但文字层没有语境」的页补空上下文 ----
    extra_pages = sorted(set(slides) - set(ctxs))
    if extra_pages:
        first_ctx = next(iter(ctxs.values()))
        for p in extra_pages:
            ctxs[p] = figures.PageContext(page=p, title="", body="",
                                          deck_title=deck_title,
                                          deck_outline=list(first_ctx.deck_outline))
        print("⚠ 补空语境：%d 页没有文字层（%s），按空正文处理"
              % (len(extra_pages), extra_pages[:12]))

    # ---- 4. 选页（与断头旧版一致：--pages → --limit → 过滤）----
    all_pages = sorted(ctxs)
    wanted = list(all_pages)
    if args.pages:
        try:
            picked = _parse_pages(args.pages)
        except ValueError as e:
            print("错误：%s" % e, file=sys.stderr)
            return 2
        unknown = [p for p in picked if p not in ctxs]
        if unknown:
            print("⚠ --pages 里有不存在的页，已忽略：%s" % unknown)
        wanted = [p for p in picked if p in ctxs]
    if args.limit and args.limit > 0:
        wanted = wanted[: args.limit]
    if not wanted:
        print("错误：选页后没有可跑的页。", file=sys.stderr)
        return 2

    # ---- 5. 过滤（skip-text-heavy / only-with-images）----
    has_img: dict[int, bool] | None = None
    if args.only_with_images:
        if kind == "pptx":
            try:
                has_img = figures.page_has_image(src, kind)
            except Exception as e:  # noqa: BLE001
                print("⚠ 判断页内是否有图片对象失败（不过滤）：%s" % e)
                has_img = None
            else:
                if has_img is None:
                    print("⚠ 这份 pptx 上没有可靠判据，--only-with-images 不生效")
        else:
            print("⚠ PDF 没有可靠判据，--only-with-images 已忽略（照常跑选中页）")

    selected: list[int] = []
    skipped: list[dict] = []
    for p in wanted:
        ctx = ctxs[p]
        body_len = len((ctx.body or "").strip())
        if has_img is not None and not has_img.get(p, True):
            skipped.append({"page": p, "reason": "无图片对象"})
            continue
        if (args.skip_text_heavy
                and body_len > max(0, int(args.text_heavy_chars))):
            skipped.append({"page": p, "reason": "正文 %d 字 > 阈值 %d"
                                               % (body_len, int(args.text_heavy_chars))})
            continue
        selected.append(p)

    print()
    print("选中页 : %d / %d 页（跳过 %d 页）" % (len(selected), len(wanted), len(skipped)))
    for item in skipped:
        print("  - p%d 跳过：%s" % (item["page"], item["reason"]))
    missing_slide = [p for p in selected if p not in slides]
    if missing_slide:
        print("⚠ 这些页没有整页图，将跳过：%s" % missing_slide)

    # ---- 5a. dry-run：只落盘计划与语境，不调模型 ----
    if args.dry_run:
        outdir.mkdir(parents=True, exist_ok=True)
        ctxdir = outdir / "contexts"
        ctxdir.mkdir(parents=True, exist_ok=True)
        for p in selected:
            try:
                (ctxdir / ("p%02d.txt" % p)).write_text(ctxs[p].to_prompt(),
                                                        encoding="utf-8")
            except OSError:
                pass
        plan = {
            "dry_run": True,
            "src": str(src),
            "kind": kind,
            "deck_title": deck_title,
            "n_pages": len(stexts),
            "n_contexts": len(ctxs),
            "wanted": wanted,
            "selected": selected,
            "skipped": skipped,
            "missing_slides": missing_slide,
            "render_ok": bool(slides) and not render_error,
            "render_error": render_error,
            "slides_found": len(slides),
            "n_slide_files": len(slides),
            "model": args.model or getattr(config, "DEEPSEEK_VISION_MODEL", ""),
            "thinking": not args.no_thinking,
            "out": str(outdir),
            "visual_api_calls": 0,
            "cost_cny": 0.0,
            "elapsed_sec": round(time.perf_counter() - t_start, 2),
        }
        (outdir / "dryrun-plan.json").write_text(
            json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
        print()
        print("dry-run 计划已落盘：%s" % (outdir / "dryrun-plan.json"))
        print("  页面语境：%s（%d 份）" % (ctxdir, len(selected)))
        print("  视觉 API 调用：0 次；费用：¥0.0000（未调用模型、未写数据库）")
        return 0

    # ---- 5b. 实跑 ----
    if not figures.load_deepseek_key():
        print("错误：没有可用的 DeepSeek API Key（环境变量 DEEPSEEK_API_KEY 或 "
              "~/.dsh/.credentials.yaml），无法调用视觉模型。", file=sys.stderr)
        print("      只想离线检查选页/渲染，请加 --dry-run。", file=sys.stderr)
        return 2
    if not slides:
        print("错误：没有整页图可识图（渲染失败且未提供 --slides）。", file=sys.stderr)
        return 2

    model = args.model.strip() or None
    thinking = not args.no_thinking
    todo = [p for p in selected if p in slides]
    print()
    print("开始逐页识图（模型=%s，thinking=%s，共 %d 页）"
          % (model or getattr(config, "DEEPSEEK_VISION_MODEL", "默认"),
             "开" if thinking else "关", len(todo)))
    outdir.mkdir(parents=True, exist_ok=True)

    results: list[dict] = []
    errors: list[dict] = []
    total_cost = 0.0
    total_calls = 0
    described = 0
    no_info = 0
    for i, p in enumerate(todo, 1):
        ctx = ctxs[p]
        label = (ctx.title or "").strip()[:30]
        print("[%d/%d] p%d %s ..." % (i, len(todo), p, label), end="", flush=True)
        t0 = time.perf_counter()
        try:
            r, cost, calls = _describe_one(slides[p], ctx, model=model,
                                           thinking=thinking)
        except Exception as e:  # noqa: BLE001 —— 单页异常不许拖垮整批
            msg = "%s: %s" % (type(e).__name__, e)
            errors.append({"page": p, "error": msg})
            print(" 失败（%s）" % msg)
            continue
        total_calls += calls
        total_cost += cost
        elapsed = time.perf_counter() - t0
        if r is None or not r.ok:
            msg = (r.error if r is not None else "unknown error") or "unknown error"
            errors.append({"page": p, "error": msg[:300]})
            print(" 失败（%s）" % msg[:120])
            continue
        body = (r.text or "").strip()
        if not body:
            errors.append({"page": p, "error": "空输出"})
            print(" 失败（空输出）")
            continue
        _write_description_md(outdir, p, ctx.title, body)
        described += 1
        is_no = figures.is_no_info(body)
        if is_no:
            no_info += 1
        results.append({"page": p, "title": ctx.title, "chars": len(body),
                        "no_info": is_no, "model": r.model or (model or ""),
                        "cost_cny": round(cost, 6),
                        "elapsed_sec": round(r.elapsed, 1)})
        print(" OK（%d 字，¥%.4f，%s）" % (len(body), cost, _human(elapsed)))

    elapsed_all = time.perf_counter() - t_start
    summary = {
        "src": str(src),
        "kind": kind,
        "deck_title": deck_title,
        "n_pages": len(stexts),
        "wanted": wanted,
        "selected": selected,
        "skipped": skipped,
        "missing_slides": missing_slide,
        "n_slide_files": len(slides),
        "model": model or getattr(config, "DEEPSEEK_VISION_MODEL", ""),
        "thinking": thinking,
        "out": str(outdir),
        "described": described,
        "no_info": no_info,
        "failed": len(errors),
        "errors": errors,
        "visual_api_calls": total_calls,
        "cost_cny": round(total_cost, 6),
        "elapsed_sec": round(elapsed_all, 1),
        "results": results,
    }
    (outdir / "results.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")

    print()
    print("=" * 60)
    print("完成：成功 %d 页 / 失败 %d 页（跳过 %d 页，无整页图 %d 页）"
          % (described, len(errors), len(skipped), len(missing_slide)))
    if no_info:
        print("      其中 %d 页模型判定「本页无知识性图示」（入库时会自动跳过）"
              % no_info)
    print("      视觉 API 调用 %d 次；费用 ¥%.4f；总耗时 %s"
          % (total_calls, total_cost, _human(elapsed_all)))
    print("      产物目录：%s（slide_*.md + results.json）" % outdir)
    if errors:
        print("      失败页：%s" % ", ".join("p%d" % e["page"] for e in errors))
    hard_fail = len(errors) + len(missing_slide)
    return 0 if hard_fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
