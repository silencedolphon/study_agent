# 复习 UI v2 + 页签保持修复 + 名词整理入口（task-36）

- 任务：`task-36` · 执行人：dash-fixer · 日期：2026-10-24
- 改动：`tools/dashboard.py`（**+693 / −199 行**；CRLF、无 BOM）+ 更新 `tests/test_24_review_ui.py`（12 条，按 v3 契约重写旧挖空断言）
- 未动 `core/`（task-35 后端 `C-0128-review-v3` + `core/terms_preview.py` 由 artifact-importer 交付）、其它测试/matrix、真实数据/8756；未 commit；**零 AI 调用**
- 优先级执行：页签保持（P0，含修复前复现）→ 自测重做 → 三步窗口化 → 名词整理 → 悬浮窗细节

## 1. 结论

1. **页签保持（用户点名 bug）已修**：切回旧页签时弹窗 **0ms 即完整可见、不闪不消失**（修复前：0ms 隐藏 → 100ms 空白占位 → 300ms 才有内容；滚动恢复还可能把弹窗二次关掉）。做法是离开前抓 DOM+参数*快照*，`go()` 里**先同步摆回快照再渲染页面**，渲染后静默刷新内容；恢复期 `holdUntil` 抑制"滚动即关"。主动关闭/Esc/评分仍清空参数+快照，不会自己弹回来；滚动位置保留。
2. **自测 v2**：不再挖空。每条抽中 KP 显示「父级→自身」结构路径树（branch，末级高亮）、「展开总结」折叠、右侧「已掌握/未掌握」单选；全部标记后才能提交 `{results:{kp_id:bool}}`；结果页显示熟悉度=已掌握/抽样 + 未掌握列表 → 进入三步。
3. **三步窗口化**：Step1 直接打开现有知识点窗口（`mmOpenKp`，不是自定义卡片）；Step2 笔记窗口（modal，按 KP 分组，点笔记用 `openNote` 打开原文）；Step3 名词窗口（翻面看 def/why，上一个/下一个）；每步可跳过；悬浮窗「继续」= 重开当前步窗口（切到问答页也有效），并提供 下一个/下一步/跳过本步/结束。
4. **名词整理入口**：知识点页名词卡新增「按预习重点名词重新整理（0 AI）」：有预习 → 显示「预习第N章候选 M 个 · 当前来源：预览名词/旧管线/暂无」；无预习 → 人话提示（无按钮）；点击 → confirmBox 说明零 AI/只留相关词 → POST → 刷新 `/api/terms`。
5. 新增 2 组名词路由（任务书命名 + 后端建议命名双挂）：`GET /api/terms/preview/options|status`、`POST /api/terms/preview/build|build-preview`。

## 2. 页签保持：修复前后 CDP 证据（0/100/300/800/1500ms）

探针：`_work/review-ui-v2/persist_probe.mjs`（打开 KP 弹窗→切问答→切回，逐步采样 `.evpop` display/内容/标题）。原始数据：`shots/persist-before.json`、`shots/persist-after.json`。

| 切回后 | 修复前 | 修复后 |
|---|---|---|
| 0ms | `display:none`（还没恢复） | `display:flex`，**完整内容**（bodyLen=230，标题含 KP id） |
| 100ms | `display:flex` 但 **`#mmkp-body` 不存在、bodyLen=0**（"正在读取…"占位，内容闪一下） | 完整内容（bodyLen=230） |
| 300ms | 完整内容 | 完整内容 |
| 800ms | 完整内容 | 完整内容 |
| 1500ms | 完整内容 | 完整内容 |
| 二次切走→切回 | 0ms 隐藏 → 150ms 才显示 | 0ms 即完整可见 |
| 主动 Esc 关闭后再切走切回 | 不弹（正确） | 不弹（正确，`popup=null`、快照清空） |

根因（已写进代码注释）：① `evPopOutside` 切页时只 `display:none`，DOM/位置未快照；② 旧实现 `go()` 等视图渲染完才 `uiRestorePopup()`，且 `mmOpenKp` 先 mount「正在读取…」占位（闪）；③ 恢复滚动位置产生的 `window.scroll` 被 `evPopScroll` 捕获，把刚恢复的弹窗再次关闭（消失）。
修复实现（`tools/dashboard.py:1352` go / `2397` 快照函数）：
- `uiSnapshotPopup()/uiSnapshotModal()`：离开前记录 innerHTML + class + dataset + 参数（db 弹窗另存 modalbox）；
- `uiShowPopupSnapshot()`：`go()` 里**渲染前**同步摆回快照并重新挂 click/keydown/scroll 监听、`evPopPlace` 定位；
- `uiRefreshMmPopup()`：无占位屏，直接请求 `/api/summary-kp` 后整块替换；失败保留快照；
- `UI_PERSIST.holdUntil`：恢复期（2s）内 `evPopScroll` 不自动关闭；
- 主动关闭/Esc/评分：`uiForgetPopup()+uiClearSnapshot()`，不回弹；滚动仍按视图保存/重试恢复。

