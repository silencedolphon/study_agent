# tools/ 全量分类清单（审计快照 2026-10-18 16:52 / HEAD d10941f）

> 生成：`_work/audit/build_reports.py`（数据源：`ast-audit.json`、`deep-audit.json`、`release_pack.py` 的白名单与排除规则、`badfiles-serve.json`）。
> 判据：**分类优先取 release_pack.py 的 TOOLS_RELEASE 白名单 / TOOLS_EXCLUDE_RULES 规则**（这是项目自己的发布口径），其次看语法状态与命名。

## 总览

- tools/ 下 `.py` 文件：**162** 个（另有 .ps1/.cmd/.mjs/.bak 非 py 资产，见文末）。
- 分类计数：R=11；D=83；S=0；T=55；X=10；O=3
- 语法不过：**7** 个；断头/空壳：3 个。
- 运行时必需（R1）的最小集合：`dashboard.py`、`study.py`、`serve_mcp.py`、`convert_legacy.ps1`、`render_pptx_slides.ps1`；安装必需（R2）：`download_model.py`。

## R 运行/发布/安装必需（release_pack 白名单 + 入口引用）（11）

| # | 文件 | 大小 | 状态 | 判定/理由 | 被引用证据 |
|---:|---|---:|---|---|---|
| 1 | `dashboard.py` | 616,581 | OK | R1 学习看板 Web UI 主程序（双击启动器的目标） | tools/dashboard.py:24, install.cmd:8, 看板.cmd:7, dashboard.cmd:1, README.md:4, 重建助手.cmd:4 |
| 2 | `download_model.py` | 3,750 | OK | R2 首次运行下载本地嵌入模型（约 100MB），没有它检索跑不起来 | install.cmd:2, README.md:3 |
| 3 | `healthcheck.py` | 9,198 | OK | 安装/运维入口（新增或安装必需） |  |
| 4 | `inventory.py` | 1,526 | OK | R3 库清单（文档/切块/识图统计），排查用 |  |
| 5 | `kp_build.py` | 10,083 | OK | R3 AI 知识点整理管线命令行（看板提示语里点名的那条命令） | tools/dashboard.py:10, tools/study.py:1 |
| 6 | `list_docs.py` | 1,078 | OK | R3 列出全部资料并按内容哈希找重复入库 |  |
| 7 | `reimport_all.py` | 9,928 | OK | R3 批量入库（README 点名推荐：按资料提交、中断可续） | tools/dashboard.py:2, README.md:2 |
| 8 | `reingest_doc.py` | 1,468 | OK | R3 单份资料重建（先存识图块再重入库，用户改过源文件时用） |  |
| 9 | `serve_mcp.py` | 23,631 | OK | R1 MCP server（stdio），把知识库暴露给 DSH 等助手 | tools/serve_mcp.py:4 |
| 10 | `study.py` | 36,648 | OK | R1 命令行主入口（check/ingest/search/docs/stats/kp/review/mistake/note） | tools/dashboard.py:36, tools/study.py:33, tools/serve_mcp.py:10, install.cmd:3, 看板.cmd:1, dashboard.cmd:1, README.md:15, 使用说明.md:1 |
| 11 | `terms_build.py` | 6,131 | OK | R3 名词解释独立命令行 | tools/dashboard.py:4 |

## X 缺陷文件（语法不过 / 空壳 / 断头）（10）

| # | 文件 | 大小 | 状态 | 判定/理由 | 被引用证据 |
|---:|---|---:|---|---|---|
| 1 | `c0089_tmp_launch.py` | 72 | **FAIL** | 已损坏（语法不过） |  |
| 2 | `c0098_audit_ui.py` | 12,303 | **FAIL** | 已损坏（语法不过） |  |
| 3 | `check_mindmap_kp.py` | 12,803 | **FAIL** | 已损坏（语法不过） | tools/dashboard.py:2 |
| 4 | `find_empty_figures.py` | 0 | **0B** | 空壳（0 字节，内容全失） |  |
| 5 | `run_figure_desc.py` | 2,228 | OK | 断头（缺模块头/imports/尾部调用，运行时 NameError） | tools/dashboard.py:1 |
| 6 | `smoke_test.py` | 12,689 | **FAIL** | 已损坏（语法不过） |  |
| 7 | `verify_c0039_precheck.py` | 2,448 | **FAIL** | 已损坏（语法不过） |  |
| 8 | `verify_c0048_window.py` | 0 | **0B** | 空壳（0 字节，内容全失） |  |
| 9 | `verify_c0057_browser.py` | 15,642 | **FAIL** | 已损坏（语法不过） |  |
| 10 | `verify_c0057_offline.py` | 1,700 | **FAIL** | 已损坏（语法不过） |  |

