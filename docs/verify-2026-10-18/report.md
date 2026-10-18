# P2 验收基线报告 · study_agent 学习看板

- 生成时间：2026-10-18T17:36:29+0800
- 代码版本：`96a4662（产品区干净）`
- 被测代码指纹：core/config.py + core/study.py + core/search.py + core/subjects.py + tools/dashboard.py + tools/study.py 的 sha256 见 report.json::env.product_hashes
- 结论：**67 通过 / 0 失败 / 0 未修复缺陷(expectedFailure) / 0 跳过**，共 67 条用例（功能矩阵 67 行，覆盖 67 行）；历史缺陷已修复 3 个（回归用例已转正）
- 退出码：`0`

## 1. 怎么跑

```
cd D:\study_agent
venv\Scripts\python.exe -X utf8 tests\run_all.py            # 全量
venv\Scripts\python.exe -X utf8 tests\run_all.py --only test_search  # 单条
set VERIFY_REFRESH_FIXTURES=1 & venv\Scripts\python.exe -X utf8 tests\run_all.py  # 按当前真实库重拍 fixture
```

隔离方式：产品代码复制到 `_work/verify/sandbox/`（ROOT=沙箱），数据用 sqlite3 backup API 快照到 `_work/verify/fixtures/` → 每次运行复制到 `_work/verify/data/`，通过 `KB_DB/KB_RAW/KB_NOTES/KB_INDEX/KB_LOG/KB_SETTINGS_FILE` 指向副本；真实数据目录只读。

## 2. 隔离与清理证据

- 代码沙箱与产品代码一致：`是`
- 运行期间产品代码是否被改动：`否`
- 测试实例端口：[8897, 8897, 8897, 8897, 8897, 8897]；运行后监听状态：{8897: False, 8898: False, 8899: False}
- 残留进程：无
- 真实数据指纹（运行前后）：运行前后完全一致（本套件未写真实数据）
- fixture 快照：`_work\verify\fixtures\manifest.json`

## 3. 功能矩阵与结果

