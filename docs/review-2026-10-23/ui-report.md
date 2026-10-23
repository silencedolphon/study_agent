# 复习板块大改：自测 + 三步复习 + 悬浮窗 + 页签保持 + 笔记分组（task-33）

- 任务：`task-33` · 执行人：dash-fixer · 时间：2026-10-23 22:58–23:2x
- 改动：`tools/dashboard.py`（前端复习流程/笔记页/页签保持 + 5 条 `/api/review/*` 路由）；新建 `tests/test_24_review_ui.py`（11 条）
- 未动：`core/`（`core/review.py` 由 artifact-importer 交付，零 AI）、其它测试/matrix、真实数据/8756/3080；未 commit；**零 AI 调用**

## 1. 结论

复习页新增「课件复习流程」卡（9 份真实课件）：已自测显示「熟悉度 X%（全对 n/m）」、预习名词数、映射章节，按钮「继续复习 / 重新自测」；未自测显示「未自测」+「开始自测」。遗忘曲线月历每天的课件行新增「开始复习」（无自测先弹自测，有则进三步）。自测页：11 条随机 KP、44 个行内挖空 `input`（Tab/Enter 支持）、提交后逐条绿/红 + 正确答案 + 熟悉度 + 不熟悉列表 →「进入三步复习」。三步 runner：① 按导图顺序逐 KP（名称/总结/要点/页码，记住/再想想/进度 i/n）→ ② 整份课件笔记（5 组/5 篇，空态兜底）→ ③ 预习第6章 11 个重点名词（翻面看 def/why）→ 完成页（熟悉度/条目数/建议）。全局悬浮窗固定右下、跨页签/刷新恢复（localStorage）：「复习中 · 课件 · Step i/3 · x/n + 继续/最小化/结束」。页签保持：滚动位置按视图保存/恢复；知识点弹窗参数（mm/db）切页签后自动重放；主动关闭/Esc/评分后清空，不会自己弹回来。笔记页按「课件 → 绑定知识点」分组（24 篇/6 份课件/18 个 KP 组），支持搜索、打开全文、跳到知识点、「复习这份课件的笔记」。390 无横向溢出、悬浮窗在视口内不挡操作。

## 2. 改动块（tools/dashboard.py）

| 位置 | 内容 |
|---|---|
| CSS（月历卡前） | 复习页课件卡 `.rv-*`、自测挖空输入、三步卡、完成页、`#rev-float` 悬浮窗、笔记分组 `.nt-*`；`@media(max-width:560px)` 把月历 7 列压进 390、悬浮窗 210px |
| JS 复习区块（资料段前） | `UI_PERSIST` + `uiSaveScroll/uiRestoreScroll/uiRememberPopup/uiRestorePopup/uiForgetPopup`；`REV` 状态 + `localStorage('study_agent_review_flow_v1')`；`reviewBoot/revSave/revDrop`；`revFloatUpdate/revEnd`；`renderReviewHome/reviewCoursewaresHtml/reviewQueueHtml`；`revStartSelftest/renderReviewSelftest/submitReviewSelftest`；`revStartFlow/renderReviewFlow/reviewNotesStepHtml/renderReviewDone/revResumeFlow`；`viewNotes/noteKpNamesFromTree/noteRowHtml/bindReviewNotes`；`revStartFromMonth` |
| `viewReview` | 改为按 `REV.stage` 分发（selftest/flow/done/home），其它逻辑不变 |
| `renderDeckDay` | 月历明细行新增「开始复习」按钮（`data-act="review"` → `revStartFromMonth`） |
| `go()` | 切页前 `uiSaveScroll`；渲染后 `uiRestoreScroll` + `await uiRestorePopup` + 原入库进度条逻辑 |
| `evPopOutside` | 点顶部 `#nav` 视为切页：收起弹窗但保留参数（`keepNavClose`）；点其它处仍主动关 |
| `evPopClose` / `closeModal` | 主动关闭/评分后 `uiForgetPopup()`（评分后 `go(CURRENT)` 不会自动重弹） |
| `mmOpenKp` / `openKp` | 打开时 `uiRememberPopup({type:'mm'/'db',...})` |
| Python 胶水 | `_review_mod` 惰性 import；`api_review_coursewares/flow/selftest/selftest_submit/selftest_status`（异常统一 `{ok:false,error}`，后端的 `_code` 交给 `_json_auto`） |
| 路由 | GET `/api/review/coursewares`、`/api/review/flow`（只读锁内 conn）；GET `/api/review/selftest`、`/api/review/selftest/status`（锁外）；POST `/api/review/selftest/submit`（**锁外**，后端原子写 `logs/review/<doc>-自测.json`） |

## 3. 与后端契约（core/review.py，零 AI）

- `list_courseware_reviews(conn)` → 9 份课件 item（selftest/terms/chapter）；
- `start_selftest(doc_id)` → `{ok,doc_id,title,session_id,n_total:22,n_sample:11,kps,items:[{kp_id,name,segments}]}`，blank 带 `answer`（前端渲染为空输入、提交按 `i` 取值）；
- `submit_selftest(doc_id,session_id,answers)` → `{ok,familiarity,n_sample,n_familiar,n_unfamiliar,unfamiliar_ids,results}`，持久化沙箱 `logs/review`；
- `selftest_status(doc_id)` → 上次摘要；`build_flow(conn,doc_id)` → kps/notes.groups/notes.unbound/terms（真实数据：第6章 11 词）/selftest。
- 防御式：`core.review` 未就绪/抛错 → 200 `{ok:false,error}`，复习页显示人话不白屏；`doc_id<=0/session 缺失` 前置校验。