## D 开发/验收/探针（release_pack 规则排除，不进发布包）（83）

| # | 文件 | 大小 | 状态 | 判定/理由 | 被引用证据 |
|---:|---|---:|---|---|---|
| 1 | `_c0052_base_compare.py` | 1,647 | OK | 卡内一次性施工脚本（某张卡的过程产物） |  |
| 2 | `_c0052_check_result.py` | 1,202 | OK | 卡内一次性施工脚本（某张卡的过程产物） |  |
| 3 | `_c0052_final_check.py` | 1,202 | OK | 卡内一次性施工脚本（某张卡的过程产物） |  |
| 4 | `_c0052_probe_stream.py` | 1,820 | OK | 卡内一次性施工脚本（某张卡的过程产物） |  |
| 5 | `_c0052_selftest_notes.py` | 2,532 | OK | 卡内一次性施工脚本（某张卡的过程产物） |  |
| 6 | `_c0057_probe.py` | 1,540 | OK | 卡内一次性施工脚本（某张卡的过程产物） |  |
| 7 | `_c0057_probe_call.py` | 1,809 | OK | 卡内一次性施工脚本（某张卡的过程产物） |  |
| 8 | `_c0057_snap.py` | 3,083 | OK | 卡内一次性施工脚本（某张卡的过程产物） |  |
| 9 | `_c0058_diff.py` | 2,285 | OK | 卡内一次性施工脚本（某张卡的过程产物） |  |
| 10 | `_c0058_probe.py` | 6,234 | OK | 卡内一次性施工脚本（某张卡的过程产物） |  |
| 11 | `_c0058_report.py` | 4,500 | OK | 卡内一次性施工脚本（某张卡的过程产物） |  |
| 12 | `_c0058_snap.py` | 4,110 | OK | 卡内一次性施工脚本（某张卡的过程产物） |  |
| 13 | `_c0058_terms_check.py` | 2,687 | OK | 卡内一次性施工脚本（某张卡的过程产物） |  |
| 14 | `_c0082_probe_note.py` | 3,938 | OK | 卡内一次性施工脚本（某张卡的过程产物） |  |
| 15 | `author_mark.py` | 7,581 | OK | 著作权人侧工具（源码交付前打版权标记），卡里明确不进包 |  |
| 16 | `check_c0056_evidence.py` | 7,865 | OK | 静态/接口自检脚本：开发验收用 |  |
| 17 | `check_c0084_artifacts.py` | 5,660 | OK | 静态/接口自检脚本：开发验收用 |  |
| 18 | `check_dashboard_js.py` | 1,659 | OK | 静态/接口自检脚本：开发验收用 |  |
| 19 | `check_steward_review_watch.py` | 724 | OK | 静态/接口自检脚本：开发验收用 |  |
| 20 | `compare_reasoning.py` | 3,234 | OK | 对比实验脚本：模型/版本对照 |  |
| 21 | `diag_figure_reuse.py` | 2,272 | OK | 诊断脚本：开发期调参与口径确认 |  |
| 22 | `measure_reasoning_gain.py` | 4,404 | OK | 量化实验脚本：度量增量与收益 |  |
| 23 | `pack_exe.py` | 26,432 | OK | 发布流水线脚本（开发侧工具，不进包） |  |
| 24 | `peek_db.py` | 2,258 | OK | 调试：直接看库 |  |
| 25 | `probe_c0050_api.py` | 3,753 | OK | 探针脚本：一次性根因、性能、接口探测 |  |
| 26 | `probe_c0074_firstrun.py` | 1,365 | OK | 探针脚本：一次性根因、性能、接口探测 |  |
| 27 | `probe_c0074_three_steps.py` | 683 | OK | 探针脚本：一次性根因、性能、接口探测 |  |
| 28 | `probe_c0093_http.py` | 10,388 | OK | 探针脚本：一次性根因、性能、接口探测 |  |
| 29 | `release_pack.py` | 45,945 | OK | 发布流水线脚本（开发侧工具，不进包） |  |
| 30 | `steward_card_set_status.py` | 4,157 | OK | 大管家（内部管理）工具，与产品运行无关 |  |
| 31 | `steward_poll.py` | 11,046 | OK | 大管家（内部管理）工具，与产品运行无关 |  |
| 32 | `steward_poll_daemon.py` | 9,786 | OK | 大管家（内部管理）工具，与产品运行无关 |  |
| 33 | `steward_review_watch.py` | 13,606 | OK | 大管家（内部管理）工具，与产品运行无关 |  |
| 34 | `steward_wake.py` | 4,177 | OK | 大管家（内部管理）工具，与产品运行无关 |  |
| 35 | `steward_wake_watch.py` | 4,540 | OK | 大管家（内部管理）工具，与产品运行无关 |  |
| 36 | `test_textbook_chapters.py` | 23,676 | OK | 测试脚本（含 DOM/浏览器测试），开发验收用 |  |
| 37 | `verify_c0046_lkp_display.py` | 25,451 | OK | 验收脚本：卡验收 / 大管家独立复核用，非运行必需 |  |
| 38 | `verify_c0050_browser.py` | 20,309 | OK | 验收脚本：卡验收 / 大管家独立复核用，非运行必需 |  |
| 39 | `verify_c0052_browser.py` | 27,824 | OK | 验收脚本：卡验收 / 大管家独立复核用，非运行必需 |  |
| 40 | `verify_c0052_quick.py` | 11,021 | OK | 验收脚本：卡验收 / 大管家独立复核用，非运行必需 | tools/dashboard.py:2 |
| 41 | `verify_c0053_browser.py` | 19,686 | OK | 验收脚本：卡验收 / 大管家独立复核用，非运行必需 |  |
| 42 | `verify_c0053_quick.py` | 27,749 | OK | 验收脚本：卡验收 / 大管家独立复核用，非运行必需 |  |
| 43 | `verify_c0055_browser.py` | 33,556 | OK | 验收脚本：卡验收 / 大管家独立复核用，非运行必需 |  |
| 44 | `verify_c0055_quick.py` | 7,999 | OK | 验收脚本：卡验收 / 大管家独立复核用，非运行必需 |  |
| 45 | `verify_c0056_browser.py` | 22,193 | OK | 验收脚本：卡验收 / 大管家独立复核用，非运行必需 |  |
| 46 | `verify_c0056_quick.py` | 15,974 | OK | 验收脚本：卡验收 / 大管家独立复核用，非运行必需 |  |
| 47 | `verify_c0059_browser.py` | 14,370 | OK | 验收脚本：卡验收 / 大管家独立复核用，非运行必需 |  |
| 48 | `verify_c0059_offline.py` | 26,234 | OK | 验收脚本：卡验收 / 大管家独立复核用，非运行必需 |  |
| 49 | `verify_c0061_browser.py` | 28,496 | OK | 验收脚本：卡验收 / 大管家独立复核用，非运行必需 |  |
| 50 | `verify_c0061_offline.py` | 22,913 | OK | 验收脚本：卡验收 / 大管家独立复核用，非运行必需 |  |
| 51 | `verify_c0065_legacy.py` | 20,981 | OK | 验收脚本：卡验收 / 大管家独立复核用，非运行必需 |  |
| 52 | `verify_c0071_firstrun.py` | 12,166 | OK | 验收脚本：卡验收 / 大管家独立复核用，非运行必需 |  |
| 53 | `verify_c0071_leak_scan.py` | 4,340 | OK | 验收脚本：卡验收 / 大管家独立复核用，非运行必需 |  |
| 54 | `verify_c0073_exe.py` | 33,973 | OK | 验收脚本：卡验收 / 大管家独立复核用，非运行必需 |  |
| 55 | `verify_c0074_docs.py` | 7,801 | OK | 验收脚本：卡验收 / 大管家独立复核用，非运行必需 |  |
| 56 | `verify_c0077_empty_kp.py` | 49,459 | OK | 验收脚本：卡验收 / 大管家独立复核用，非运行必需 |  |
| 57 | `verify_c0078_yen_encoding.py` | 8,943 | OK | 验收脚本：卡验收 / 大管家独立复核用，非运行必需 |  |
| 58 | `verify_c0079_share.py` | 38,184 | OK | 验收脚本：卡验收 / 大管家独立复核用，非运行必需 |  |
| 59 | `verify_c0080_delivery.py` | 16,404 | OK | 验收脚本：卡验收 / 大管家独立复核用，非运行必需 |  |
| 60 | `verify_c0080_share_ui.py` | 11,836 | OK | 验收脚本：卡验收 / 大管家独立复核用，非运行必需 |  |
| 61 | `verify_c0081_delivery.py` | 19,291 | OK | 验收脚本：卡验收 / 大管家独立复核用，非运行必需 |  |
| 62 | `verify_c0081_host.py` | 13,275 | OK | 验收脚本：卡验收 / 大管家独立复核用，非运行必需 |  |
| 63 | `verify_c0081_share_e2e.py` | 42,306 | OK | 验收脚本：卡验收 / 大管家独立复核用，非运行必需 |  |
| 64 | `verify_c0082_browser.py` | 14,249 | OK | 验收脚本：卡验收 / 大管家独立复核用，非运行必需 |  |
| 65 | `verify_c0082_offline.py` | 22,436 | OK | 验收脚本：卡验收 / 大管家独立复核用，非运行必需 |  |
| 66 | `verify_c0083_dragdrop.py` | 19,777 | OK | 验收脚本：卡验收 / 大管家独立复核用，非运行必需 |  |
| 67 | `verify_c0084_fixture.py` | 15,438 | OK | 验收脚本：卡验收 / 大管家独立复核用，非运行必需 |  |
| 68 | `verify_c0084_purge.py` | 51,803 | OK | 验收脚本：卡验收 / 大管家独立复核用，非运行必需 |  |
| 69 | `verify_c0085_fixture.py` | 12,563 | OK | 验收脚本：卡验收 / 大管家独立复核用，非运行必需 |  |
| 70 | `verify_c0085_rename.py` | 38,992 | OK | 验收脚本：卡验收 / 大管家独立复核用，非运行必需 |  |
| 71 | `verify_c0086_release.py` | 51,260 | OK | 验收脚本：卡验收 / 大管家独立复核用，非运行必需 |  |
| 72 | `verify_c0087_textbook.py` | 11,673 | OK | 验收脚本：卡验收 / 大管家独立复核用，非运行必需 |  |
| 73 | `verify_c0088_regression.py` | 12,525 | OK | 验收脚本：卡验收 / 大管家独立复核用，非运行必需 |  |
| 74 | `verify_c0088_textbook.py` | 17,008 | OK | 验收脚本：卡验收 / 大管家独立复核用，非运行必需 |  |
| 75 | `verify_c0089_textbook.py` | 32,079 | OK | 验收脚本：卡验收 / 大管家独立复核用，非运行必需 |  |
| 76 | `verify_c0090_textbook.py` | 11,382 | OK | 验收脚本：卡验收 / 大管家独立复核用，非运行必需 |  |
| 77 | `verify_c0092_sections.py` | 27,484 | OK | 验收脚本：卡验收 / 大管家独立复核用，非运行必需 |  |
| 78 | `verify_c0093.py` | 19,496 | OK | 验收脚本：卡验收 / 大管家独立复核用，非运行必需 |  |
| 79 | `verify_c0094_preview.py` | 16,347 | OK | 验收脚本：卡验收 / 大管家独立复核用，非运行必需 |  |
| 80 | `verify_c0095_share_preview.py` | 23,229 | OK | 验收脚本：卡验收 / 大管家独立复核用，非运行必需 |  |
| 81 | `verify_c0099.py` | 13,114 | OK | 验收脚本：卡验收 / 大管家独立复核用，非运行必需 |  |
| 82 | `verify_c0100.py` | 9,316 | OK | 验收脚本：卡验收 / 大管家独立复核用，非运行必需 |  |
| 83 | `verify_mcp_handshake.py` | 6,979 | OK | 验收脚本：卡验收 / 大管家独立复核用，非运行必需 |  |