| 编号 | 功能 | 入口/接口 | 优先级 | 结果 | 用例 |
|---|---|---|---|---|---|
| F01 | 服务启动与首页渲染 | `GET /` | P0 | ✅ 通过 | test_01_health.TestHealth.test_home_ok |
| F02 | 首页 HTML 别名与页面骨架 | `GET /index.html` | P0 | ✅ 通过 | test_01_health.TestHealth.test_index_html_alias |
| F03 | 页面自包含（无外部静态资源依赖） | `GET /（HTML 内嵌全部 JS/CSS）` | P1 | ✅ 通过 | test_01_health.TestHealth.test_page_self_contained |
| F04 | 未知路由回落 404 JSON | `GET/POST /api/<未知>` | P1 | ✅ 通过 | test_01_health.TestHealth.test_unknown_route_404 |
| F05 | 测试实例指向隔离数据副本 | `GET /api/firstrun、/api/subjects` | P0 | ✅ 通过 | test_01_health.TestHealth.test_isolation_paths |
| F06 | 真实数据零写入（只读校验） | `真实 index/raw/notes/subjects 指纹对比` | P0 | ✅ 通过 | test_01_health.TestHealth.test_real_data_untouched |
| F10 | 学习总览统计 | `GET /api/stats` | P0 | ✅ 通过 | test_02_api_read.TestReadApi.test_stats<br>test_04_cli.TestCli.test_stats |
| F11 | 文档列表 | `GET /api/docs` | P0 | ✅ 通过 | test_02_api_read.TestReadApi.test_docs<br>test_03_api_write.TestWriteApi.test_docs_delete_preview<br>test_04_cli.TestCli.test_docs<br>test_04_cli.TestCli.test_docs_outline |
| F12 | 文档详情（大纲/切块） | `GET /api/doc?id=` | P0 | ✅ 通过 | test_02_api_read.TestReadApi.test_doc_detail |
| F13 | 切块及上下文 | `GET /api/chunk?id=` | P1 | ✅ 通过 | test_02_api_read.TestReadApi.test_chunk |
| F14 | 混合检索（向量+关键词） | `GET /api/search?q=&k=` | P0 | ✅ 通过 | test_02_api_read.TestReadApi.test_search<br>test_04_cli.TestCli.test_search |
| F15 | 知识点列表 | `GET /api/kps` | P1 | ✅ 通过 | test_02_api_read.TestReadApi.test_kps |
| F16 | 知识点详情（出处/历史/前置） | `GET /api/kp?id=` | P1 | ✅ 通过 | test_02_api_read.TestReadApi.test_kp_detail |
| F17 | 复习队列 | `GET /api/queue` | P0 | ✅ 通过 | test_02_api_read.TestReadApi.test_queue |
| F18 | 课件复习月历 | `GET /api/deck-calendar?ym=` | P1 | ✅ 通过 | test_02_api_read.TestReadApi.test_deck_calendar |
| F19 | 错题本列表与统计 | `GET /api/mistakes` | P1 | ✅ 通过 | test_02_api_read.TestReadApi.test_mistakes |
| F20 | 知识点依赖图谱 | `GET /api/graph` | P2 | ✅ 通过 | test_02_api_read.TestReadApi.test_graph |
| F21 | 思维导图数据 | `GET /api/mindmap?doc_id=` | P1 | ✅ 通过 | test_02_api_read.TestReadApi.test_mindmap |
| F22 | 名词解释产物状态 | `GET /api/terms?doc_id=` | P2 | ✅ 通过 | test_02_api_read.TestReadApi.test_terms |
| F23 | 按页知识点定位 | `GET /api/page-kps?doc_id=&page=` | P2 | ✅ 通过 | test_02_api_read.TestReadApi.test_page_kps |
| F24 | 知识点整理候选与成本预估 | `GET /api/kp-build/options、/batch/options` | P1 | ✅ 通过 | test_02_api_read.TestReadApi.test_kp_build_options |
| F25 | 原始资料清单（raw/ 与孤儿） | `GET /api/raw` | P1 | ✅ 通过 | test_02_api_read.TestReadApi.test_raw<br>test_07_legacy_ingest.TestLegacyIngestDecision.test_raw_list_legacy_supported |
| F26 | 首次运行体检（空库/凭据判定） | `GET /api/firstrun` | P1 | ✅ 通过 | test_02_api_read.TestReadApi.test_firstrun |
| F27 | 笔记列表/详情/按来源反查 | `GET /api/notes、/api/note、/api/notes/by_kp` | P1 | ✅ 通过 | test_02_api_read.TestReadApi.test_notes_read |
| F28 | 学科列表与当前真实路径 | `GET /api/subjects` | P0 | ✅ 通过 | test_02_api_read.TestReadApi.test_subjects |
| F29 | 设置读取（四条管线） | `GET /api/settings` | P1 | ✅ 通过 | test_02_api_read.TestReadApi.test_settings_read |
| F30 | 设置窗口提示词读取（内置/文件） | `GET /api/settings/prompt` | P2 | ✅ 通过 | test_02_api_read.TestReadApi.test_settings_prompt |
| F31 | 长任务进度快照 | `GET /api/ingest/progress、/api/kp-build/progress、/api/ingest/batch/progress、/api/kp-build/batch/progress` | P1 | ✅ 通过 | test_02_api_read.TestReadApi.test_progress_snapshots |
| F32 | 分享包列表与进度 | `GET /api/share/packages、/api/share/progress` | P2 | ✅ 通过 | test_02_api_read.TestReadApi.test_share |
| F33 | 问答管线状态（离线可用性） | `GET /api/chat/status` | P1 | ✅ 通过 | test_02_api_read.TestReadApi.test_chat_status |
| F40 | 知识点新建/详情/图谱联动 | `POST /api/kp` | P0 | ✅ 通过 | test_03_api_write.TestWriteApi.test_kp_create |
| F41 | 复习评分与统计联动 | `POST /api/review` | P0 | ✅ 通过 | test_03_api_write.TestWriteApi.test_review<br>test_04_cli.TestCli.test_review |
| F42 | 笔记保存→列表→按文件原位更新 | `POST /api/note/kp、/api/note/update` | P0 | ✅ 通过 | test_03_api_write.TestWriteApi.test_note_write |
| F43 | 课件答疑笔记保存 | `POST /api/note/deck` | P1 | ✅ 通过 | test_03_api_write.TestWriteApi.test_note_deck |
| F44 | 笔记正文路径安全（拒绝穿越） | `POST /api/note/update（非法 file）` | P1 | ✅ 通过 | test_03_api_write.TestWriteApi.test_note_update_guard |
| F45 | 设置保存/重置（写沙箱 settings） | `POST /api/settings、/api/settings/reset` | P1 | ✅ 通过 | test_03_api_write.TestWriteApi.test_settings_write |
| F46 | 按路径导入预览 | `POST /api/import (dry_run)` | P1 | ✅ 通过 | test_03_api_write.TestWriteApi.test_import_preview<br>test_07_legacy_ingest.TestLegacyIngestDecision.test_import_preview_legacy |
| F47 | 批量入库计划预览 | `POST /api/ingest/batch/plan` | P1 | ✅ 通过 | test_03_api_write.TestWriteApi.test_ingest_batch_plan |
| F48 | 孤儿数据清理预览 | `POST /api/cleanup (dry_run)` | P1 | ✅ 通过 | test_03_api_write.TestWriteApi.test_cleanup_preview |
| F49 | 单份删除影响预览 | `POST /api/doc/delete (dry_run)` | P1 | ✅ 通过 | test_03_api_write.TestWriteApi.test_doc_delete_preview |
| F50 | 文档标题改名预览 | `POST /api/doc/rename (dry_run)` | P2 | ✅ 通过 | test_03_api_write.TestWriteApi.test_doc_rename_preview |
| F51 | 学科切换 + 课件月历 + 入计划/标记 | `POST /api/subjects/switch、/api/deck-enroll、/api/deck-mark` | P0 | ✅ 通过 | test_03_api_write.TestWriteApi.test_subject_switch_deck |
| F52 | 新建/重命名学科 | `POST /api/subjects/create、/api/subjects/rename` | P1 | ✅ 通过 | test_03_api_write.TestWriteApi.test_subject_create_rename |
| F53 | 批量删除预览（BUG-1 修复回归） | `POST /api/docs/delete (dry_run)` | P1 | ✅ 通过 | test_03_api_write.TestWriteApi.test_docs_delete_preview |
| F54 | 原始文件重命名预览（BUG-1 修复回归） | `POST /api/rename/raw (dry_run)` | P1 | ✅ 通过 | test_03_api_write.TestWriteApi.test_rename_raw_preview |
| F55 | 非法查询参数健壮性（BUG-3 修复回归） | `GET /api/doc?id=abc 等` | P2 | ✅ 通过 | test_03_api_write.TestWriteApi.test_bad_query_params_robust |
| F56 | 按名读取笔记（BUG-2 修复回归） | `GET /api/note?name=` | P1 | ✅ 通过 | test_03_api_write.TestWriteApi.test_note_lookup_by_name |
| F60 | CLI 环境自检 | `study.py check` | P0 | ✅ 通过 | test_04_cli.TestCli.test_check |
| F61 | CLI 学习统计 | `study.py stats` | P1 | ✅ 通过 | test_02_api_read.TestReadApi.test_stats<br>test_04_cli.TestCli.test_stats |
| F62 | CLI 文档列表/大纲 | `study.py docs [--outline ID]` | P1 | ✅ 通过 | test_02_api_read.TestReadApi.test_docs<br>test_03_api_write.TestWriteApi.test_docs_delete_preview<br>test_04_cli.TestCli.test_docs<br>test_04_cli.TestCli.test_docs_outline |
| F63 | CLI 混合检索 | `study.py search` | P0 | ✅ 通过 | test_02_api_read.TestReadApi.test_search<br>test_04_cli.TestCli.test_search |
| F64 | CLI 切块上下文 | `study.py show` | P1 | ✅ 通过 | test_04_cli.TestCli.test_show |
| F65 | CLI 复习队列/评分 | `study.py review` | P0 | ✅ 通过 | test_03_api_write.TestWriteApi.test_review<br>test_04_cli.TestCli.test_review |
| F66 | CLI 知识点增/列 | `study.py kp add/list` | P1 | ✅ 通过 | test_02_api_read.TestReadApi.test_kp_build_options<br>test_02_api_read.TestReadApi.test_kp_detail<br>test_02_api_read.TestReadApi.test_kps<br>test_03_api_write.TestWriteApi.test_kp_create<br>test_04_cli.TestCli.test_kp |
| F67 | CLI 错题登记/统计 | `study.py mistake add/stats` | P1 | ✅ 通过 | test_02_api_read.TestReadApi.test_mistakes<br>test_04_cli.TestCli.test_mistake |
| F68 | CLI 笔记回写与读回 | `study.py note save/list/read` | P0 | ✅ 通过 | test_02_api_read.TestReadApi.test_notes_read<br>test_03_api_write.TestWriteApi.test_note_deck<br>test_03_api_write.TestWriteApi.test_note_lookup_by_name<br>test_03_api_write.TestWriteApi.test_note_update_guard<br>test_03_api_write.TestWriteApi.test_note_write<br>test_04_cli.TestCli.test_note |
| F69 | CLI 小 md 入库（解析/切块/向量/FTS） | `study.py ingest` | P0 | ✅ 通过 | test_03_api_write.TestWriteApi.test_ingest_batch_plan<br>test_04_cli.TestCli.test_ingest<br>test_04_cli.TestCli.test_ingest_idempotent |
| F70 | CLI 入库幂等（重复跑跳过） | `study.py ingest 同文件两次` | P1 | ✅ 通过 | test_04_cli.TestCli.test_ingest_idempotent |
| F80 | Edge headless 主界面渲染 | `Edge --headless --dump-dom /` | P0 | ✅ 通过 | test_05_browser.TestBrowser.test_edge_dom |
| F81 | Edge headless 截图证据 | `Edge --headless --screenshot /` | P2 | ✅ 通过 | test_05_browser.TestBrowser.test_edge_screenshot |
| F90 | 一键健康检查（解释器/导入/语法/库/study check） | `tools/healthcheck.py --json` | P1 | ✅ 通过 | test_06_healthcheck.TestHealthcheck.test_healthcheck_all_green |
| F91 | MCP 服务自测与 stdio 握手（经 healthcheck） | `tools/serve_mcp.py --selftest / verify_mcp_handshake.py` | P1 | ✅ 通过 | test_06_healthcheck.TestHealthcheck.test_healthcheck_all_green |
| F92 | 旧格式在 raw 列表中可入库（supported/kind/note） | `GET /api/raw（.ppt/.doc/.xls）` | P0 | ✅ 通过 | test_07_legacy_ingest.TestLegacyIngestDecision.test_raw_list_legacy_supported |
| F93 | 导入预览放开旧格式（supported + 自动转换提示） | `POST /api/import (dry_run)（.ppt/.doc/.xls）` | P0 | ✅ 通过 | test_07_legacy_ingest.TestLegacyIngestDecision.test_import_preview_legacy |
| F94 | 批处理计划纳入旧格式（unsupported=0 / 待处理） | `POST /api/ingest/batch/plan（.ppt/.doc/.xls）` | P0 | ✅ 通过 | test_07_legacy_ingest.TestLegacyIngestDecision.test_batch_plan_legacy |
| F95 | 转换产物已在库时源文件显示已入库、计划跳过（防重复转换） | `GET /api/raw + POST /api/ingest/batch/plan（x.ppt + x.pptx）` | P0 | ✅ 通过 | test_07_legacy_ingest.TestLegacyIngestDecision.test_converted_artifact_indexed |
| F96 | 一键数据备份（备份到临时目录 + integrity + --keep 清理） | `tools/backup_data.py --json --out <tmp>` | P1 | ✅ 通过 | test_06_healthcheck.TestHealthcheck.test_backup_data_isolated |

