# 教材补充 AI 卡（右栏）+ 导图横幅 AI 定位（task-30）

- 任务：`task-30` · 执行人：dash-fixer · 时间：2026-10-22 00:48–01:12
- 改动：`tools/dashboard.py`（**+657 / −71 行**；CRLF 保持、无 BOM）+ 新建 `tests/test_22_teach_ai_ui.py`（12 条）+ **更新 `tests/test_20_textbook_supplement_ui.py`**（改版带来的 2 条旧断言，见 §5）
- 未动：`core/`（后端 `core/teach.py` 新版由另一智能体交付，本任务只对接）、其它测试/matrix、真实数据/8756/3080；未 commit；**未真实调用 AI**（所有生成都在确认框处取消）

## 1. 结论

右侧「教材补充」栏升级为 AI 混合版：顶部固定「教材名 + 整份课件定位章（AI 定位/本地兜底小字 + 页范围 + 映射成本）」+ ≤3 张页图；中间**AI 补充卡**按状态渲染 `未生成（后端真实 estimate：1 次调用/上界 ≤¥X/模型 + 生成按钮 → confirmBox → POST kp/build → spinner → 刷新）`、`AI 已生成（verdict/summary/points(text+pages+kind)/gaps/citations 折叠/模型·费用·耗时/重新生成）`、`no_supplement`、`failed（错误+重试，本地兜底仍在）`；底部「教材原文（参考）」折叠区保留本地 passages + 教材补充点。**旧版纯本地产物**（无 chapter_map/ai）显示「旧版教材补充，建议重新 AI 定位」→ 按钮走 build（AI 映射+本地小节），不冒充 AI 已就绪。导图栏横幅按 `needs_backfill`/`legacy` 显示「AI 定位章节（约 ≤¥X）」，确认后 POST build，就绪行显示章节/本地小节数/可逐条生成 AI；已有产物只显示一行不重复花钱。原有小窗左树/正文/页图/笔记/上一/下一等零改动。

## 2. 改动块（tools/dashboard.py）

| 位置 | 内容 |
|---|---|
| `519-...` | CSS：`.mteach-cm`（定位章/来源/成本）、`.mteach-ai` 卡（badge/est/spin/verdict/points/kind/gaps/citations/foot）、`.mteach-legacy`（旧版提示）、`.mteach-local`（教材原文折叠）；390 下按钮自适应、无横向溢出 |
| `5360-5533` | `mteachAiCard` / `mmTeachGenerate` / `mmTeachLegacyRebuild`：未生成 estimate+确认框（成本/模型/1 次调用）→ `POST /api/textbook-supplement/kp/build`（`{doc_id,kp_id,force}`）→ 刷新；已生成/无需补充/失败/旧版各分支 |
| `5342-5358` | `mteachChapterLine`/`mteachPagesHtml`/`mteachLocalHtml`/`mteachAiPoints`（章映射、页图、本地原文折叠、points 渲染） |
| `5450-5570` | `mmTeachRender` 重排：顶部章映射 + 页图 → AI 卡 → 本地原文折叠；无教材/需补做/无匹配兜底状态保留 |
| `5571-5660` | 导图栏：`mteachReadyLine`（章节/小节数/可逐条 AI/来源/成本）、`mmTeachBackfill`（legacy/needs 横幅 + 就绪行）、`mmTeachBuild`（确认框含 estimate → POST build → 刷新） |
| `10906-11160` | Python 胶水：`_teach_normalize_estimate`/`_teach_estimate_map`、`_teach_augment_status`（把后端 `ai.map` → 顶层 `chapter_map`；`supplement.legacy`→`legacy/needs_backfill`；缺 estimate 时用后端 `estimate_map`）、`_teach_augment_kp`（合并顶层 `ai`、归一 estimate）、`api_textbook_supplement_kp_build`、`api_textbook_supplement_kp_estimate` |
| 路由 | 新增 GET `/api/textbook-supplement/kp/estimate`（只读锁内）；POST `/api/textbook-supplement/kp/build`（同步、**不持全局 _lock**）；`/api/textbook-supplement/kp` 返回 estimate；`/api/textbook-supplement/build` 返回 `meta.chapter_map`/`legacy`/`estimate`；`on_done=_teach_after_kp_build` 保持不变 |