## T 施工/临时脚本（卡号命名，验完即删类）（55）

| # | 文件 | 大小 | 状态 | 判定/理由 | 被引用证据 |
|---:|---|---:|---|---|---|
| 1 | `c0083_tmp_launch.py` | 2,570 | OK | 施工/临时（卡号命名） |  |
| 2 | `c0084_tmp_launch.py` | 6,143 | OK | 施工/临时（卡号命名） |  |
| 3 | `c0085_tmp_launch.py` | 4,961 | OK | 施工/临时（卡号命名） |  |
| 4 | `c0087_report.py` | 6,785 | OK | 施工/临时（卡号命名） |  |
| 5 | `c0088_selftest_compare.py` | 3,332 | OK | 施工/临时（卡号命名） |  |
| 6 | `c0088_snapshot.py` | 23,600 | OK | 施工/临时（卡号命名） |  |
| 7 | `c0089_make_textbook.py` | 8,411 | OK | 施工/临时（卡号命名） |  |
| 8 | `c0089_probe_dbcheck.py` | 3,867 | OK | 施工/临时（卡号命名） |  |
| 9 | `c0090_backup.py` | 6,500 | OK | 施工/临时（卡号命名） |  |
| 10 | `c0090_batch_equiv.py` | 3,985 | OK | 施工/临时（卡号命名） |  |
| 11 | `c0090_before_cross.py` | 4,653 | OK | 施工/临时（卡号命名） |  |
| 12 | `c0090_compare.py` | 15,365 | OK | 施工/临时（卡号命名） |  |
| 13 | `c0090_diagnose_chapters.py` | 5,943 | OK | 施工/临时（卡号命名） |  |
| 14 | `c0090_guard_test.py` | 8,406 | OK | 施工/临时（卡号命名） |  |
| 15 | `c0090_probe.py` | 3,651 | OK | 施工/临时（卡号命名） |  |
| 16 | `c0090_report.py` | 25,775 | OK | 施工/临时（卡号命名） |  |
| 17 | `c0090_rerun.py` | 4,393 | OK | 施工/临时（卡号命名） |  |
| 18 | `c0090_rerun2.py` | 11,491 | OK | 施工/临时（卡号命名） |  |
| 19 | `c0090_search_probe.py` | 5,580 | OK | 施工/临时（卡号命名） |  |
| 20 | `c0090_snapshot.py` | 3,696 | OK | 施工/临时（卡号命名） |  |
| 21 | `c0092_sections.py` | 6,214 | OK | 施工/临时（卡号命名） |  |
| 22 | `c0094_estimate_doc.py` | 5,609 | OK | 施工/临时（卡号命名） |  |
| 23 | `c0094_sha_compare.py` | 2,449 | OK | 施工/临时（卡号命名） |  |
| 24 | `c0097_apply_patch.py` | 27,664 | OK | 施工/临时（卡号命名） |  |
| 25 | `c0097_verify.py` | 10,122 | OK | 施工/临时（卡号命名） |  |
| 26 | `c0098_apply_patch.py` | 20,574 | OK | 施工/临时（卡号命名） |  |
| 27 | `c0098_apply_patch2.py` | 3,132 | OK | 施工/临时（卡号命名） |  |
| 28 | `c0098_docs_probe.py` | 17,022 | OK | 施工/临时（卡号命名） |  |
| 29 | `c0098_fix_escapes.py` | 5,142 | OK | 施工/临时（卡号命名） |  |
| 30 | `c0098_js_syntax_check.py` | 2,405 | OK | 施工/临时（卡号命名） |  |
| 31 | `c0098_scan_escapes.py` | 4,125 | OK | 施工/临时（卡号命名） |  |
| 32 | `c0098_shots.py` | 5,085 | OK | 施工/临时（卡号命名） |  |
| 33 | `c0098_verify_static.py` | 7,080 | OK | 施工/临时（卡号命名） |  |
| 34 | `c0100_apply_patch.py` | 29,452 | OK | 施工/临时（卡号命名） |  |
| 35 | `c0100_apply_patch2.py` | 2,349 | OK | 施工/临时（卡号命名） |  |
| 36 | `c0100_apply_patch3.py` | 5,868 | OK | 施工/临时（卡号命名） |  |
| 37 | `c0100_probe_ui.py` | 20,822 | OK | 施工/临时（卡号命名） |  |
| 38 | `c0100_tmp_launch.py` | 3,955 | OK | 施工/临时（卡号命名） |  |
| 39 | `c0101_apply_patch.py` | 29,528 | OK | 施工/临时（卡号命名） |  |
| 40 | `c0101_apply_patch2.py` | 5,193 | OK | 施工/临时（卡号命名） |  |
| 41 | `c0101_diff_scope.py` | 3,041 | OK | 施工/临时（卡号命名） |  |
| 42 | `c0101_probe_all.py` | 3,845 | OK | 施工/临时（卡号命名） |  |
| 43 | `c0101_probe_cost.py` | 2,941 | OK | 施工/临时（卡号命名） |  |
| 44 | `c0101_probe_db.py` | 1,403 | OK | 施工/临时（卡号命名） |  |
| 45 | `c0101_probe_ui.py` | 11,380 | OK | 施工/临时（卡号命名） |  |
| 46 | `c0101_tmp_launch.py` | 3,902 | OK | 施工/临时（卡号命名） |  |
| 47 | `c0101_verify.py` | 9,984 | OK | 施工/临时（卡号命名） |  |
| 48 | `c0102_apply_patch.py` | 21,174 | OK | 施工/临时（卡号命名） |  |
| 49 | `c0102_diag_click.py` | 5,411 | OK | 施工/临时（卡号命名） |  |
| 50 | `c0102_diff_scope.py` | 4,108 | OK | 施工/临时（卡号命名） |  |
| 51 | `c0102_probe_db.py` | 2,466 | OK | 施工/临时（卡号命名） |  |
| 52 | `c0102_probe_http.py` | 6,399 | OK | 施工/临时（卡号命名） |  |
| 53 | `c0102_probe_ui.py` | 23,653 | OK | 施工/临时（卡号命名） |  |
| 54 | `c0102_tmp_launch.py` | 7,971 | OK | 施工/临时（卡号命名） |  |
| 55 | `c0102_verify.py` | 13,235 | OK | 施工/临时（卡号命名） |  |