## 4. 失败与缺陷

无失败用例。
**当前无未修复的已知缺陷。**

### 已修复历史缺陷（回归用例已转正，防止退化）

#### BUG-1（高危）批量删除预览 / 原始文件重命名 500（Path.replace 误用）

- 修复：`ec394bb fix(dashboard): 修复三个真实用户路径缺陷（验收 BUG-1/2/3）` — 改为 Path(str(doc_path or '').replace('\\', '/')).stem.strip()（与同文件 7970 行一致）。
- 历史表现：资料页「批量删除」预览、raw 文件「重命名」预览/执行均返回 HTTP 500，错误体为 TypeError: Path.replace() takes 2 positional arguments but 3 were given。
- 历史根因：tools/dashboard.py::_doc_stem() 把结字符串的 `str(...).replace('\\','/')` 写成 `Path(str(doc_path)).replace('\\','/')`，而 pathlib.Path.replace 是「改文件名」(2 参数)；_deck_dirs_for() → _doc_artifacts() 触发，波及 /api/docs/delete 与 /api/rename/raw。
- 验收证据：回归用例：test_03_api_write.py::test_docs_delete_preview、test_rename_raw_preview（已全绿）
Lead 侧独立验证：_work/lead/verify_bugfix.py（8898 隔离实例，12/12 通过）
- 回归复现：`venv\Scripts\python.exe -X utf8 tests\run_all.py --only test_docs_delete_preview
venv\Scripts\python.exe -X utf8 tests\run_all.py --only test_rename_raw_preview`