## 3. 与后端实际契约（core/teach.py 新版）对接

- `supplement_status(doc_id)`：`supplement.{legacy,stale,ai_map_source,ai_map_cost_cny}`、`ai.{map{chapter_no,chapter_name,source,confidence,cost_cny},n_ai_ready,n_ai_missing,enabled,model}`、`needs_backfill/reason`；
- `estimate_map(doc_id)`：真实 MAP 估价（1 次小调用，无模型）→ 横幅「约 ≤¥X」；
- `estimate_kp_supplement(doc_id,kp_id)`：单条 KP 真实估价 → 未生成卡「1 次调用/上界/模型」；
- `analyze_kp_supplement(doc_id,kp_id,force=)`：按需 AI（1 次调用）；返回 `entry.ai{status,verdict,summary,points,gaps,citations,model,cost_cny,elapsed_s}`；
- `build_supplement(doc_id,force=,allow_ai=)`：AI 章节映射 + 本地小节定位（`SUPPLEMENT_VERSION=C-0125-teach-ai-v1`）；旧产物会被判 legacy；
- `supplement_for_kp`：`{ok,available,textbook,entry,ai,legacy,needs_rebuild,estimate,note}`；胶水把顶层 `ai` 并进 entry、estimate 归一并兜底。
- 后端暂缺/异常时全部 200/4xx JSON（不 500），estimate 缺成本时用兜底值并标 `estimate_is_fallback`。

## 4. 验收证据（qa-teach-ai@8933，沙箱假产物；截图 `_work/teach-ai-ui/shots/`）

### 4.1 旧版纯本地产物（`shots/TS-01~04`，`scenario-legacy.json`）
- 导图横幅：「检测到**旧版**教材补充（纯本地匹配，无 AI 章节映射）：建议重新 AI 定位……（≤¥0.0178，不改动原知识点）」+「重新 AI 定位并补做」；reason 行显示后端旧版原因；
- 右侧栏：旧版提示卡 +「重新 AI 定位并补做」按钮，**无「生成 AI 补充」按钮**（不冒充 AI 就绪）；本地 passages 3 段 + 页图仍在；章映射小字「本地兜底」；
- 点旧版按钮/横幅按钮：confirmBox 均含「约 1 次调用｜成本上界 ≤¥0.0178｜模型 deepseek-flash」→ **取消，未调 AI**。

### 4.2 假 AI 产物（`scenario-ai.json`，`TS-05~11`）
| 检查项 | 结果 |
|---|---|
| 导图就绪行 | 「教材补充已就绪 · 章节：第6章 作用于胆碱受体药 · p.70–p.82 · 本地小节：4 个 · 可逐条生成 AI 补充（AI 定位 · 映射成本 ¥0.0031）」 |
| AI 已生成卡 | badge「AI 已生成」；verdict/summary 正常；points 3 条（kind：机制补充/药物补充/易错提醒 + pages）；gaps 2 条（"课件没讲/没讲清"）；citations「教材出处（2 条）」展开后 2 条原文；底部「模型 deepseek-flash · 费用 ¥0.0213 · 耗时 8.4s」；「重新生成」；章映射「AI 定位 · 映射成本 ¥0.0031」 |
| 未生成卡 | badge「未生成」；estimate「预计 1 次调用 · 成本上界 ≤¥0.0092 · 模型 deepseek-flash」（后端真实估价）；「生成 AI 补充」→ confirmBox 含同样成本 → **取消，0 AI 调用** |
| no_supplement | 「AI：无需补充」+「AI 判断教材对应小节没有需要补充的内容。」+ summary |
| failed | 「生成失败」+ 错误人话 +「重试生成」；本地「教材原文（参考）」折叠仍在 |
| 本地兜底 | 「教材原文（参考）· 3 段 · 8 个补充点」折叠、页图 1 张正常、原有左树/正文正常 |
| JS 异常 | **0** |

