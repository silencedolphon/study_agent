# 预习页（task-24）· 前端施工报告 — 图谱页签删除 → 预习页签（教材章节 + 左树右文查看器）

- 任务：`task-24` · 执行人：dash-fixer · 时间：2026-10-20 23:33–00:00
- 改动：`tools/dashboard.py`（**+1086 / −104 行**：VIEWS/前端页面/CSS/新的预习后端胶水路由；CRLF 保持、无 BOM）+ 新建 `tests/test_18_preview_ui.py`
- 未动：其它页面、`core/`（`core/preview.py` 由后端智能体并行重写，不是本任务改的）、`tests/matrix.py`/`run_all.py`/其它测试
- 未 commit；未碰真实数据/8756/3080；**没有真跑模型**（只调 dry-run 估价；点「预习本章」在确认框处取消）

## 1. 结论

「图谱」页签已删除，同位置换成「预习」页签（导航 8 项：问答/总览/复习/**预习**/资料/知识点/错题/笔记）。预习页实现：教材选择（≤1 份显示标题）→ 左栏 42 章目录（可展开/收起、状态徽标：未预习/已预习·N小节/生成中/失败；每章按钮 预习本章/重新预习/查看预习；已预习展开出小节）→ 右栏查看器（面包屑、章概览/学习顺序/难点/前置/考点、小节 why/core/lecture/pitfalls/self_check（答案 details 默认收起）、出处 chip 可点 openChunk、关键名词）。生成流程：`estimate` dry-run → confirmBox「预计 1 次调用｜成本上界 ≤¥0.1029｜章 14 页」→（确认后）`POST start` → 800ms 轮询 progress → 完成刷新/选中该章。未开启横幅 +「去设置开启」→ 设置中心并定位「AI 预习总结」；无教材/无章节/未生成都有空态。390 单栏 +「← 返回章节」，无横向溢出。

## 2. 改动块（tools/dashboard.py，行号为改后）

| 位置 | 内容 |
|---|---|
| `987` | `VIEWS`：`graph: 图谱/viewGraph` → `preview: 预习/viewPreview`（同位置） |
| `5638-5790 附近` | 删 `viewGraph()` 及其注释、删空状态按钮 `gr-go-kps`；**保留**同段内原有「笔记」页函数（`noteSourceText`/`viewNotes`/`openNote`，见风险 1） |
| `5796-6285` | 新增 `viewPreview()` 页面：`PV` 状态、`pvLoadOptions/pvLoadChapters/pvRender/pvChapHtml/pvBadge`（章节目录）、`pvNormArtifact`（v2 产物归一：`artifact.meta/summary/subsections/terms/sources` → overview+sections；兼容旧版/legacy warning）、`pvEnsureArt/pvViewerHtml`（右栏查看器 + 面包屑 + 章概览 + 小节各块 + self_check details + 出处 chips + 关键名词）、`pvStart`（estimate→confirmBox→start）、`pvWatchProgress`（800ms 轮询 + `renderProgress(p,'#pv-progress')`）、`pvReload/PVTOSET`、`matchMedia` 窄屏切换 |
| `731-834` | 预习页 CSS：两栏（左 340px + 右自适应）、卡片/徽标/横幅/小节列表/查看器块/`pv-qa` 答案折叠/chips；`@media (max-width:900px)` 单栏；`pv-mob-hide-main/side` 类做窄屏单栏切换 |
| `8452-8640` | **新增预习后端胶水**（dashboard.py 内，零模型）：`_preview_mod/_preview_enabled_state/_preview_pages_label/_preview_artifact_candidates/_preview_read_artifact_file/_preview_status_map/_preview_list_chapters` + `api_preview_options/chapters/estimate/start/progress/doc`。**删** `api_graph()`。 |
| `GET 路由` | 删 `/api/graph`；新增 `/api/preview/options|chapters|estimate|doc`（在只读 `_lock` 组内）；`/api/preview/progress` 放在 **`with _lock:` 之前**（与 `/api/kp-build/progress` 同款） |
| `POST 路由` | `/api/preview/start`（在 kp-build 之后、ingest 之前，起后台线程不阻塞 HTTP） |

### 与后端（`core/preview.py` 已并行重写）的实际契约
- `preview.list_chapters(doc_id)` → `{ok,title,chapters:[{no,name,heading_path,n_blocks,page_start,page_end,n_sections,…}]}`；
- **`preview.preview_status(doc_id)`**（注意实际是**按整份教材**返回 `{chapter_no: {...}, _schema, _old_single}`，任务描述写的"按章"已按实际实现适配）；
- `preview.estimate_preview(doc_id, chapter)` → dry-run（0 调用 0 写盘，含 `est_calls/est_cost_cny/n_pages/model/skip_would_happen`）；
- `preview.start_background(doc_id, chapter, force=?)` → `{started, error, hint}`；
- `preview.progress_snapshot()` → kp-build 同构快照；
- `preview.read_preview_artifact(doc_id, chapter)` → `{ok, artifact:{meta,chapter,summary,subsections,terms,sources}, legacy}`（v2 schema；旧单文件只读兼容）。前端 `pvNormArtifact` 兼容 v2 与旧字段名；后端函数缺失时用产物文件兜底（不会 500）。

## 3. 验收证据

