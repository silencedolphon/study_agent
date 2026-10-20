"""功能矩阵：功能 → 入口/接口 → 自动化用例 → 优先级。

`tests/run_all.py` 用这里的 `tests` 字段（测试方法名片段）把 matrix 行映射到
实际 `unittest` 用例结果：某行引用的所有用例都通过才算该功能点「通过」。
字段说明：
  id      : 功能编号（报告里引用）
  feature : 人话功能名
  entry   : 用户入口 / HTTP 接口 / CLI 子命令
  kind    : health | read | write | cli | browser
  priority: P0（主链路） / P1（常用） / P2（辅助）
  tests   : 测试方法名片段列表（至少一个）
  evidence: 证据类型（http/json/cli/dom/screenshot/db）
"""

MATRIX = [
    # ---------------- 启动 / 页面 / 隔离 ----------------
    dict(id="F01", feature="服务启动与首页渲染", entry="GET /", kind="health", priority="P0",
         tests=["test_home_ok"], evidence="http"),
    dict(id="F02", feature="首页 HTML 别名与页面骨架", entry="GET /index.html", kind="health",
         priority="P0", tests=["test_index_html_alias"], evidence="http"),
    dict(id="F03", feature="页面自包含（无外部静态资源依赖）", entry="GET /（HTML 内嵌全部 JS/CSS）",
         kind="health", priority="P1", tests=["test_page_self_contained"], evidence="http"),
    dict(id="F04", feature="未知路由回落 404 JSON", entry="GET/POST /api/<未知>", kind="health",
         priority="P1", tests=["test_unknown_route_404"], evidence="json"),
    dict(id="F05", feature="测试实例指向隔离数据副本", entry="GET /api/firstrun、/api/subjects",
         kind="health", priority="P0", tests=["test_isolation_paths"], evidence="json"),
    dict(id="F06", feature="真实数据零写入（只读校验）", entry="真实 index/raw/notes/subjects 指纹对比",
         kind="health", priority="P0", tests=["test_real_data_untouched"], evidence="db"),

    # ---------------- 只读 API ----------------
    dict(id="F10", feature="学习总览统计", entry="GET /api/stats", kind="read", priority="P0",
         tests=["test_stats"], evidence="json"),
    dict(id="F11", feature="文档列表", entry="GET /api/docs", kind="read", priority="P0",
         tests=["test_docs"], evidence="json"),
    dict(id="F12", feature="文档详情（大纲/切块）", entry="GET /api/doc?id=", kind="read",
         priority="P0", tests=["test_doc_detail"], evidence="json"),
    dict(id="F13", feature="切块及上下文", entry="GET /api/chunk?id=", kind="read", priority="P1",
         tests=["test_chunk"], evidence="json"),
    dict(id="F14", feature="混合检索（向量+关键词）", entry="GET /api/search?q=&k=", kind="read",
         priority="P0", tests=["test_search"], evidence="json"),
    dict(id="F15", feature="知识点列表", entry="GET /api/kps", kind="read", priority="P1",
         tests=["test_kps"], evidence="json"),
    dict(id="F16", feature="知识点详情（出处/历史/前置）", entry="GET /api/kp?id=", kind="read",
         priority="P1", tests=["test_kp_detail"], evidence="json"),
    dict(id="F17", feature="复习队列", entry="GET /api/queue", kind="read", priority="P0",
         tests=["test_queue"], evidence="json"),
    dict(id="F18", feature="课件复习月历", entry="GET /api/deck-calendar?ym=", kind="read",
         priority="P1", tests=["test_deck_calendar"], evidence="json"),
    dict(id="F19", feature="错题本列表与统计", entry="GET /api/mistakes", kind="read",
         priority="P1", tests=["test_mistakes"], evidence="json"),
    dict(id="F21", feature="思维导图数据", entry="GET /api/mindmap?doc_id=", kind="read",
         priority="P1", tests=["test_mindmap"], evidence="json"),
    dict(id="F22", feature="名词解释产物状态", entry="GET /api/terms?doc_id=", kind="read",
         priority="P2", tests=["test_terms"], evidence="json"),
    dict(id="F23", feature="按页知识点定位", entry="GET /api/page-kps?doc_id=&page=", kind="read",
         priority="P2", tests=["test_page_kps"], evidence="json"),
    dict(id="F24", feature="知识点整理候选与成本预估", entry="GET /api/kp-build/options、/batch/options",
         kind="read", priority="P1", tests=["test_kp_build_options"], evidence="json"),
    dict(id="F25", feature="原始资料清单（raw/ 与孤儿）", entry="GET /api/raw", kind="read",
         priority="P1", tests=["test_raw"], evidence="json"),
    dict(id="F26", feature="首次运行体检（空库/凭据判定）", entry="GET /api/firstrun", kind="read",
         priority="P1", tests=["test_firstrun"], evidence="json"),
    dict(id="F27", feature="笔记列表/详情/按来源反查", entry="GET /api/notes、/api/note、/api/notes/by_kp",
         kind="read", priority="P1", tests=["test_notes_read"], evidence="json"),
    dict(id="F28", feature="学科列表与当前真实路径", entry="GET /api/subjects", kind="read",
         priority="P0", tests=["test_subjects"], evidence="json"),
    dict(id="F29", feature="设置读取（四条管线）", entry="GET /api/settings", kind="read",
         priority="P1", tests=["test_settings_read"], evidence="json"),
    dict(id="F30", feature="设置窗口提示词读取（内置/文件）", entry="GET /api/settings/prompt",
         kind="read", priority="P2", tests=["test_settings_prompt"], evidence="json"),
    dict(id="F31", feature="长任务进度快照", entry="GET /api/ingest/progress、/api/kp-build/progress、"
         "/api/ingest/batch/progress、/api/kp-build/batch/progress", kind="read", priority="P1",
         tests=["test_progress_snapshots"], evidence="json"),
    dict(id="F32", feature="分享包列表与进度", entry="GET /api/share/packages、/api/share/progress",
         kind="read", priority="P2", tests=["test_share"], evidence="json"),
    dict(id="F33", feature="问答管线状态（离线可用性）", entry="GET /api/chat/status", kind="read",
         priority="P1", tests=["test_chat_status"], evidence="json"),

    # ---------------- 写操作（仅临时副本） ----------------
    dict(id="F40", feature="知识点新建/详情联动", entry="POST /api/kp", kind="write",
         priority="P0", tests=["test_kp_create"], evidence="json+db"),
    dict(id="F41", feature="复习评分与统计联动", entry="POST /api/review", kind="write",
         priority="P0", tests=["test_review"], evidence="json"),
    dict(id="F42", feature="笔记保存→列表→按文件原位更新", entry="POST /api/note/kp、/api/note/update",
         kind="write", priority="P0", tests=["test_note_write"], evidence="json+file"),
    dict(id="F43", feature="课件答疑笔记保存", entry="POST /api/note/deck", kind="write",
         priority="P1", tests=["test_note_deck"], evidence="json"),
    dict(id="F44", feature="笔记正文路径安全（拒绝穿越）", entry="POST /api/note/update（非法 file）",
         kind="write", priority="P1", tests=["test_note_update_guard"], evidence="json"),
    dict(id="F45", feature="设置保存/重置（写沙箱 settings）", entry="POST /api/settings、/api/settings/reset",
         kind="write", priority="P1", tests=["test_settings_write"], evidence="json"),
    dict(id="F46", feature="按路径导入预览", entry="POST /api/import (dry_run)", kind="write",
         priority="P1", tests=["test_import_preview"], evidence="json"),
    dict(id="F47", feature="批量入库计划预览", entry="POST /api/ingest/batch/plan", kind="write",
         priority="P1", tests=["test_ingest_batch_plan"], evidence="json"),
    dict(id="F48", feature="孤儿数据清理预览", entry="POST /api/cleanup (dry_run)", kind="write",
         priority="P1", tests=["test_cleanup_preview"], evidence="json"),
    dict(id="F49", feature="单份删除影响预览", entry="POST /api/doc/delete (dry_run)", kind="write",
         priority="P1", tests=["test_doc_delete_preview"], evidence="json"),
    dict(id="F50", feature="文档标题改名预览", entry="POST /api/doc/rename (dry_run)", kind="write",
         priority="P2", tests=["test_doc_rename_preview"], evidence="json"),
    dict(id="F51", feature="学科切换 + 课件月历 + 入计划/标记", entry="POST /api/subjects/switch、"
         "/api/deck-enroll、/api/deck-mark", kind="write", priority="P0",
         tests=["test_subject_switch_deck"], evidence="json"),
    dict(id="F52", feature="新建/重命名学科", entry="POST /api/subjects/create、/api/subjects/rename",
         kind="write", priority="P1", tests=["test_subject_create_rename"], evidence="json"),
    dict(id="F53", feature="批量删除预览（BUG-1 修复回归）", entry="POST /api/docs/delete (dry_run)",
         kind="write", priority="P1", tests=["test_docs_delete_preview"], evidence="json"),
    dict(id="F54", feature="原始文件重命名预览（BUG-1 修复回归）", entry="POST /api/rename/raw (dry_run)",
         kind="write", priority="P1", tests=["test_rename_raw_preview"], evidence="json"),
    dict(id="F55", feature="非法查询参数健壮性（BUG-3 修复回归）", entry="GET /api/doc?id=abc 等",
         kind="write", priority="P2", tests=["test_bad_query_params_robust"], evidence="json"),
    dict(id="F56", feature="按名读取笔记（BUG-2 修复回归）", entry="GET /api/note?name=",
         kind="write", priority="P1", tests=["test_note_lookup_by_name"], evidence="json"),

    # ---------------- CLI ----------------
    dict(id="F60", feature="CLI 环境自检", entry="study.py check", kind="cli", priority="P0",
         tests=["test_check"], evidence="cli"),
    dict(id="F61", feature="CLI 学习统计", entry="study.py stats", kind="cli", priority="P1",
         tests=["test_stats"], evidence="cli"),
    dict(id="F62", feature="CLI 文档列表/大纲", entry="study.py docs [--outline ID]", kind="cli",
         priority="P1", tests=["test_docs", "test_docs_outline"], evidence="cli"),
    dict(id="F63", feature="CLI 混合检索", entry="study.py search", kind="cli", priority="P0",
         tests=["test_search"], evidence="cli"),
    dict(id="F64", feature="CLI 切块上下文", entry="study.py show", kind="cli", priority="P1",
         tests=["test_show"], evidence="cli"),
    dict(id="F65", feature="CLI 复习队列/评分", entry="study.py review", kind="cli", priority="P0",
         tests=["test_review"], evidence="cli"),
    dict(id="F66", feature="CLI 知识点增/列", entry="study.py kp add/list", kind="cli", priority="P1",
         tests=["test_kp"], evidence="cli"),
    dict(id="F67", feature="CLI 错题登记/统计", entry="study.py mistake add/stats", kind="cli",
         priority="P1", tests=["test_mistake"], evidence="cli"),
    dict(id="F68", feature="CLI 笔记回写与读回", entry="study.py note save/list/read", kind="cli",
         priority="P0", tests=["test_note"], evidence="cli"),
    dict(id="F69", feature="CLI 小 md 入库（解析/切块/向量/FTS）", entry="study.py ingest", kind="cli",
         priority="P0", tests=["test_ingest"], evidence="cli+db"),
    dict(id="F70", feature="CLI 入库幂等（重复跑跳过）", entry="study.py ingest 同文件两次", kind="cli",
         priority="P1", tests=["test_ingest_idempotent"], evidence="cli+db"),

    # ---------------- 浏览器 ----------------
    dict(id="F80", feature="Edge headless 主界面渲染", entry="Edge --headless --dump-dom /", kind="browser",
         priority="P0", tests=["test_edge_dom"], evidence="dom"),
    dict(id="F81", feature="Edge headless 截图证据", entry="Edge --headless --screenshot /", kind="browser",
         priority="P2", tests=["test_edge_screenshot"], evidence="screenshot"),

    # ---------------- 旧版 Office 入库链路（P4 / commit 430b4d8） ----------------
    dict(id="F92", feature="旧格式在 raw 列表中可入库（supported/kind/note）",
         entry="GET /api/raw（.ppt/.doc/.xls）", kind="read", priority="P0",
         tests=["test_raw_list_legacy_supported"], evidence="json"),
    dict(id="F93", feature="导入预览放开旧格式（supported + 自动转换提示）",
         entry="POST /api/import (dry_run)（.ppt/.doc/.xls）", kind="read", priority="P0",
         tests=["test_import_preview_legacy"], evidence="json"),
    dict(id="F94", feature="批处理计划纳入旧格式（unsupported=0 / 待处理）",
         entry="POST /api/ingest/batch/plan（.ppt/.doc/.xls）", kind="read", priority="P0",
         tests=["test_batch_plan_legacy"], evidence="json"),
    dict(id="F95", feature="转换产物已在库时源文件显示已入库、计划跳过（防重复转换）",
         entry="GET /api/raw + POST /api/ingest/batch/plan（x.ppt + x.pptx）",
         kind="read", priority="P0", tests=["test_converted_artifact_indexed"], evidence="json+db"),

    # ---------------- M0 止血阶段（2026-10-18；2026-10-19 用户实测后回退功能型新增） ----------------
    # 保留项：掌握度口径修复（F99）/ 文案与排版（F102）/ 移动端基础（F104）
    # 已回退：F97 新建知识点入口、F98 错题写入闭环、F100 检索规则修复、F101 场景预设、F103 导入看板入口
    dict(id="F99", feature="M0-5 掌握度四处同口径（不写库）",
         entry="GET /api/kps + /api/kp + /api/graph + /api/stats", kind="read", priority="P0",
         tests=["test_08_dashboard_fixes.TestM0Mastery"], evidence="json"),
    dict(id="F102", feature="M0-6a 用户可见文案去工程化 + 空态真实 CTA + 知识点摘要截断",
         entry="8 个视图可见文本 / 各空态主按钮 / 知识点列表 .kpsum",
         kind="ui", priority="P0", tests=["test_11_m0_scenario_copy"], evidence="json+shot"),
    dict(id="F104", feature="M0-8 移动端基础（390 无横向滚动/首屏 scrollY=0/表格卡片式）",
         entry="≤560px 媒体查询 + safeFocus", kind="ui", priority="P1",
         tests=["test_12_m0_import_ui_mobile"], evidence="json+shot"),
    dict(id="F105", feature="知识点小窗「已绑定 N 条笔记」计数（summary-kp 返回 note_count）",
         entry="GET /api/summary-kp?doc_id=&kp_id=", kind="read", priority="P1",
         tests=["test_13_summary_note_count"], evidence="json"),
    dict(id="F106", feature="知识点小窗左侧思维导图栏（高亮当前 + 点击直达，纯新增）",
         entry="知识点小窗 #mmkp-tree + GET /api/mindmap", kind="ui", priority="P1",
         tests=["test_14_mindmap_sidebar"], evidence="json+shot"),
    dict(id="F107", feature="课件整页切块（每页一块 + 识图并入同一块，旧模式可回退）",
         entry="study.py ingest + config.INGEST_PAGE_CHUNK / KB_INGEST_PAGE_CHUNK",
         kind="write", priority="P1", tests=["test_15_page_chunk"], evidence="json+db"),
    dict(id="F108", feature="设置中心改版（5 管线分组/入库方式突出/搜索/未保存计数/响应式）",
         entry="⚙ 设置中心（前端）", kind="ui", priority="P1",
         tests=["test_16_settings_ui"], evidence="json+shot"),
    dict(id="F109", feature="预习后端 v2（教材按章多产物 / 小节 schema / 新提示词）",
         entry="core/preview.py::build_chapter_preview + CLI preview_build", kind="read",
         priority="P1", tests=["test_17_preview_core"], evidence="json"),
    dict(id="F110", feature="预习页签（图谱下线 / 章节目录可收起 / 每章预习按钮 / 左树右文查看器）",
         entry="预习页 + /api/preview/{options,chapters,estimate,progress,doc} + POST /api/preview/start",
         kind="ui", priority="P1", tests=["test_18_preview_ui"], evidence="json+shot"),

    # ---------------- 工具链（新增交付） ----------------
    dict(id="F90", feature="一键健康检查（解释器/导入/语法/库/study check）",
         entry="tools/healthcheck.py --json", kind="cli", priority="P1",
         tests=["test_healthcheck_all_green"], evidence="json"),
    dict(id="F91", feature="MCP 服务自测与 stdio 握手（经 healthcheck）",
         entry="tools/serve_mcp.py --selftest / verify_mcp_handshake.py", kind="cli",
         priority="P1", tests=["test_healthcheck_all_green"], evidence="json"),
    dict(id="F96", feature="一键数据备份（备份到临时目录 + integrity + --keep 清理）",
         entry="tools/backup_data.py --json --out <tmp>", kind="cli", priority="P1",
         tests=["test_backup_data_isolated"], evidence="json"),
]