## 3. 改动块（tools/dashboard.py）

| 位置 | 内容 |
|---|---|
| 1352–1385 | `go()`：离开抓滚动+弹窗快照；渲染前 `uiShowPopupSnapshot()`；渲染后恢复滚动+静默刷新；渲染期间又切走则不恢复 |
| 1499–1560 | `evPopOutside/evPopClose/evPopScroll`：nav 切页保留参数+快照；主动关闭清空；恢复期滚动不关窗 |
| 2390–2470 | 快照基础设施：`UI_PERSIST`（popupSnap/holdUntil）、`uiSnapshotPopup/Modal`、`uiShowPopupSnapshot`、`uiRefreshMmPopup/DbPopup`、`uiRestorePopup` |
| 2728–2928 | 自测 v2：`reviewBranchOf/reviewPathHtml/reviewSummaryHtml`、`renderReviewSelftest`（路径树+展开总结+已掌握/未掌握单选+全选才可提交）、`submitReviewSelftest`（POST results）、结果页 |
| 2930–3220 | 三步窗口化：`revStartFlow`（自动开当前步窗口）、`revOpenStepWindow/revOpenNotesWindow/revOpenTermWindow`、`revFlowNext/revSkipStep/revContinue`、`renderReviewFlow`（状态页+重开窗口按钮）、完成页（已掌握/抽样） |
| 3223–3310 | 悬浮窗 v2 + 名词整理入口 JS：`revFloatUpdate`（继续/下一个/跳过/结束，按 step 变化）、`refreshTermsPreview/buildTermsPreview` |
| 12545–12600 | Python 胶水：`_terms_preview_mod`、`api_terms_preview_options(conn,doc_id)`、`api_terms_build_preview(payload)`（force 默认 true、log noop） |
| 16407/16570 | 路由：GET `/api/terms/preview/options`（+ `/status` 别名，只读锁内）；POST `/api/terms/preview/build`（+ `/build-preview` 别名，锁外写产物） |
| 自测提交胶水 | 接受 `results`（优先）/旧 `answers`，透传 `submit_selftest(doc_id, session_id, results)` |

## 4. 验收证据（qa-review2@8936，真实 index/logs/notes，0 AI）

截图 `_work/review-ui-v2/shots/`（宽屏）+ `shots/narrow/`（390×844）。

### 4.1 自测 v2（V2-01~04）
- 复习页 9 份课件卡；doc46「已自测 · 熟悉度 100%（已掌握 11/11）· 22 条知识点 · 预习名词 11 个 · 第6章」+ 继续复习/重新自测；
- 自测页：11 条卡 / 11 个结构路径（35 个路径节点，末级 self 高亮）/ 11 个「展开总结」/ **22 个已掌握·未掌握按钮**；提交按钮初始禁用「提交自测（0/11 已标记）」；展开总结+全部标记后启用；无任何输入框（`inputs=0`，挖空已移除）；
- 提交 → 结果页「本次熟悉度 100%（已掌握 11/抽样 11）」+ 11 个 ✓ + 进入三步复习。

### 4.2 三步窗口化 + 悬浮窗（V2-06~13）
- 进入三步：Step1 **直接打开现有知识点窗口**（弹窗标题「受体选择性与作用部位 KP-N03-1」，左侧树/教材补充等原窗口能力都在）；悬浮窗「Step 1/3 · 1/22 + 继续/下一个/跳过第一步（知识点）/结束」；
- 悬浮窗「下一个」→ 窗口换到 KP-N04-1、进度 2/22；「跳过第一步」→ Step2 笔记窗口（5 篇/按知识点分组）；
- 点笔记标题 → 打开原文（openNote）；关闭后悬浮窗「继续」→ **回到笔记窗口**（不是只回复习页）；
- 「下一步」→ Step3 名词窗口（第 1/11：胆碱受体激动药 / cholinoceptor agonist）；显示解释 → def/why；「跳过第三步」→ 完成页（知识点 22 条 · 笔记 5 篇 · 重点名词 11 个 + 建议），悬浮窗隐藏；
- 跨页签：切到问答 → 悬浮窗仍显示 Step 1/3 + 当前 KP；点「继续」→ 回复习页并**重新打开当前步知识点窗口**。

