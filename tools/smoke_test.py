    """大文件/整目录导入，以及上传通道的大小限制。

    这组来自一次真实失败：用户要把桌面一个 645MB / 36 文件的目录传进 raw/，
    浏览器直接报 "fail to fetch"——请求根本没到服务端。
    原因是旧实现把整个请求体读进内存，且单次上传上限 512MB
    而其中单个 PDF 就有 513.9MB。

    结论不是"把上限调大"，而是**本机场景不该走 HTTP**：
    看板就在本机，可以直接按路径把文件拷进 raw/，没有大小限制、不吃内存。
    """
    import shutil

    print("\n[15] 大文件与整目录导入")
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        import dashboard as D
    except Exception as e:
        check("可加载看板模块", False, f"{type(e).__name__}: {e}")
        return

    tmp_raw = config.ROOT / "_import_probe" / "raw"
    src_dir = config.ROOT / "_import_probe" / "src"
    if tmp_raw.parent.exists():
        shutil.rmtree(tmp_raw.parent, ignore_errors=True)
    tmp_raw.mkdir(parents=True, exist_ok=True)
    src_dir.mkdir(parents=True, exist_ok=True)
    tmp_db = config.INDEX_DIR / "import_probe.db"
    saved = (config.RAW_DIR, config.DB_PATH, config.NOTES_DIR)
    try:
        if tmp_db.exists():
            tmp_db.unlink()
        config.RAW_DIR = tmp_raw
        config.DB_PATH = tmp_db
        config.NOTES_DIR = tmp_raw / "notes"

        # 造一个带子目录与不支持格式的源目录
        (src_dir / "a.md").write_text("# 甲\n\n这是一份用于导入测试的文档。\n",
                                      encoding="utf-8")
        (src_dir / "b.txt").write_text("乙：另一份测试文档。\n", encoding="utf-8")
        (src_dir / "legacy.ppt").write_bytes(b"\xd0\xcf\x11\xe0fake")   # 旧格式
        sub = src_dir / "子目录"
        sub.mkdir(exist_ok=True)
        (sub / "c.md").write_text("# 丙\n\n子目录里的文档也要被递归导入。\n",
                                  encoding="utf-8")

        # 1) 预览不应写入任何文件
        pv = D.api_import_path(str(src_dir), sub="导入测试", dry_run=True)
        check("导入预览统计正确", pv["dry_run"] and pv["count"] == 4,
              f"{pv['count']} 个文件")
        check("预览不写入文件", not (tmp_raw / "导入测试").exists())
        unsupported = [f for f in pv["files"] if not f["supported"]]
        check("预览标出旧格式不支持", len(unsupported) == 1,
              unsupported[0]["name"] if unsupported else "无")

        # 2) 实际导入：递归、分目录、改名旧格式
        r = D.api_import_path(str(src_dir), sub="导入测试")
        check("整目录递归导入", r["copied"] == 4, f"复制 {r['copied']} 个")
        dest = tmp_raw / "导入测试"
        got = sorted(f.name for f in dest.iterdir())
        check("子目录文件也被导入", "c.md" in got, str(got))

        # 3) 重名不覆盖而是加序号
        r2 = D.api_import_path(str(src_dir / "a.md"), sub="导入测试")
        got2 = sorted(f.name for f in dest.iterdir())
        check("同名文件不覆盖", "a_1.md" in got2 and r2["renamed"] >= 1, str(got2))

        # 4) 错误处理
        for bad, label in [("", "空路径"),
                           (str(config.ROOT / "_不存在的目录"), "不存在的路径")]:
            try:
                D.api_import_path(bad)
                check(f"拒绝{label}", False, "未报错")
            except ValueError:
                check(f"拒绝{label}", True)

        # 5) 上传上限：必须明确拒绝并给出可操作的建议
        check("HTTP 上传上限已知且合理",
              0 < D.MAX_UPLOAD_BYTES <= 512 * 1024 * 1024,
              f"{D.MAX_UPLOAD_BYTES // 1048576} MB")

                   text="t", heading_path="a > b", source_ref="p.1")
        check("Hit.is_extra 默认 False", h.is_extra is False,
              "普通命中不该被当成补全块")
        # 空输入不该炸
        check("补全对空输入安全",
              S3.expand_neighbors(None, []) == [], "")
    except Exception as e:  # noqa: BLE001
        check("有邻页补全函数", False, f"{type(e).__name__}: {e}")