### 3.1 宽屏 1440（qa-preview@8925；假产物 `logs/knowledge/50-预习总结-第2章.json` 按 v2 schema 手造，**未真跑模型**）
| 检查项 | 结果（`result.json`） |
|---|---|
| 导航 | `["问答","总览","复习","预习","资料","知识点","错题","笔记"]`；**无「图谱」**；0 JS 异常 |
| 页面 | 标题「📖 预习」；教材「药理学 第6版 刘晓东」；**42 章**；第2章徽标「✓ 已预习 · 3 小节」，其余「未预习」 |
| 默认选中 | 自动打开第2章、默认第一节（`药物的体内过程…`）；面包屑含教材›第2章›小节 + p.19–p.39 |
| 查看器（小节） | why ✓；核心概念 5 条（+关键名词行）；「老师会怎么讲」1 段；易错点 3 条；自测 2 题（答案默认收起，展开后显示答案）；出处 chip 3 个（复用 `openChunk`）；截图 `PV-03-section.png` |
| 章概览 | 「第2章 章概览」+ 概述 + 学习顺序 4 条 + 难点/前置/考点 3 列 + 关键名词卡（`PV-05-overview.png`） |
| 未预习章 | 第3章展开提示「还没预习。点右侧「预习本章」…」+「预习本章」按钮；右栏空态（`PV-06-unpreviewed.png`） |
| 费用预估 | 点「预习本章」→ 弹窗「现在预习这一章？预计 **1 次调用**｜成本上界 **≤¥0.1029**｜章 **14 页**｜模型 deepseek-flash｜40-53」；**点取消后没有任何请求发出**（`progress.active=false`，`PV-07-estimate.png`） |
| 未开启 | 关闭开关后：顶部「🔒 预习功能未开启」+「去设置开启」；点击 → 设置中心打开且**当前管线=preview**；恢复开关后横幅消失（`PV-08-disabled-banner.png`、`PV-09-jump-settings.png`） |

### 3.2 窄屏 390×844（CDP Emulation 真 390，`shots/narrow/`）
| 检查项 | 结果 |
|---|---|
| 章节目录 | `.pv-side` 366px 全宽显示、`.pv-main` 隐藏、返回按钮隐藏；42 章；`docScrollWidth=390=clientWidth`（无横向溢出） |
| 点章/节 | 切到内容视图：`.pv-side` 隐藏、`.pv-main` 366px、`← 返回章节` 显示；0 溢出 |
| 自测答案 | 390 下 details 可展开 |
| 返回 | 「← 返回章节」恢复章节目录 |
| JS 异常 | **0** |

### 3.3 测试
```text
tests\run_all.py --only test_18 → 13 条 / 通过 13 / 失败 0 / 退出码 0
tests\run_all.py（全量）        → 126 条 / 通过 122 / 失败 4 / 退出码 1
```
全量 4 条失败**全部是删图谱的已知连带**（Lead 统一改测试/matrix，我没动其它测试）：
1. `test_02_api_read.TestReadApi.test_graph`（整个用例要删）；
2. `test_03_api_write.TestWriteApi.test_kp_create`（末尾断言 `/api/graph`）；
3. `test_08_dashboard_fixes.TestM0Mastery.test_mastery_consistent_across_surfaces`（把 `/api/graph` 当第四面）；
4. `test_11_m0_scenario_copy.TestCopyCleanup.test_empty_state_cta_buttons`（断言旧图谱空态按钮 `id="gr-go-kps"`）。
- ⚠ 与 Lead 预判的差异：`test_05_browser` 本次**未失败**（其导航断言可能没硬查「图谱」，已如实记录）；除上述 4 条外其余 122 条全绿（含其余页面/设置中心/知识点等）。
- `test_18`（自包含 `tests/env.py` 沙箱，13 条）：VIEWS/无 viewGraph/`/api/graph` 404；预习 API 正常与错参不 500；`options/chapters/estimate/progress/doc/start` 形状；页面关键选择器与文案（章节目录、三种按钮、why/core/lecture/pitfalls/self_check/章概览/chips/estimate 确认框/800ms 轮询/空态/未开启跳设置）；响应式 CSS。
- `py_compile` OK；内嵌 JS `node --check` OK；无 BOM、14365 CRLF。

## 4. 风险 / 说明
1. **踩坑记录（重要）**：图谱函数区（`知识图谱`→`检索` 注释之间）里还夹着「笔记」页的 `noteSourceText/viewNotes/openNote`；第一次替换时一并删了导致 `viewNotes is not defined` 整页白屏。已从 `git show HEAD` 抽出保留段重新拼接，当前三函数都在（`6309/6317/6393`）。将来删这段代码时要按函数边界切，不要按注释区间整段切。
2. 页面主体容器用 `<div class="pv-main">`，**不能用 `<section>`**：全局样式里有 `section{display:none}`（给 tab 容器用的），曾导致右栏空白（已定位并修复）。
3. 后端契约联动：`preview_status` 实际是"按整份教材"返回章号字典（与任务描述的"按章"不同），已按实际函数适配；若后端后续再加改动，联调由 Lead 统一做；后端函数缺失/旧版时前端 API 有产物文件兜底，页面不 500。
4. 生成：只调用 dry-run 估价；真实 `POST /api/preview/start` 的启动路径已接通（后台线程 + 800ms 轮询 + 完成 toast/刷新/选中），但**本任务未实际跑模型**，联调时需 Lead 用沙箱开启开关实测一次。
5. 沙箱内为验证关闭横幅曾把 `PREVIEW_ENABLED` 写 0 再写回 1（仅 qa-preview 沙箱配置，真实 `config.local.json` 未动）；假产物只写进沙箱 `logs/knowledge/`，真实 `logs/` 未写。
6. 390 窄屏用 JS（matchMedia）切单栏类；headless 截图工具在页面加载后才改视口的场景也已通过 matchMedia change 监听重渲染处理。
7. `test_11` 等旧测试的空态断言仍指向图谱按钮，属 Lead 统一修改范围；我没有为了"绿"恢复任何图谱代码。

## 5. 回滚
`git checkout -- tools/dashboard.py` + 删 `tests/test_18_preview_ui.py` 即恢复（会同时恢复图谱页签/API）；无数据、配置、其它文件改动。
