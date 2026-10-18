                         % baks[-1].name,
               "默认库内容与备份不一致：%s" % json.dumps(diff, ensure_ascii=False)[:400])

        # ---------- 8. 隔离写探测 ----------
        sec("8. 隔离写探测（自建临时学科 → 入库 → 断言默认库分毫未变 → 删除）")
        if args.no_probe:
            L.append("  [SKIP] 指定了 --no-probe")
        else:
            if probe_dir.exists():
                shutil.rmtree(probe_dir, ignore_errors=True)
            entry = S.create(PROBE)
            ck(entry["slug"] == PROBE, "建库成功：%s" % entry["dir"], "建库失败")
            new_db = Path(entry["dir"]) / "index" / "study.db"
            # schema 完整性（相对 db.py 的定义，而不是"和主库表数一样"：
            # 主库多出的 figure_desc 是 figures.py 惰性建的、t_tri 是历史遗留）
            want = set(__import__("re").findall(
                r"CREATE TABLE IF NOT EXISTS\s+(\w+)", db.SCHEMA))
            want |= set(__import__("re").findall(
                r"CREATE VIRTUAL TABLE IF NOT EXISTS\s+(\w+)", db.FTS_SCHEMA))
            want |= {"chunks_fts_" + x for x in ("data", "idx", "docsize", "config")}
            got = tables_of(new_db)
            ck(not (want - got), "新库 schema 完整（应有 %d 张表全在，实有 %d）" % (len(want), len(got)),
               "新库缺表：%s" % sorted(want - got))
            difc = {}
            for t in TABLES + ("meta",):
                a, b = cols_of(main_db, t), cols_of(new_db, t)
                if a != b:
                    difc[t] = {"only_main": sorted(a - b), "only_new": sorted(b - a)}
            ck(not difc, "新库逐表列集合与默认库一致（顺序无关）",
               "新库列结构与默认库不同：%s" % difc)

            S.switch(PROBE)
            ck(Path(config.DB_PATH) == new_db, "切库后 config.DB_PATH 指向新库",
               "切库无效：%s" % config.DB_PATH)
            ck(dash._knowledge_dir() == Path(entry["dir"]) / "logs" / "knowledge",
               "切库后 dashboard 知识点目录跟着切到新库",
               "切库后知识点目录没跟着切：%s" % dash._knowledge_dir())
            ck(dash._outline_dir() == Path(entry["dir"]) / "logs" / "outline",
               "切库后 dashboard 导图目录跟着切到新库",
               "切库后导图目录没跟着切：%s" % dash._outline_dir())