# 复习 v2 + 名词整理 + 化学结构式 · 独立门禁终验报告（task-38 第二段）

> 终验时间：2026-10-24 · 执行者：独立验收（QA，不写产品代码）
> 被测：**`c011540 feat(review+terms+structure): 复习自测 v2/三步窗口化 + 预习名词整理 + 化学结构式扫描管线`**（HEAD，工作区干净）
> 基线：[`baseline.json`](baseline.json) · 判据：[`PROTOCOL.md`](PROTOCOL.md)
> 工具：`gate_review_v2.py`（隔离实例 + API/结构模块 + sha 校验；`_v2_checks.py` 为终验适配检查）+ `cdp_review_v2.mjs`（CDP：v2 自测/三步窗口/悬浮窗/页签保持/名词卡片）
> **零真实 AI**（结构式 dry-run / 名词 0 AI / 自测本地）；**未碰真实 8756/logs/notes**（只读扫描与沙箱写入）。

## 0. 结论

| 验证组 | 改版前基线（HEAD `5abe6e6`） | 合并后终验（c011540） | 判定 |
|---|---|---|---|
| A 后端（自测 v3 / 提交 results / status v3 / terms preview / 错误边界） | FAIL（9 项） | **PASS（11/11）** | ✅ |
| B 前端（结构路径/已掌握、三步窗口+跳过、悬浮窗回当前 step、页签保持 5 档、主动关闭、笔记） | FAIL（15 项） | **PASS（15/15）** | ✅ |
| C 名词整理卡片（按钮/预览/来源） | FAIL（3 项） | **PASS（2/2）** | ✅ |
| D 化学结构式（scan/dry-run/文本漏抽/产物/CLI 取消/无自动 AI） | FAIL（5 项） | **PASS（7/7）** | ✅ |
| E 原产物/notes sha + 零 AI | PASS | **PASS（2/2）** | ✅ |
| 专项测试 test_23/24/26/27/29 | — | **5 / 12 / 4 / 8 / 25 全 OK** | ✅ |
| 全量回归 + 健康检查 | — | **226 条·225 通过·0 失败·1 设计内跳过·退出码 0；矩阵 85/85；healthcheck PASS 9/0** | ✅ |

**本轮功能通过：复习 v3 自测/三步窗口化/页签保持、预习名词整理（11→10 过滤、0 AI）、化学结构式管线（候选>0、默认 dry-run、CLI 强制确认）全部达标，原功能无回归。**

## 1. 代码指纹与范围

| 项 | 值 |
|---|---|
| git HEAD | `c011540`（工作区干净） |
| `core/review.py` sha256 | `3a5cc8666526a6b4…`（C-0128-review-v3） |
| `core/terms_preview.py` sha256 | `bfa1c0932e8c657d…`（C-0129-terms-preview-v1） |
| `core/structures.py` sha256 | `9db52713d27ab9d9…`（结构式扫描/dry-run/识图管线） |
| `tools/dashboard.py` sha256 | `f001bc02f534f842…`（复习 UI v2/名词卡片） |
| `tools/structure_build.py` sha256 | `909b849e21a7a99e…`（CLI：--dry-run/--status/--run+--yes） |
| 实例 | `qa-reviewv2-g:8934`（独立命名；已 down、端口释放） |
| 沙箱数据 | doc46（22 KP）/doc50 预习第6章（11 名词）/notes 24 篇；doc40、doc47 PPTX（结构页） |

## 2. A 后端 → PASS（11/11）