#### BUG-2（中危）GET /api/note?name= 恒返回第一篇笔记（匹配条件写成恒真）

- 修复：`ec394bb fix(dashboard): 修复三个真实用户路径缺陷（验收 BUG-1/2/3）` — name 与 file/title/stem 精确匹配（含大小写兜底），未命中返回 {"error": "笔记不存在"}。
- 历史表现：笔记页点开任意一篇、或知识点窗口「查看笔记」，返回的永远是列表里的第一篇笔记；前端编辑保存会把第一篇笔记的文件名带回去，存在写错笔记的风险。
- 历史根因：tools/dashboard.py::api_note() 循环里写成 `if (name, n['title'], n['file'].removesuffix('.md')) in [(name, n['title'], n['file'].removesuffix('.md'))]:`——元组在单元素列表里，条件恒为真。
- 验收证据：回归用例：test_03_api_write.py::test_note_lookup_by_name、test_note_write（已全绿）
Lead 侧独立验证：_work/lead/verify_bugfix.py 12/12 通过
- 回归复现：`venv\Scripts\python.exe -X utf8 tests\run_all.py --only test_note_lookup_by_name`

#### BUG-3（低危）非法数字型查询参数返回 500（非法学科 slug 也 500）

- 修复：`ec394bb fix(dashboard): 修复三个真实用户路径缺陷（验收 BUG-1/2/3）` — 统一改用 _int_arg(qs.get('id'), 0)；subjects/switch 捕获 SubjectError 返回 400 JSON。
- 历史表现：GET /api/doc?id=abc、/api/chunk?id=abc、/api/search?k=abc、/api/kp?id=abc、/api/queue?limit=abc 均 HTTP 500（ValueError: invalid literal for int()）。另：POST /api/subjects/switch 传不存在的 slug 也是 500。
- 历史根因：tools/dashboard.py::do_GET 这几个分支用裸 `int(qs.get(...))`，未用同文件已有的 `_int_arg()` 兜底；subjects/switch 未捕获 SubjectError。
- 验收证据：回归用例：test_03_api_write.py::test_bad_query_params_robust（已全绿）
Lead 侧独立验证：_work/lead/verify_bugfix.py 12/12 通过
- 回归复现：`venv\Scripts\python.exe -X utf8 tests\run_all.py --only test_bad_query_params_robust`