### 4.3 390×844（`shots/narrow/`、`narrow-result.json`）
- 右侧栏默认收起、「📖 教材」抽屉 335px 在窗内；`docScrollWidth=390=clientWidth`、溢出元素 **0**；AI 卡（badge/points/verdict/本地折叠）在抽屉内完整渲染；关闭抽屉/Esc 正常；**0 JS 异常**。

### 4.4 测试
```text
tests\run_all.py --only test_22 → 12 条 / 通过 12 / 失败 0 / 退出码 0
tests\run_all.py --only test_20 → 12 条 / 通过 12 / 失败 0 / 退出码 0
tests\run_all.py（全量）        → 168 条 / 通过 168 / 失败 0 / 退出码 0（94.9s；矩阵 77 行全覆盖）
```
- `test_22_teach_ai_ui.py`（12 条）：AI 卡结构/estimate/按钮/四种状态、旧版提示、本地原文折叠、章映射、横幅 AI 文案、就绪行、源码路由断言（kp/build 不包 `with _lock`）、运行时错误降级（status/kp/estimate/build/kp-build 非法参数均 200 JSON，不触发模型）、原有功能/导航。
- `py_compile` + 内嵌 JS `node --check` OK；无 BOM、15414 CRLF。

## 5. test_20 的改动（Lead 指示，已注明原因）

task-30 改版后旧纯本地面板结构变为「AI 卡 + 本地原文折叠」，导致 task-27 的 test_20 两条静态断言过期：
1. `test_content_parts`：删除 `"补充段落"`（新 UI 无此静态标题），改为 `"教材原文（参考）"`、`mteach-local`、`mteach-ai` 等新结构断言；
2. `test_banner_and_build`：`"教材补充已就绪："` → `"教材补充已就绪"`（新就绪行用 `·` 分隔并带章节/小节），`"force: false"` → `"force: legacy"`（旧版补做走 force）。
其余断言原样保留（页图/教材补充点/底部注释/左树正文/1280/1279 响应式/关闭按钮层级等），用例数仍为 12，未动其它测试/matrix。

## 6. 环境与风险
1. qa-teach-ai 沙箱：复制真实 `logs/knowledge`/`figure_desc`/`outline` + index 快照；**手工写入假产物**（旧版变体 & 带 ai 字段的假 AI 产物），并从仓库同步了新版 `core/teach.py`/`kp_build.py` 做真实接口联调；`qa_teach` 页图使用沙箱内 452 页假教材 PDF + 沙箱 DB abs_path（真实数据/PDF 未动）。
2. **未真实调用 AI**：所有「生成/重新生成/重试/补做」都在 confirmBox 处取消；真实调用路径已接通（`analyze_kp_supplement` / `build_supplement`），请 Lead 联调时用沙箱开启一次真实调用验证费用与产物写回。
3. estimate 全部来自后端真实估价（横幅 MAP ≤¥0.0178、单 KP ≤¥0.0092）；仅当后端 estimate 缺 cost 时才用兜底值并标 `estimate_is_fallback`。
4. 旧版产物识别：以后端 `supplement.legacy` 为准，并用产物 meta（无 `chapter_map`/无任何 `ai`）兜底；真实站点的 40/41 旧产物会显示旧版提示 + 重新 AI 定位按钮（沙箱已用旧版变体验证）。
5. 同步 core 新后端后，沙箱全链路（status/kp/estimate/build/analyze/页图）均按新契约跑通；`api_kp_build_start` 的 on_done 仍自动补做（现在会先跑 1 次 AI 章节映射；失败/无 key 本地兜底）。
6. 并发测试踩坑：其它智能体同时跑全量时 `_work/verify` 的 PermissionError 会打乱 `--only`；本轮等静默窗口后串行跑，最终 168/168 全绿；`qa-teach-g` 等他人实例未触碰。

## 7. 回滚
`git checkout -- tools/dashboard.py tests/test_20_textbook_supplement_ui.py` + 删 `tests/test_22_teach_ai_ui.py` 即恢复（后端新函数保留但无调用方）；无数据/配置改动。