def check_deck_outline() -> None:
    """AI 全局结构分析（`core/outline.py` + `chunks.topic`）。

    这一层改的是"内容挂在导图的哪个位置"，没法靠"路径非空"判断对错，
    但有几条**确定性的契约**必须守住，否则跨页检索会静默退化：

    1. **父子关系**：p.27 是 p.26 底下一项时，它的路径必须以 p.26 的
       路径为前缀——这是用户要的"结构信息"，丢了就再也搜不到 p.27；
    2. `topic()` 是路径**去掉本页名**，父子页的 topic 是前缀关系（不是相等）；
    3. 面包屑**不许重复课件名**（模型常把课件名当第一级）；
    4. 切块要把节路径带进 `meta["topic"]`，且**每一块挂的是本页的主题**；
    5. 没有 outline 时（老管线）一个字段都不许多出来——老库那 6000 多块
       的行为必须逐字节不变。
    """
    print("\n[25] AI 全局结构分析：思维导图路径与跨页分组")
    try:
        from core import chunk as C4
        from core import extract as E4
        from core import outline as O4
    except Exception as e:  # noqa: BLE001
        check("可加载 outline 模块", False, f"{type(e).__name__}: {e}")
        return
    check("可加载 outline 模块", True, "")

    deck = "镇静催眠抗焦虑药"
    # 关键：p.27/p.28 是 p.26 的**子节点**，不是它的兄弟。
    ol = O4.DeckOutline(doc_id=1, deck=deck, pages={
        2: [deck, "概念与镇静催眠作用"],
        25: [deck, "巴比妥类", "分类"],
        26: [deck, "巴比妥类", "药理作用与临床应用"],
        27: [deck, "巴比妥类", "药理作用与临床应用", "镇静催眠作用"],
        28: [deck, "巴比妥类", "药理作用与临床应用", "抗惊厥抗癫痫与麻醉"],
        30: ["巴比妥类", "体内过程"],        # 模型没写课件名的那种
    })

    # 1) 父子关系必须体现在面包屑里（**这是整个功能的要害**）
    bc26, bc27 = ol.breadcrumb(26), ol.breadcrumb(27)
    check("子页面包屑以父页为前缀", bc27.startswith(bc26 + " > "),
          f"{bc27}")
    check("父页面包屑带自己的名字", bc26.endswith("药理作用与临床应用"), bc26)
    # 反过来：兄弟页之间不该互为前缀
    check("兄弟页不互为前缀",
          not ol.breadcrumb(28).startswith(bc27 + " > ")
          and not bc27.startswith(ol.breadcrumb(28) + " > "), "")

    # 2) topic = 路径去掉本页名；父子页是前缀关系而不是相等
    check("topic 去掉本页名", ol.topic(26) == "巴比妥类", repr(ol.topic(26)))
    check("子页 topic 以父页 topic 为前缀",
          ol.topic(27).startswith(ol.topic(26) + " > "), repr(ol.topic(27)))
    check("父子 topic 不相等（所以判据必须用前缀）",
          ol.topic(26) != ol.topic(27), "")
    # 模型写不写课件名都不影响
    check("课件名写不写都不影响 topic", ol.topic(30) == "巴比妥类",
          repr(ol.topic(30)))

    # 3) 面包屑不许重复课件名
    check("面包屑不重复课件名", bc27.count(deck) == 1, bc27)

    # 4) 切块：路径要落进面包屑与 meta，且**每块挂的是本页的主题**
    try:
        blocks = [
            E4.Block(text=deck, page=1, para=0, kind="heading", level=1),
            E4.Block(text="药理作用与临床应用", page=26, para=0,
                     kind="heading", level=2),
            E4.Block(text="普遍性中枢抑制作用，随剂量增加而增强", page=26, para=1),
            E4.Block(text="镇静催眠作用", page=27, para=0,
                     kind="heading", level=2),
            E4.Block(text="小剂量镇静，中剂量催眠，大剂量抗惊厥", page=27, para=1),
        ]
        ck = C4.chunk_blocks(blocks, doc_meta={"slides": 27}, outline=ol)
        by_ref = {c.source_ref: c for c in ck}
        check("切块把路径写进面包屑",
              "p.27" in by_ref
              and by_ref["p.27"].heading_path.startswith(bc26 + " > "),
              f"{by_ref.get('p.27').heading_path if 'p.27' in by_ref else '无'}")
        check("p.26 的块挂本页主题（不是下一页的）",
              "p.26" in by_ref
              and by_ref["p.26"].heading_path.endswith("药理作用与临床应用"),
              f"{by_ref.get('p.26').heading_path if 'p.26' in by_ref else '无'}")
        check("p.27 的块挂本页主题（不是下一页的）",
              "p.27" in by_ref
              and by_ref["p.27"].heading_path.endswith("镇静催眠作用"),
              f"{by_ref.get('p.27').heading_path if 'p.27' in by_ref else '无'}")
        check("切块带上节路径",
              {c.meta.get("topic") for c in ck} == {"巴比妥类",
                                                    "巴比妥类 > 药理作用与临床应用"},
              f"{ {c.meta.get('topic') for c in ck} }")
    except Exception as e:  # noqa: BLE001
        check("切块把路径写进面包屑", False, f"{type(e).__name__}: {e}")

    # 4b) 页面上**没有标题行**的页也要能切到自己的主题。
    # extract 只把有标题行的页标成 heading，纯图页/表格页没有标题块；
    # 只靠 heading 触发切换的话，这些页会一直挂着上一页的主题。
    try:
        b2 = [
            E4.Block(text=deck, page=1, para=0, kind="heading", level=1),
            E4.Block(text="第一页没有标题行，讲概念。", page=2, para=1),
            E4.Block(text="第二页也没有标题行，讲分类。", page=3, para=1),
        ]
        ol2 = O4.DeckOutline(doc_id=1, deck=deck, pages={
def main() -> int:
    print("=" * 70)
    print("study_agent 端到端验收")
    print("=" * 70)
    print(f"夹具目录: {FIXTURES}")

    # ---------- 1. 摄入 ----------
    print("\n[1] 摄入")
    fix = ensure_fixtures()
    check("测试夹具已就绪", (fix / "demo").exists() and (fix / "formats").exists(),
          f"{fix.name}/（测试自带，不依赖 raw/）")
    if TEST_DB.exists():
        TEST_DB.unlink()
    # 数据库与笔记目录都必须重定向。
    # 早期只改了数据库，笔记仍写进真实的 notes/——测试会污染用户的笔记，
    # 表现是"清空笔记后它又自己出现了"。
    _redirect_dirs()
    config.NOTES_DIR = FIXTURES / "notes"
    config.NOTES_DIR.mkdir(parents=True, exist_ok=True)
    conn = db.connect(TEST_DB)
    db.init_db(conn)
    check("使用独立测试库", str(TEST_DB).endswith("smoke_test.db"),
          "index\\smoke_test.db（不触碰真实资料库）")
    check("笔记写入夹具目录而非真实 notes/",
          Path(config.NOTES_DIR) == FIXTURES / "notes",
          str(config.NOTES_DIR))
    # 同时摄入 demo（中文检索用）与 formats（真实格式解析用）
    rep = ingest.ingest_paths(
        conn, [fix / "demo", fix / "formats"], verbose=False
    )
    n_ok = rep.added
    check("摄入新增文档", n_ok == 7, rep.summary())

    n_docs = conn.execute("SELECT COUNT(*) AS n FROM documents").fetchone()["n"]
    n_chunks = conn.execute("SELECT COUNT(*) AS n FROM chunks").fetchone()["n"]
    n_vecs = conn.execute("SELECT COUNT(*) AS n FROM embeddings").fetchone()["n"]
    check("文档数正确", n_docs == 7, f"{n_docs} 个")
    check("切块已生成", n_chunks > 0, f"{n_chunks} 块")
    check("每块都有向量", n_vecs == n_chunks, f"{n_vecs} 向量 / {n_chunks} 块")

    # ---------- 2. 出处完整性 ----------
    print("\n[2] 出处完整性（引用可回链的前提）")
    no_ref = conn.execute(
        "SELECT COUNT(*) AS n FROM chunks WHERE source_ref IS NULL OR source_ref=''"
    ).fetchone()["n"]
    check("所有切块都有 source_ref", no_ref == 0, f"缺失 {no_ref} 块")
    no_head = conn.execute(
        "SELECT COUNT(*) AS n FROM chunks WHERE heading_path IS NULL OR heading_path=''"
    ).fetchone()["n"]
    check("所有切块都有标题路径", no_head == 0, f"缺失 {no_head} 块")

    # ---------- 3. 中文切块完整性 ----------
    print("\n[3] 中文切块质量")
    oversize = conn.execute(
        "SELECT COUNT(*) AS n FROM chunks WHERE char_len > ?",
        (config.CHUNK_MAX_CHARS + 50,),
    ).fetchone()["n"]
    check("无超长切块", oversize == 0, f"超限 {oversize} 块 (上限 {config.CHUNK_MAX_CHARS})")
    tiny = conn.execute(
        "SELECT COUNT(*) AS n FROM chunks WHERE char_len < 20"