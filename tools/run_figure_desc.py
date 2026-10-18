def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True, help="原始文档（pptx 或 pdf）")
    ap.add_argument("--slides", default="", help="已有的整页图目录；留空则自动渲染")
    ap.add_argument("--pages", default="", help="只跑这些页，逗号分隔；空=全部")
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 页")
    ap.add_argument("--no-thinking", action="store_true")
    ap.add_argument("--out", default="")
    ap.add_argument("--model", default="")
    ap.add_argument("--skip-text-heavy", action="store_true",
                    help="跳过正文超过阈值的页（图上信息多半已在文字层）")
    ap.add_argument("--text-heavy-chars", type=int, default=700)
    ap.add_argument("--only-with-images", action="store_true",
                    help="只跑「这页有图片对象」的页（**仅 pptx 有效**）")
    args = ap.parse_args()

    src = Path(args.src)
    if not src.exists():
        print(f"源文件不存在: {src}")
        return
    kind = "pdf" if src.suffix.lower() == ".pdf" else "pptx"
    print(f"源文件: {src.name}")
    print(f"格式  : {kind}")

    # ---- 语境 ----
    deck_title, stexts = figures.extract_pages(src, kind)
    ctxs = figures.build_contexts(stexts, deck_title)
    print(f"课件  : {deck_title}")
    print(f"页数  : {len(stexts)}（构建了 {len(ctxs)} 个页面上下文）")
    print()

    # ---- 整页图 ----
    if args.slides:
        sdir = Path(args.slides)
        slides = {int(p.stem.split("_")[1]): p
                  for p in sorted(sdir.glob("slide_*.png"))}
        print(f"复用已有整页图: {sdir}（{len(slides)} 张）")
    else:
        sdir = config.ROOT / "logs" / f"{src.stem[:20]}_pages"
        print(f"渲染整页图 -> {sdir} ...")
        t0 = time.perf_counter()
        slides = figures.render_pages(src, sdir, kind=kind)
        print(f"  渲染完成 {len(slides)} 页，耗时 {time.perf_counter()-t0:.1f}s")
    print()

    # ---- 选页 ----
    want = ([int(x) for x in args.pages.split(",") if x.strip()]
            if args.pages else sorted(ctxs))
    if args.limit:
        want = want[: args.limit]