## 5. 未覆盖功能与原因（如实列出）

| 编号 | 功能 | 入口 | 未覆盖原因 |
|---|---|---|---|
| G01 | AI 问答流式接口 | `POST /api/chat/stream` | 需要真实大模型 API Key 与联网，离线基线不覆盖；本次仅覆盖 /api/chat/status 状态查询。 |
| G02 | AI 知识点总结（单份/批量） | `POST /api/kp-build、/api/kp-build/batch` | 需要真实大模型 API Key（付费）且历史 outline/提示词产物在恢复后的 logs/ 中缺失，离线环境不可执行。 |
| G03 | 课件答疑流式接口 | `POST /api/deck-chat/stream` | 依赖大模型 API；离线不覆盖（笔记落盘路径由 /api/note/deck 覆盖）。 |
| G04 | 课件分享包导出/导入真实写 | `POST /api/share/export、/api/share/import` | 导出会触发渲染/打包长任务；本次只覆盖 /api/share/packages、/api/share/progress 与错误分支（share/estimate）。 |
| G05 | 上传文件（multipart） | `POST /api/upload` | 需要构造 multipart 请求；同路径能力由 /api/import 预览覆盖，避免在验收里引入额外复杂度。 |
| G06 | 教材入库（OCR 长任务） | `POST /api/ingest/textbook、ingest --mode textbook` | OCR 452 页量级、单页 16s+，且依赖本地 OCR 引擎；本次用 md fixture 覆盖同一条 ingest 管线的解析/切块/向量/FTS。 |
| G07 | 单份 API 入库 /api/ingest | `POST /api/ingest` | 服务端同步长任务会加载嵌入模型并可能调用大模型；CLI ingest 已覆盖同一 pipeline，API 仅覆盖非法路径错误分支。 |
| G08 | 识图/图注（视觉模型） | `入库 FIGURE_DESC、/img/slide` | 需要视觉大模型 API 与真实页图；离线基线不覆盖。 |
| G09 | 分享包导入的真实解压/落库 | `POST /api/share/import` | 需要先有导出产物（见 G04）；本次只验证 inspect/estimate 的输入校验分支。 |
| G10 | 设置窗口密钥回填/校验 | `POST /api/settings（secret 字段）` | 离线基线不写入真实密钥；只验证非密钥字段保存与重置。 |

