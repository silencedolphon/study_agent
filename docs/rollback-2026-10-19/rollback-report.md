# M0 外科手术式回退 · 施工报告（task-16）

- 任务：`task-16` · 执行人：dash-fixer · 时间：2026-10-19 18:23–18:52
- 基准：回退前 tag `m0-complete`（HEAD=4046c2e）；M0 前产品代码基准 `251886f`
- 改动范围：`tools/dashboard.py`（删除 442 行 / 净 +30，CRLF 保持、无 BOM）、`core/search.py`（`git checkout 251886f --`）、4 个测试文件（删 test_10）、证据 `_work/rollback/`
- 未动：`tests/matrix.py`、`core/` 其它文件、真实数据/`notes/`/`raw`/`logs`、8756/3080、无 AI 调用、未 git commit

## 1. 结论

M0 的**功能型新增全部撤销**（场景预设 / 新建知识点入口 / 错题闭环 / 导入看板入口 / 检索规则），**排版-文案-移动端-掌握度修复全部保留**。浏览器与 API 实测：默认首屏回到 M0 前的「问答」页、8 个页签恢复；知识点页无「＋新建知识点」/无「导入已整理」；错题页无「＋记一道错题」；`POST /api/mistake`、`GET /api/kp-import/preview`、`POST /api/kp-import/apply` 均 **404**；评分 2 不再自动记错题；设置无场景项；8 视图术语 0 命中；摘要仍 2 行截断；390 宽 8 视图无横向滚动、首屏 scrollY=0；掌握度四处一致（0.2533，spread=0）。4 个测试文件 `--only` 全绿：test_08 **2/2**、test_09 **7/7**、test_11 **5/5**、test_12 **2/2**（退出码均 0）。

## 2. 删除的块（回退前 HEAD 行号引用；现均已不存在）

| 模块 | 删除内容 |
|---|---|
| M0-1 场景 | `header` 里 `#scene-badge` 按钮；CSS `#scene-badge/.scene-opt`；整段 `SCENE_KEY/SCENE_ORDER/SCENES/loadScene/sceneCfg/scenePaint/setScene/cycleScene/navViews`（HEAD 760-852）；`sceneSettingsHtml()`（HEAD 6451-6463）及 `setRender` 里的插入与 `.scene-opt` 绑定（HEAD 6436/6445-6448）；启动块 `SCENE=loadScene(); scenePaint(); go(SCENES[...])` 恢复为 `go('assistant');`（HEAD 6997-7002）；`renderNav` 恢复为 `Object.entries(VIEWS)` 全量渲染（HEAD 854-859 → 基线 747-752） |
| M0-2 新建知识点 | `kpCreateCard()`、`bindKpCreate()`（HEAD 3242-3271）；`viewKps()` 空态/列表两处 `${kpCreateCard()}` 与 `bindKpCreate()` 调用；`kpForm()`/`bindKpForm()` 恢复 251886f 原版（未引用状态） |
| M0-4 错题闭环 | `POST /api/mistake` 路由；`api_add_mistake()` + `_opt_int()`（HEAD 11424-11496）；`api_review()` 里「0–2 自动 add_mistake + auto_mistake_id」整段（恢复基线评分行为；mastery 由 `now_row` 现算的正确性由基线原有代码保留）；知识点详情「记一道错题」按钮/绑定；`MISTAKE_REASONS` + `openMistakeForm()`（HEAD 5321-5390）；评分标题提示与 toast 的 auto_mistake 后缀；错题页「＋记一道错题」按钮及绑定；错题/复习空态与评分说明改为不承诺自动记录的人话（无死链） |
| M0-6b 导入入口 | `GET /api/kp-import/preview`、`POST /api/kp-import/apply` 路由；`api_kp_import_preview/apply()` helper（HEAD 7214-7238）；`from core import kp_import`；前端 `#kp-import-open` 按钮、`kpImportOpen()/kpImportApply()`、`bindKpBuild` 绑定、确认弹窗逻辑。**保留** `core/kp_import.py` 与 `tools/import_kp_artifacts.py` CLI（不删、不展示） |
| M0-7 检索 | `git checkout 251886f -- core/search.py`（与 251886f diff=0） |
| 测试 | 删除 `tests/test_10_search_regression.py`；test_08 删 `TestM0CreateKp`/`TestM0Mistake` 留 `TestM0Mastery`；test_11 删 `TestScenePreset`、更新 CTA/负向断言；test_12 删导入 API/UI 用例留移动端；**未动 tests/matrix.py** |

## 3. 保留的排版/修复（未碰）
- **M0-5 掌握度**：`api_kps`/`api_graph` 用 `study.compute_mastery()` 现算 + Python 侧排序（`api_kps` 排序逻辑、`api_graph` 现算均保留）。
- **M0-6a 文案/排版**：8 视图去工程化术语、人话文案、`<details class="adv">` 折叠高级说明、空态真实 CTA（总览/复习/图谱/笔记/名词/导图）、`.kpsum` 2 行摘要截断、知识点三张卡文案、`humanize()` 设置说明过滤。
- **M0-8 移动端**：`@media (max-width:560px)` 表格卡片化/长串断行、`safeFocus(preventScroll)`。

## 4. 验收证据（`_work/rollback/`）