## O 其它辅助工具（3）

| # | 文件 | 大小 | 状态 | 判定/理由 | 被引用证据 |
|---:|---|---:|---|---|---|
| 1 | `audit_c0099.py` | 25,974 | OK | 其它开发/辅助工具 |  |
| 2 | `preview_build.py` | 7,924 | OK | 其它开发/辅助工具 |  |
| 3 | `share.py` | 13,208 | OK | 其它开发/辅助工具 | tools/dashboard.py:29 |

## 非 .py 资产（tools/ 下）

- `.ps1`（R1 运行时被 core 直接调用）：`convert_legacy.ps1`(4,252B)、`render_pptx_slides.ps1`(1,948B)、`steward_poll_daemon.ps1`(4,650B)
- `.mjs` 真浏览器探针（开发验收用）：18 个，共 326,823B。
- `dashboard.py.bak*` 开发期备份：11 个（567,213~666,775B）——可作为 dashboard 演进的旁证（当前版最新，不要回退）。

## 结论（tools/）

1. **运行时链路完整**：入口 3 个 py + 2 个 ps1 + 安装脚本引用的 `download_model.py` 全部存在且编译通过；`serve_mcp.py` 已重建可编译。
2. **缺陷集中在开发/验收类脚本**：7 个语法不过的文件全部是 verify/check/smoke/施工临时类，且**所有恢复源均为同一坏版本**（无救回可能）；它们不在发布白名单里（`verify_`/`check_`/`smoke_test` 被 TOOLS_EXCLUDE_RULES 排除），对产品运行零影响。
3. **发布白名单有缺口**：TOOLS_RELEASE 的 18 项里 4 项文件不存在（`split_multippt.py`/`make_test_files.py`/`ingest_figures.py`/`restore_figures.py`），且 `run_figure_desc.py` 是断头版 ⇒ 打包前必须修复，否则交付包含坏工具/缺项。
4. 2 个 0 字节文件（`find_empty_figures.py`/`verify_c0048_window.py`）内容不可恢复，可直接删除。