## 4. 验收证据（qa-review@8935，真实数据，0 AI；截图 `_work/review-ui/shots/`）

### 4.1 复习页 / 自测（`shots/RV-01~05`）
- 课件卡 9 份；doc46 卡「已自测 · 熟悉度 100%（全对 11/11）· 22 条知识点 · 预习名词 11 个 · 第6章 作用于胆碱受体药」+「继续复习/重新自测」；月历 3 个排期日，2026-10-24 明细行有「开始复习」；
- 自测页 11 条卡 / 44 个空白输入；填对提交 → 熟悉度 **100%**、11 条 `✓ 全对`、无红；「进入三步复习」可达 Step1。

### 4.2 三步 runner / 悬浮窗（`shots/RV-06~14`）
- 「记住了」推进 2/22；跳到末条 → Step2「② 笔记」5 组/5 篇；Step3 第 1/11 个名词「胆碱受体激动药 / cholinoceptor agonist」，显示解释后 def/why 正确；
- 完成页：「知识点 22 条 · 笔记 5 篇 · 重点名词 11 个」+ 建议；
- 切到问答页：`#rev-float` 仍显示「复习中 · 课件 · Step 1/3 · 1/22 + 继续/最小化/结束」；「继续」回到 review 且标题/进度一致；最小化变药丸「📖 复习中 · Step 1/3 · 点开继续」；结束（确认后）悬浮窗消失。

### 4.3 页签保持（`shots/ST-01~03`）
- 打开 KP 弹窗 → 切问答 → 切回：弹窗自动恢复（标题/正文/左树都在），`UI_PERSIST.popup` 参数正确；
- Esc 主动关闭 → 再切走切回：**不再自动弹**（`popup=null`）；
- 滚动保持：知识点页滚到 245 → 切笔记 → 切回：`scrollY` 245→245（异步内容高度不足时多次重试）。

### 4.4 笔记分组（`shots/RV-15~18`）
- 6 份课件组 / 18 个 KP 组 / 24 篇；每组显示篇数/字数/最近时间 +「复习这份课件的笔记」；
- 搜索「阿托品」→ 1 篇/1 组可见，无匹配时提示；打开全文 modal 正常；「跳到知识点」切到知识点页并打开对应 KP 弹窗。

### 4.5 390×844（`shots/narrow/narrow-01~05`）
- 复习页 `scrollWidth=390`、溢出元素 **0**（顺带修了月历 7 列在窄屏的横向溢出）；自测页 44 输入无溢出；悬浮窗 210×87 在视口内（right=380 ≤390），不挡底部按钮；跨页签可见；**0 JS 异常**。

### 4.6 测试
```text
tests\run_all.py --only test_24 → 11 条 / 通过 11 / 失败 0（退出码 0）
tests\run_all.py（全量）        → 190 条 / 通过 190 / 失败 0（104.1s；矩阵 80 行全覆盖，含后端 test_23 7 条）
```
`test_24_review_ui.py`：复习首页/月历入口、自测 UI、三步 UI、悬浮窗/localStorage、页签保持函数、笔记分组、5 路由源码与运行时降级（错参 200/400 JSON 不 500、submit 处无 `with _lock`）、原有导航/小窗/预习不回归。`py_compile` + 内嵌 JS `node --check` OK；无 BOM、全 CRLF。

## 5. 风险 / 已知问题
1. **后端 selftest 状态一致性（联调发现→已修复）**：联调中发现 v1 持久化文件跨会话合并导致“100% 但不熟 1”；已报 artifact-importer，后端升 `C-0127-review-v2`（只保留本次抽样、status/flow 从 kps 现算、legacy v1 也自洽）。沙箱同步 v2 后复测：status `familiarity=1.0 / n_familiar=11 / n_unfamiliar=0 / legacy=false`，课件卡显示「熟悉度 100%（全对 11/11）」；前端卡统一用 familiarity 推导全对条数，不依赖易漂移的 n_unfamiliar。
2. **自测答案随 start 响应下发**（segments 的 blank.answer）——这是后端契约与本页脚填写所需；页面用 `data-answer` 存值并渲染为空输入，不是安全边界（本机单人应用可接受）。
3. 自测输入内容只存在内存（切页签后回到复习首页会重新生成会话）；已得熟悉度持久化不受影响。未做「自测中途切页再回填」的持久化（时间盒内按优先级取舍）。
4. 复习首页课件卡依赖 `/api/review/coursewares`；后端未就绪时只显示人话+「重试」，不阻塞原队列/月历。
5. 沙箱真实数据：复制真实 `logs/knowledge`（26 文件）+ `notes`（24 篇）+ index 快照；自测提交写在沙箱 `logs/review/`；为验证月历入口用 API 在沙箱给 doc46 录入 1 个复习计划（真实计划未动）。
6. 全量测试需在静默窗口串行跑（其它智能体同时跑会争 `_work/verify` → PermissionError）；最终 190/190 干净通过。

## 6. 回滚
`git checkout -- tools/dashboard.py` + 删 `tests/test_24_review_ui.py` 即恢复（core/review.py 由后端任务保留）；无真实数据/配置改动。