# 已修复的历史产品缺陷（保留为回归保护记录）。
# 修复前对应用例是 @unittest.expectedFailure 固化；修复后已转正为普通用例，
# 现在它们必须通过——若将来再退化，run_all 会以 FAIL 暴露。
FIXED_BUGS = [
    dict(
        id="BUG-1", severity="高", matrix_refs=["F53", "F54"],
        title="批量删除预览 / 原始文件重命名 500（Path.replace 误用）",
        symptom="资料页「批量删除」预览、raw 文件「重命名」预览/执行均返回 HTTP 500，"
                "错误体为 TypeError: Path.replace() takes 2 positional arguments but 3 were given。",
        root_cause="tools/dashboard.py::_doc_stem() 把结字符串的 `str(...).replace('\\\\','/')` "
                   "写成 `Path(str(doc_path)).replace('\\\\','/')`，而 pathlib.Path.replace 是"
                   "「改文件名」(2 参数)；_deck_dirs_for() → _doc_artifacts() 触发，"
                   "波及 /api/docs/delete 与 /api/rename/raw。",
        impact="资料管理中批量删除（含影响预览）与原始文件重命名两条真实用户路径不可用。",
        fixed_in="ec394bb fix(dashboard): 修复三个真实用户路径缺陷（验收 BUG-1/2/3）",
        fix_summary="改为 Path(str(doc_path or '').replace('\\\\', '/')).stem.strip()（与同文件 7970 行一致）。",
        verify=("回归用例：test_03_api_write.py::test_docs_delete_preview、test_rename_raw_preview（已全绿）\n"
                "Lead 侧独立验证：_work/lead/verify_bugfix.py（8898 隔离实例，12/12 通过）"),
        repro=("venv\\Scripts\\python.exe -X utf8 tests\\run_all.py --only test_docs_delete_preview\n"
               "venv\\Scripts\\python.exe -X utf8 tests\\run_all.py --only test_rename_raw_preview"),
    ),
    dict(
        id="BUG-2", severity="中", matrix_refs=["F56"],
        title="GET /api/note?name= 恒返回第一篇笔记（匹配条件写成恒真）",
        symptom="笔记页点开任意一篇、或知识点窗口「查看笔记」，返回的永远是列表里的第一篇笔记；"
                "前端编辑保存会把第一篇笔记的文件名带回去，存在写错笔记的风险。",
        root_cause="tools/dashboard.py::api_note() 循环里写成 "
                   "`if (name, n['title'], n['file'].removesuffix('.md')) in [(name, n['title'], "
                   "n['file'].removesuffix('.md'))]:`——元组在单元素列表里，条件恒为真。",
        impact="笔记按名读取（查看/编辑入口）不可用；多篇笔记时内容错位。",
        fixed_in="ec394bb fix(dashboard): 修复三个真实用户路径缺陷（验收 BUG-1/2/3）",
        fix_summary='name 与 file/title/stem 精确匹配（含大小写兜底），未命中返回 {"error": "笔记不存在"}。',
        verify=("回归用例：test_03_api_write.py::test_note_lookup_by_name、test_note_write（已全绿）\n"
                "Lead 侧独立验证：_work/lead/verify_bugfix.py 12/12 通过"),
        repro="venv\\Scripts\\python.exe -X utf8 tests\\run_all.py --only test_note_lookup_by_name",
    ),
    dict(
        id="BUG-3", severity="低", matrix_refs=["F55"],
        title="非法数字型查询参数返回 500（非法学科 slug 也 500）",
        symptom="GET /api/doc?id=abc、/api/chunk?id=abc、/api/search?k=abc、/api/kp?id=abc、"
                "/api/queue?limit=abc 均 HTTP 500（ValueError: invalid literal for int()）。"
                "另：POST /api/subjects/switch 传不存在的 slug 也是 500。",
        root_cause="tools/dashboard.py::do_GET 这几个分支用裸 `int(qs.get(...))`，未用同文件已有的 "
                   "`_int_arg()` 兜底；subjects/switch 未捕获 SubjectError。",
        impact="接口健壮性/错误码不规范（前端正常路径不受影响，异常输入/第三方调用会拿到 500）。",
        fixed_in="ec394bb fix(dashboard): 修复三个真实用户路径缺陷（验收 BUG-1/2/3）",
        fix_summary="统一改用 _int_arg(qs.get('id'), 0)；subjects/switch 捕获 SubjectError 返回 400 JSON。",
        verify=("回归用例：test_03_api_write.py::test_bad_query_params_robust（已全绿）\n"
                "Lead 侧独立验证：_work/lead/verify_bugfix.py 12/12 通过"),
        repro="venv\\Scripts\\python.exe -X utf8 tests\\run_all.py --only test_bad_query_params_robust",
    ),
    dict(
        id="BUG-5", severity="中", matrix_refs=["F99"],
        title="掌握度多处口径不一致（列表恒 0%，详情/总览 11–32%）",
        symptom="同一知识点：/api/kps 返回 mastery=0.0；/api/kp?id= 与 /api/stats 返回 "
                "compute_mastery 结果（0.1144/0.3246）；界面同屏 toast 13% 与列表 0% 矛盾。",
        root_cause="api_kps 直接读库列 mastery（评分流程不写该列），"
                   "而详情/总览用 core/study.compute_mastery(row) 现算。",
        impact="知识点排序、掌握度可视化全部失真，学生不信任数据。",
        fixed_in="M0 wave1（2026-10-18，本批合并提交）",
        fix_summary="api_kps 改用 study.compute_mastery(row) 现算（不写库）；列表排序移到 Python 侧。",
        verify=("回归用例：test_08_dashboard_fixes.py::TestM0Mastery（三处 spread=0 断言；图谱页已下线）\n"
                "_work/m0/dash/report.md §3 M0-5"),
        repro="venv\\Scripts\\python.exe -X utf8 tests\\run_all.py --only test_08_dashboard_fixes",
    ),
    dict(
        id="BUG-7", severity="中", matrix_refs=["F102"],
        title="用户可见页面暴露开发者术语 + 空态死链（学生看不懂/点不到）",
        symptom="三类学生评测 §4 P0-D：知识点页整屏出现 logs/knowledge/<doc_id>-末级知识点总结.json、"
                "tools/terms_build.py --doc <id>、卡 C-0058、doc_id 等；笔记/错题/复习空态指向不存在的按钮；"
                "知识点列表摘要过长。",
        root_cause="页面文案直接把实现说明/产物路径/卡片号写给用户；C-0053 删除入口后空态文案未同步；"
                   "知识点 summary 未截断。",
        impact="新用户读不懂、找不到入口，直接劝退（保研/突击角色均因空态死链放弃）。",
        fixed_in="M0 waveA（2026-10-18, commit 1674ede）",
        fix_summary="8 个视图可见文本开发者术语 0 命中（显示层 humanize 过滤 + 文案人话化）；"
                    "高级说明收进 <details> 折叠；6 个空态补真实主按钮；知识点摘要 2 行截断。",
        verify=("回归用例：test_11_m0_scenario_copy.py（6/6）\n"
                "gate v2 基线/终测：_work/m0/gate/gate_v2-*.json；_work/m0/ui/report.md"),
        repro="venv\\Scripts\\python.exe -X utf8 tests\\run_all.py --only test_11_m0_scenario_copy",
    ),
    dict(
        id="BUG-8", severity="中", matrix_refs=["F104"],
        title="移动端不可用（资料页 390 宽横向溢出 2.03 倍、问答页首屏自动滚动）",
        symptom="三类学生评测 §4 P1-F：390/504 宽下资料页 scrollWidth=788 > 390（表格不响应），"
                "问答页打开即 scrollY=396（autofocus 把首屏滚走），手机上无法正常浏览。",
        root_cause="看板 CSS 仅 620px 断点调整设置栏，资料页表格/长串未做卡片化；"
                   "问答页自动 focus 未用 preventScroll。",
        impact="突击生（手机党）直接放弃；移动端等于不可用。",
        fixed_in="M0 waveB（2026-10-18, commit 见 git log）",
        fix_summary="≤560px 表格改卡片式、长串断行、输入不顶宽；safeFocus/focus({preventScroll:true})；"
                    "CDP 真 390×844 实测 8 视图 sw=cw=390、首屏 scrollY 全 0、溢出元素 0。",
        verify=("回归用例：test_12_m0_import_ui_mobile.py（7/7）\n"
                "gate v2 终测：_work/m0/gate/report-v2.md；_work/m0/waveB/shots/fixed390/"),
        repro="venv\\Scripts\\python.exe -X utf8 tests\\run_all.py --only test_12_m0_import_ui_mobile",
    ),
    dict(
        id="BUG-9", severity="中", matrix_refs=["F105"],
        title="知识点小窗「已绑定 N 条笔记」计数恒为 0（切节点再回来丢失）",
        symptom="知识点页→课件思维导图→点节点打开「理解与要点」小窗：底部先显示「已绑定 1 条笔记」，"
                "点「下一知识点」再切回原知识点后变成「已绑定 0 条笔记」。",
        root_cause="tools/dashboard.py::api_summary_kp() 计算了 n_notes，但返回 dict 漏了 "
                   "`note_count` 键（2026-10-14 灾后重建版丢行）；前端 parseInt(d.note_count||0) 恒为 0，"
                   "保存后的「+1」只是前端本地假象。",
        impact="用户以为笔记没绑定成功（实际已写入 notes/ 并出现在笔记页签），对笔记功能失去信任。",
        fixed_in="2026-10-19 fix(dashboard): summary-kp 返回 note_count",
        fix_summary="返回 dict 补 `\"note_count\": int(n_notes)`；新增单测锁定契约（有 1 条=1、0 条=0）。",
        verify=("回归用例：test_13_summary_note_count.py（2/2）\n"
                "线上只读核对：doc46 KP-N07-1 等 note_count 与 /api/notes/by_kp 一致；切走再切回 1→1"),
        repro="venv\\Scripts\\python.exe -X utf8 tests\\run_all.py --only test_13_summary_note_count",
    ),
]