| 子检查 | 证据 |
|---|---|
| selftest v3 | 200；`n_total=22 / n_sample=11 / items=11`；每条 item 键= `{kp_id,name,node_path,branch,summary,unfamiliar}`；branch ≥2 级、末级=名称；**无 segments/answers** |
| submit 全 True | 200：`familiarity=1.0`、`n_mastered=11`、`n_unmastered=0`、别名 `n_familiar=11/n_unfamiliar=0`、`unfamiliar_ids=[]` |
| 一 False | 200：`familiarity=0.9091`、`n_mastered=10`、`n_unmastered=1`、`unfamiliar_ids=[KP-N07-1]` |
| status/flow 自洽 + v3 | status `n_mastered=10/n_unmastered=1/familiarity=0.9091`，flow.selftest 同口径、flow 中该 KP `unfamiliar=true`；沙箱 `logs/review/46-自测.json` `meta.version=C-0128-review-v3` |
| `GET /api/terms/preview/options` | 200 `ok:true can_rebuild:true`；预习候选 **11**、章节第6章 |
| `POST /api/terms/build-preview` | 200：`source=preview_key_terms`、**n_candidates=11 / n_selected=10 / n_dropped=1**、`dropped=["调节痉挛"]`、**calls=0 / cost_cny=0**、`embed_used=true` |
| 名词产物 schema | `logs/knowledge/46-名词解释.json`：meta.source=preview_key_terms、pipeline_version=C-0129-preview、usage_total.calls=0、cost=0；terms 10 条含 `term/en/def/first_kp_id/kp_ids/seg_id/first_page` 兼容旧 schema |
| 无预习 doc40 | 200 `ok:false source:none`（章节第12章兜底）；**不自动 AI、不写 40-名词解释.json** |
| 错误边界 | selftest/status/options doc=0 与 build doc=99999 → 200 `ok:false`，无 500 |
| 不写数据库 | 自测/名词整理前后 study.db sha256 不变 |

## 3. B 前端 → PASS（15/15）

| 检查 | 证据 |
|---|---|
| 自测 v3 | 11 条 `.rv-path` 结构路径 + 「展开总结」；每条「已掌握/未掌握」按钮（无填空输入） |
| 提交结果 | 全标记已掌握 → 「本次熟悉度 100%」；「进入三步复习」按钮 |
| Step1 窗口 | 自动打开现有知识点窗口（`.evpop` +「重新打开知识点窗口」提示）；有「跳过第一步（知识点）」 |
| Step2 窗口 | 进入 Step2 自动打开笔记窗口（「笔记窗口已打开」+ 5 分组笔记）；「跳过第二步（笔记）」 |
| Step3 窗口 | 名词窗口（预习重点名词 11 个、名词窗口已打开）；「跳过第三步（重点名词）」 |
| 悬浮窗 | 切问答再回点「继续」→ 回到 Step2 并重开窗口（step 一致） |
| **页签保持** | 0/100/300/800/1500ms 五个采样点弹窗均可见（同一 KP-N03-1，不闪不消失） |
| 主动关闭 | Esc/主动关闭后切页签不再自弹 |
| 三步完成 | 跳过第三步后出现「✅ 本次复习完成」 |
| 笔记 | 按课件+KP 分组 + 搜索框；390 无横向溢出；`js_errors=[]` |

截图：[`shots/POST-01-selftest-v3.png`](shots/POST-01-selftest-v3.png)、[`POST-02-step2-float.png`](shots/POST-02-step2-float.png)、[`POST-03-popup-keep.png`](shots/POST-03-popup-keep.png)、[`POST-04-terms-card.png`](shots/POST-04-terms-card.png)、[`POST-05-mobile.png`](shots/POST-05-mobile.png)。

## 4. C 名词整理卡片 → PASS（2/2）

- 知识点页 `#term-preview`：「预习第6章候选 **11** 个名词 · 当前来源：**预习重点名词（10 个）** · 0 AI」+ 按钮「按预习重点名词重新整理（0 AI）」；来源/候选/预览状态齐全。

## 5. D 化学结构式 → PASS（7/7）

| 子检查 | 证据 |
|---|---|
| 模块 | `core/structures.py` 存在（PPTV XML 扫描 + 选择性识图 + 提示词真源） |
| scan 候选 | `scan_pptx(doc40, dry_run=True)`：26 页、**候选 5 页**（矢量元素多/组合形状+图片）；doc47 候选>0 |
| dry-run 0 写 | 扫描前后 `logs/struct` 无任何变化（0 写盘） |
| 文本漏抽 | `scan_text_gaps(doc40)` 返回结构页/文本层对比 schema（n_pages/candidates/缺图等） |
| 结构产物 schema | `47-结构页.json`：`meta`（version/doc_id/thresholds）+ `candidates[]`；**无 calls/cost/model 记账** |
| CLI 取消 | `structure_build.py --doc 47 --run` 无 `--yes` → 打印费用上界与「确认开始真实识图？[y/N]」、stdin 非交互 → **退出码 3、stderr「已取消：未发生任何模型调用」、0 写盘** |
| 无自动 AI | dry-run calls=0/cost=0；真实调用必须显式 `--run` + `--yes` |