### 4.1 接口（`api-evidence.json`，qa-rb@8918 全新隔离实例）
| 断言 | 结果 |
|---|---|
| `POST /api/mistake` | **404** `{"error":"未知路径"}` |
| `GET /api/kp-import/preview` | **404** |
| `POST /api/kp-import/apply` | **404** |
| 评分 2 后响应含 `auto_mistake_id` | **否**；`interval_days=1.0`（基线 SM-2 行为），错题数不变（实例快照里原有 2 条用户错题） |
| 掌握度四处（kps/kp?id/graph/stats） | 全 **0.2533**，spread = **0.0** |
| 知识点列表排序 | 仍按掌握度升序（api_kps 提交的 Python 排序保留） |

### 4.2 浏览器（1440×805 场景截图 + 8 视图文本）
| 证据 | 内容 |
|---|---|
| `shots/M0RB-01-first-screen.png` | 默认首屏恢复 M0 前的「问答」页；导航 8 项（问答/总览/复习/图谱/资料/知识点/错题/笔记）；**无场景标识**（`sceneBadge=false`） |
| `shots/M0RB-02-kps.png` | 知识点页**无**「＋新建知识点」、**无**「导入已整理」，保留「选择课件批量整理」；长摘要仍 2 行截断（`lineClamp=2`、clientH=40 vs scrollH=198，358 字摘要） |
| `shots/M0RB-03-mistakes.png` | 错题页**无**「＋记一道错题」（只剩基线就有的删除/清空操作） |
| `shots/M0RB-04-review.png` | 复习页文案不再承诺自动记错题（`promisesAutoMistake=false`） |
| `shots/M0RB-05-settings.png` | 设置中心**无**「学习场景」三选一（`sceneWord=false`、`sceneOpts=0`）；说明文案术语 0 命中（humanize 保留） |
| `shots/views/rb-*.txt` | 8 个视图真实渲染文本 grep `logs/ tools/ 卡 C- doc_id .json <doc_id> 抗上下文刷新` → **全部 0 命中** |
| `shots/result.json::M0RB-99-final-devmatch` | 页面全文术语命中 **[]**，JS 异常 **0** |

### 4.3 移动端 390×844（`_work/m0/waveB/mobile_probe.mjs`，CDP 真视口）
- 8 视图 `scrollWidth == clientWidth == 390`、首屏 `scrollYAtRender = 0`、JS 异常 0；截图 `shots/390/rb-01..08-*.png`。
- 备注：图谱页有 257 条恢复数据下的 SVG 节点贴近右边缘（rect right≈392-398），但 `documentElement.scrollWidth=390`（不产生页面横向滚动），符合门禁判据；如需严格 SVG 内边距可留 M1 打磨。

## 5. 测试输出（`_work/rollback/`）
```text
tests\run_all.py --only test_08 → 2 条 / 通过 2 / 退出码 0   run_all_test_08.log
tests\run_all.py --only test_09 → 7 条 / 通过 7 / 退出码 0   run_all_test_09.log
tests\run_all.py --only test_11 → 5 条 / 通过 5 / 退出码 0   run_all_test_11.log
tests\run_all.py --only test_12 → 2 条 / 通过 2 / 退出码 0   run_all_test_12.log
```
- `py_compile` OK；内嵌 JS `node --check` OK；`tests/test_10_search_regression.py` 已删；未跑全量（Lead 串行跑）。
- 尺寸/编码：dashboard.py 12920 行全 CRLF、无 BOM；`core/search.py` 与 251886f 字节一致。

## 6. 风险 / 交接注意
1. **tests/matrix.py 未改**（按任务约束）：其中仍登记 test_10/test_08 旧类等行，Lead 跑全量前需同步删除检索回归行、更新 F96+ 的 M0 回退状态，否则 `--only` 之外的矩阵统计会显示 MISSING。
2. 回退后 `kpForm()/bindKpForm()` 处于**未引用**状态（保留为公开契约，与 251886f 一致）；后端 `POST /api/kp`、`POST /api/mistake/delete` 等基线接口未动。
3. 回退实例的库快照来自用户恢复后的备份：含 **257 条知识点 / 2 条错题**（用户数据，与被测代码无关）；验证时额外用 `POST /api/kp` 造了 2 条长摘要/掌握度测试点（只落在沙箱副本）。
4. `notes/` 下 15 个用户笔记未跟踪、未改动；请 Lead 提交时按路径 add（勿 `git add -A`）。
5. 8756 真实看板仍跑旧进程：本次回退只改工作区代码，需 Lead 统一重启才对用户生效（重启前 `tools\backup_data.py`）。
6. 检索召回退回 M0 前基线（10 题回归 6/10、规则修复撤销）——这是用户要求的一部分，M1 若再动检索需重新建回归集。
7. 若需再次"回滚回滚"：`git diff 251886f` 的保留项即 M0-6a/M0-5/M0-8 全部内容；本报告 §2 列表可用于把功能型改动重新加回（但需重新评审）。

## 7. 回滚方式（如需恢复 M0 完整版）
`git checkout m0-complete -- tools/dashboard.py core/search.py tests/test_08_dashboard_fixes.py tests/test_10_search_regression.py tests/test_11_m0_scenario_copy.py tests/test_12_m0_import_ui_mobile.py` 即可回到回退前状态（数据不受影响）。