### 4.3 页签保持（persist_probe，宽屏）
- 见 §2 表格；修复后 0ms 即完整可见、7 个采样点全部可见且 bodyLen 稳定；主动关闭后切走再回不弹；滚动 245→245 保留。

### 4.4 名词整理入口（V2-14~16）
- doc46：状态「预习第6章候选 11 个名词 · 当前来源：暂无 · 0 AI」+ 按钮；点击 → confirmBox「不调用 AI、不产生模型费用…覆盖旧文件前自动备份 .bak」；确认后 → 「当前来源：预习重点名词（10 个）」、名词列表 10 个（候选 11 → 保留 10，剔除 1）；
- doc40（第12章无预习产物）：「该教材章节还没有预习总结（第12章），先在「预习」页生成预习总结…」，不显示按钮。

### 4.5 390×844（`shots/narrow/narrow-v2-01~05` + `narrow-result.json`）
- 复习首页/自测页/三步弹窗+悬浮窗/Step2 笔记/名词入口：`docScrollWidth=390=clientWidth`，溢出元素 **0**；悬浮窗 210×116 在视口内（right=380）；KP 弹窗 366×523 在视口内；名词入口与 10 个名词正常；**0 JS 异常**。

### 4.6 测试
```text
tests\run_all.py --only test_24 → 12 条 / 通过 12 / 失败 0（退出码 0）
tests\run_all.py（全量）        → 见 §6（最终结果）
```
`test_24_review_ui.py` 更新：按 review v3 契约重写自测断言（结构路径/已掌握按钮/results 提交/无 seg·answers），新增三步窗口函数、悬浮窗继续/下一个/跳过、页签快照函数与 `go()` 顺序、名词入口与 4 条 terms 路由/锁外断言；旧 5 条失败均因此修复（`TestSelftest.test_selftest_ui`、`TestFlowRunner.test_three_steps`、`TestReviewRoutes.test_source_routes_and_contract`、`test_answer_hidden_from_initial_html`、`TestFloatAndPersistence.test_float_widget`）。

## 5. 与后端契约（C-0128-review-v3 + terms_preview）

- `GET /api/review/selftest`：`items:[{kp_id,name,node_path,branch[{name,level}],summary,unfamiliar}]`（前端不再读 segments/answers）；
- `POST /api/review/selftest/submit`：`{doc_id,session_id,results:{kp_id:bool}}` → `familiarity/n_sample/n_mastered/n_unmastered/unfamiliar_ids/results`；胶水也兼容 `answers`；
- `GET /api/review/selftest/status` / `flow`：mastered 口径（兼容旧 n_familiar 别名）；前端课件卡显示「熟悉度%（已掌握 n/m）」；
- `terms_preview_status(doc_id, conn=conn)`：`{ok,source,chapter,preview{exists,n_candidates,textbook_doc_id},current{exists,is_current,source,n_terms},can_rebuild,calls,cost_cny}`；
- `build_terms_from_preview(doc_id, force=True)`：写 `<doc>-名词解释.json`，前端 toast「候选→保留 N 个（0 AI）」。

## 6. 风险 / 备注
1. 自测的"已掌握/未掌握"选择只存内存（切页签后回到自测页会重置选择；结果已落盘/熟悉度不丢）——时间盒内按优先级取舍。
2. 三步 Step2/Step3 用现有 modal；切页签时窗口会收起，悬浮窗「继续」可重开当前步（已验证）。Step1 的知识点弹窗同时受页签保持快照机制保护，切回会自动恢复。
3. 名词整理写产物（sandbox `logs/knowledge/46-名词解释.json`）；沙箱验证后真实 logs 未写；`build_terms_from_preview` 失败/无预习均 200/400 JSON 不 500。
4. 后端 v3 legacy 兼容（v1/v2 自测文件）已在 status 层处理；前端只认 `mastered/familiarity`。
5. 并发跑测试会互相清 `_work/verify`（本轮首次全量出现 test_07/test_18 并发污染，非本改动；已等静默窗口复核）。
6. 真实数据隔离：复制真实 index 快照/logs/notes；doc46 月历计划与名词产物只在沙箱写入；qa-review2 用完 down+删目录。

## 7. 回滚
`git checkout -- tools/dashboard.py tests/test_24_review_ui.py` 即恢复（core/terms_preview 由后端任务保留）；无真实数据/配置改动。