# 当前【未修复】的已登记缺陷。有新的确认缺陷时：用例加 @unittest.expectedFailure，
# 并在这里登记一条；修好后从本列表移出、用例转正、记录进 FIXED_BUGS。
KNOWN_BUGS: list = []

# 明确「未覆盖」的功能点及原因（报告中如实列出，不假装覆盖）
GAPS = [
    dict(id="G01", feature="AI 问答流式接口", entry="POST /api/chat/stream",
         reason="需要真实大模型 API Key 与联网，离线基线不覆盖；本次仅覆盖 /api/chat/status 状态查询。"),
    dict(id="G02", feature="AI 知识点总结（单份/批量）", entry="POST /api/kp-build、/api/kp-build/batch",
         reason="需要真实大模型 API Key（付费）且历史 outline/提示词产物在恢复后的 logs/ 中缺失，离线环境不可执行。"),
    dict(id="G03", feature="课件答疑流式接口", entry="POST /api/deck-chat/stream",
         reason="依赖大模型 API；离线不覆盖（笔记落盘路径由 /api/note/deck 覆盖）。"),
    dict(id="G04", feature="课件分享包导出/导入真实写", entry="POST /api/share/export、/api/share/import",
         reason="导出会触发渲染/打包长任务；本次只覆盖 /api/share/packages、/api/share/progress 与错误分支（share/estimate）。"),
    dict(id="G05", feature="上传文件（multipart）", entry="POST /api/upload",
         reason="需要构造 multipart 请求；同路径能力由 /api/import 预览覆盖，避免在验收里引入额外复杂度。"),
    dict(id="G06", feature="教材入库（OCR 长任务）", entry="POST /api/ingest/textbook、ingest --mode textbook",
         reason="OCR 452 页量级、单页 16s+，且依赖本地 OCR 引擎；本次用 md fixture 覆盖同一条 ingest 管线的解析/切块/向量/FTS。"),
    dict(id="G07", feature="单份 API 入库 /api/ingest", entry="POST /api/ingest",
         reason="服务端同步长任务会加载嵌入模型并可能调用大模型；CLI ingest 已覆盖同一 pipeline，API 仅覆盖非法路径错误分支。"),
    dict(id="G08", feature="识图/图注（视觉模型）", entry="入库 FIGURE_DESC、/img/slide",
         reason="需要视觉大模型 API 与真实页图；离线基线不覆盖。"),
    dict(id="G09", feature="分享包导入的真实解压/落库", entry="POST /api/share/import",
         reason="需要先有导出产物（见 G04）；本次只验证 inspect/estimate 的输入校验分支。"),
    dict(id="G10", feature="设置窗口密钥回填/校验", entry="POST /api/settings（secret 字段）",
         reason="离线基线不写入真实密钥；只验证非密钥字段保存与重置。"),
]