## 6. E 数据安全 / 零 AI

- 原 KP/预习产物与 notes sha256 不变；唯一新增为 `logs/knowledge/46-名词解释.json`（名词整理产物，允许项）；study.db sha 不变；
- 全流程 `KB_TEACH_AI=0`、结构 dry-run、名词 0 AI、自测本地 → **0 真实模型调用**。

## 7. 专项/全量/健康检查

| 门禁 | 结果 |
|---|---|
| `tests/test_23_review_flow.py` | **Ran 5 · OK** |
| `tests/test_24_review_ui.py` | **Ran 12 · OK** |
| `tests/test_26_review_v3.py` | **Ran 4 · OK** |
| `tests/test_27_terms_preview.py` | **Ran 8 · OK** |
| `tests/test_29_structure_scan.py` | **Ran 25 · OK** |
| `tests/run_all.py` | **Ran 226 tests · OK (skipped=1) · 退出码 0**；226 条 / 225 通过 / 0 失败 / 1 跳过；矩阵 **85/85** |
| 跳过说明 | `test_01_health.test_real_data_untouched`（真实 8756 使用中，设计内跳过） |
| `tools/healthcheck.py` | **PASS 9 / FAIL 0 / SKIP 0** |

原始输出：[`evidence-post-console.txt`](evidence-post-console.txt)、`evidence-test_23/24/26/27/29.txt`、[`evidence-run_all-post.txt`](evidence-run_all-post.txt)、[`evidence-healthcheck-post.txt`](evidence-healthcheck-post.txt)。

## 8. 基线/终验对比

| 组 | 基线（HEAD 5abe6e6，`--code-ref HEAD`） | 终验（c011540） |
|---|---|---|
| A | FAIL（selftest 旧字段含 answer、submit results 失败、terms 404） | **PASS 11/11** |
| B | FAIL（填空自测、无窗口/跳过） | **PASS 15/15** |
| C | FAIL（无整理按钮/预览/来源） | **PASS 2/2** |
| D | FAIL（structures.py 不存在） | **PASS 7/7** |
| E | PASS | **PASS 2/2** |

## 9. 残留风险与边界（如实说明）

1. **结构式真实识图未调用**：本次只验证 dry-run/候选扫描/产物 schema/CLI 取消（零成本）；真实视觉模型效果、费用与失败重试由 test_29 fake vision 用例覆盖，实际效果待用户显式 `--run --yes` 后试用。
2. **结构扫描读取真实 PPTX**：沙箱 DB 的 doc40/47 `abs_path` 指向真实 `raw/*.pptx`（只读扫描，不写真实 logs）；`scan_pptx(dry_run=False)` 产物仅写沙箱 `logs/struct/`。
3. **名词整理**：doc46 预习 11 名词 → 保留 10 / 剔除「调节痉挛」；真实其它课件按同规则过滤，极端相关度边界由 test_27 覆盖。
4. **自测随机性**：每次随机抽 11/22；验证了全对/一错两路径与 v3 持久化，历史 v2 文件 legacy 升级由 test_26 覆盖。
5. 覆盖范围：doc46 复习 + doc40/47 结构扫描 + 名词整理；未逐一覆盖所有课件/章节组合。
6. 全量 1 条跳过为真实数据在跑所致（设计内）；真实 8756 由 Lead 重启并只读检查（复习页/自测可用）。

## 10. 证据索引

| 证据 | 路径 |
|---|---|
| 门禁结果（A–E 逐项 + 证据 + 截图清单） | [`gate-review-v2-result.json`](gate-review-v2-result.json) |
| 改版前基线 | [`baseline.json`](baseline.json) |
| 判据/契约 | [`PROTOCOL.md`](PROTOCOL.md) |
| 截图（POST 5 张 + result；基线 BASE-*） | [`shots/`](shots/) |
| 专项/全量/健康检查原始输出 | [`evidence-*.txt`](.) |
| 工具 | [`gate_review_v2.py`](gate_review_v2.py)、[`_v2_checks.py`](_v2_checks.py)、[`cdp_review_v2.mjs`](cdp_review_v2.mjs) |