## 6. 用例明细

| 用例 | 状态 | 耗时(s) |
|---|---|---|
| `test_01_health.TestHealth.test_home_ok` | ✅ 通过 | 0.02 |
| `test_01_health.TestHealth.test_index_html_alias` | ✅ 通过 | 0.02 |
| `test_01_health.TestHealth.test_isolation_paths` | ✅ 通过 | 0.04 |
| `test_01_health.TestHealth.test_page_self_contained` | ✅ 通过 | 0.03 |
| `test_01_health.TestHealth.test_real_data_untouched` | ✅ 通过 | 0.06 |
| `test_01_health.TestHealth.test_unknown_route_404` | ✅ 通过 | 0.04 |
| `test_02_api_read.TestReadApi.test_chat_status` | ✅ 通过 | 0.02 |
| `test_02_api_read.TestReadApi.test_chunk` | ✅ 通过 | 0.01 |
| `test_02_api_read.TestReadApi.test_deck_calendar` | ✅ 通过 | 0.0 |
| `test_02_api_read.TestReadApi.test_doc_detail` | ✅ 通过 | 0.06 |
| `test_02_api_read.TestReadApi.test_docs` | ✅ 通过 | 0.01 |
| `test_02_api_read.TestReadApi.test_firstrun` | ✅ 通过 | 0.02 |
| `test_02_api_read.TestReadApi.test_graph` | ✅ 通过 | 0.03 |
| `test_02_api_read.TestReadApi.test_kp_build_options` | ✅ 通过 | 0.05 |
| `test_02_api_read.TestReadApi.test_kp_detail` | ✅ 通过 | 0.03 |
| `test_02_api_read.TestReadApi.test_kps` | ✅ 通过 | 0.01 |
| `test_02_api_read.TestReadApi.test_mindmap` | ✅ 通过 | 0.05 |
| `test_02_api_read.TestReadApi.test_mistakes` | ✅ 通过 | 0.01 |
| `test_02_api_read.TestReadApi.test_notes_read` | ✅ 通过 | 0.05 |
| `test_02_api_read.TestReadApi.test_page_kps` | ✅ 通过 | 0.01 |
| `test_02_api_read.TestReadApi.test_progress_snapshots` | ✅ 通过 | 0.05 |
| `test_02_api_read.TestReadApi.test_queue` | ✅ 通过 | 0.02 |
| `test_02_api_read.TestReadApi.test_raw` | ✅ 通过 | 0.01 |
| `test_02_api_read.TestReadApi.test_search` | ✅ 通过 | 0.2 |
| `test_02_api_read.TestReadApi.test_settings_prompt` | ✅ 通过 | 0.03 |
| `test_02_api_read.TestReadApi.test_settings_read` | ✅ 通过 | 0.02 |
| `test_02_api_read.TestReadApi.test_share` | ✅ 通过 | 0.02 |
| `test_02_api_read.TestReadApi.test_stats` | ✅ 通过 | 0.03 |
| `test_02_api_read.TestReadApi.test_subjects` | ✅ 通过 | 0.01 |
| `test_02_api_read.TestReadApi.test_terms` | ✅ 通过 | 0.03 |
| `test_03_api_write.TestWriteApi.test_bad_query_params_robust` | ✅ 通过 | 0.33 |
| `test_03_api_write.TestWriteApi.test_cleanup_preview` | ✅ 通过 | 0.04 |
| `test_03_api_write.TestWriteApi.test_doc_delete_preview` | ✅ 通过 | 0.06 |
| `test_03_api_write.TestWriteApi.test_doc_rename_preview` | ✅ 通过 | 0.04 |
| `test_03_api_write.TestWriteApi.test_docs_delete_preview` | ✅ 通过 | 0.08 |
| `test_03_api_write.TestWriteApi.test_import_preview` | ✅ 通过 | 0.01 |
| `test_03_api_write.TestWriteApi.test_ingest_batch_plan` | ✅ 通过 | 0.01 |
| `test_03_api_write.TestWriteApi.test_kp_create` | ✅ 通过 | 0.14 |
| `test_03_api_write.TestWriteApi.test_note_deck` | ✅ 通过 | 0.08 |
| `test_03_api_write.TestWriteApi.test_note_lookup_by_name` | ✅ 通过 | 0.23 |
| `test_03_api_write.TestWriteApi.test_note_update_guard` | ✅ 通过 | 0.16 |
| `test_03_api_write.TestWriteApi.test_note_write` | ✅ 通过 | 0.24 |
| `test_03_api_write.TestWriteApi.test_rename_raw_preview` | ✅ 通过 | 0.05 |
| `test_03_api_write.TestWriteApi.test_review` | ✅ 通过 | 0.21 |
| `test_03_api_write.TestWriteApi.test_settings_write` | ✅ 通过 | 0.12 |
| `test_03_api_write.TestWriteApi.test_subject_create_rename` | ✅ 通过 | 0.42 |
| `test_03_api_write.TestWriteApi.test_subject_switch_deck` | ✅ 通过 | 0.23 |
| `test_04_cli.TestCli.test_check` | ✅ 通过 | 1.98 |
| `test_04_cli.TestCli.test_docs` | ✅ 通过 | 0.89 |
| `test_04_cli.TestCli.test_docs_outline` | ✅ 通过 | 0.89 |
| `test_04_cli.TestCli.test_ingest` | ✅ 通过 | 2.08 |
| `test_04_cli.TestCli.test_ingest_idempotent` | ✅ 通过 | 1.8 |
| `test_04_cli.TestCli.test_kp` | ✅ 通过 | 1.8 |
| `test_04_cli.TestCli.test_mistake` | ✅ 通过 | 1.87 |
| `test_04_cli.TestCli.test_note` | ✅ 通过 | 2.76 |
| `test_04_cli.TestCli.test_review` | ✅ 通过 | 3.63 |
| `test_04_cli.TestCli.test_search` | ✅ 通过 | 1.2 |
| `test_04_cli.TestCli.test_show` | ✅ 通过 | 1.82 |
| `test_04_cli.TestCli.test_stats` | ✅ 通过 | 0.9 |
| `test_05_browser.TestBrowser.test_edge_dom` | ✅ 通过 | 0.0 |
| `test_05_browser.TestBrowser.test_edge_screenshot` | ✅ 通过 | 0.0 |
| `test_06_healthcheck.TestHealthcheck.test_backup_data_isolated` | ✅ 通过 | 2.8 |
| `test_06_healthcheck.TestHealthcheck.test_healthcheck_all_green` | ✅ 通过 | 6.3 |
| `test_07_legacy_ingest.TestLegacyIngestDecision.test_batch_plan_legacy` | ✅ 通过 | 0.01 |
| `test_07_legacy_ingest.TestLegacyIngestDecision.test_converted_artifact_indexed` | ✅ 通过 | 0.05 |
| `test_07_legacy_ingest.TestLegacyIngestDecision.test_import_preview_legacy` | ✅ 通过 | 0.03 |
| `test_07_legacy_ingest.TestLegacyIngestDecision.test_raw_list_legacy_supported` | ✅ 通过 | 0.03 |

## 7. 证据文件

- `_work\verify\artifacts\browser-home.png` — Edge headless 首页截图
- `_work\verify\artifacts\healthcheck-result.json` — tools/healthcheck.py --json 原始输出
- `_work\verify\artifacts\backup-data-result.json` — tools/backup_data.py --json 原始输出
- `_work\verify\report.md` — 本报告
- `_work\verify\report.json` — 机读结果
- `_work\verify\fixtures\manifest.json` — fixture 快照与真实库指纹
- `_work\verify\data\main.db` — 被测主库副本（真实库 backup 快照）
- `_work\verify\logs\browser-8897.log` — 被测实例启动日志
- `_work\verify\logs\health-8897.log` — 被测实例启动日志
- `_work\verify\logs\healthcheck-8897.log` — 被测实例启动日志
- `_work\verify\logs\legacy-8897.log` — 被测实例启动日志
- `_work\verify\logs\read-8897.log` — 被测实例启动日志
- `_work\verify\logs\write-8897.log` — 被测实例启动日志

## 8. 给后续回归的建议

- 每个改动合并前跑一遍 `tests/run_all.py`；失败项看第 4 节与最小复现命令。
- 历史 BUG-1/2/3 已修复（ec394bb），对应用例已从 expectedFailure 转正；将来若再退化，run_all 会以 FAIL / 退出码 1 暴露。
- 新增已确认缺陷时：用例加 `@unittest.expectedFailure` 并登记到 `tests/matrix.py::FIXED_BUGS` 之前的 `KNOWN_BUGS`（当前为空）；缺陷修复后按同样方式转正。
- 真实库在验收期间若被真实看板改动，fixture 不会漂移；需要按最新真实数据重拍时设 `VERIFY_REFRESH_FIXTURES=1`。
