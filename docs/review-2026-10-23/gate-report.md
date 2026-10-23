# 复习功能 · 独立门禁终验报告（task-34 第二段）

> 终验时间：2026-10-23 · 执行者：独立验收（QA，不写产品代码）
> 被测：**`ce5f63b feat(review): 复习板块重做（自测挖空/熟悉度 + 三步复习 + 悬浮进度 + 页签保持 + 笔记整理）`**
> （当前 HEAD `7f23d0d` = ce5f63b + docs 归档，产品代码同 ce5f63b；工作区干净）
> 基线：[`baseline.json`](baseline.json) · 判据：[`PROTOCOL.md`](PROTOCOL.md)
> 工具：`gate_review.py`（隔离实例 + 5 条 `/api/review/*` + sha/持久化校验）+ `cdp_review.mjs`（CDP：自测/三步/月历/悬浮窗/跨页签/笔记/390/原有功能）
> **零 AI**（review 纯本地）；**未碰真实 8756/真实 logs/notes**（全部在沙箱；沙箱内可写自测结果与复习计划种子）。

## 0. 结论

| 验证组 | 改版前基线（HEAD e48d4ec） | 合并后终验（ce5f63b） | 判定 |
|---|---|---|---|
| A 后端 5 条 review API（自测/熟悉度/持久化/flow/错误边界） | FAIL（全 404） | **PASS（11/11）** | ✅ |
| B 前端（课件卡/自测/三步/月历/悬浮窗/页签保持/笔记分组/390/0 JS） | FAIL | **PASS（20/20）** | ✅ |
| C 原有复习队列/月历/导图/预习/设置 | PASS | **PASS（4/4）** | ✅ |
| D 原产物/笔记 sha 不变 + 零 AI | PASS | **PASS（2/2）** | ✅ |
| 专项测试 test_23 / test_24 | — | **7 OK / 11 OK** | ✅ |
| 全量回归 + 健康检查 | — | **190 条·189 通过·0 失败·1 设计内跳过·退出码 0；矩阵 82/82；healthcheck PASS 9/0** | ✅ |

**复习功能通过：自测挖空/熟悉度、三步复习、月历入口、全局悬浮窗、页签状态保持、笔记分组全部达标，原有功能无回归。**

## 1. 代码指纹与范围

| 项 | 值 |
|---|---|
| git HEAD | `7f23d0d`（= ce5f63b 产品代码 + docs 归档；工作区干净） |
| `core/review.py` sha256 | `9de9c05f421a6328…`（= HEAD；C-0127-review-v2） |
| `tools/dashboard.py` sha256 | `011e4a61edf6a0cf…`（= HEAD；复习前端） |
| `core/study.py` sha256 | `60f5d31bed98b171…`（= HEAD；deck 数据） |
| 实例 | `qa-review-g:8933`（独立命名；已 down、端口释放） |
| 沙箱数据 | doc46 `46-末级知识点总结.json`（22 KP）、doc50 `50-预习总结-第6章.json`（11 名词）、真实 notes 24 篇副本 |

## 2. A 后端 → PASS（11/11）

| 子检查 | 证据 |
|---|---|
| `GET /api/review/coursewares` | 200、`ok:true`、9 份课件；doc46 `n_kps:22`、terms 可用 11、chapter.no=6 |
| `POST /api/deck-enroll`（沙箱种子） | 200、`plan_id:16`、n_rounds=5、intervals=[1,3,7,16,35]、`already:false`（仅写沙箱 DB，供月历入口验证） |
| `GET /api/review/selftest?doc_id=46` | 200；`n_total=22`、`n_sample=11`、`items=11`、session 有效 |
| 挖空/答案/提示 | 每条 KP 1~4 个 blank，均含 `answer`+`hint`，答案可回填原文 |
| 全对 submit | 200：`familiarity=1.0`、`n_sample=11`、`n_total=22`、`n_familiar=11`、`n_unfamiliar=0`、`unfamiliar_ids=[]` |
| 故意错一空 | 200：`familiarity=0.9091`（10/11）、`n_familiar=10`、`n_unfamiliar=1`、`unfamiliar_ids=["KP-N03-1"]` |
| status 自洽/持久化 | 200 `ok:true exists:true`，`familiarity=0.9091 / n_sample=11 / n_unfamiliar=1 / legacy=false`；沙箱 `logs/review/46-自测.json` 已写 |
| `GET /api/review/flow?doc_id=46` | 200：`kps=22`、`notes.groups=22`、绑定笔记 5 篇、`n_notes=5`、`stages=3` |
| flow terms | `source=preview_chapter`、`n=11`、`chapter.no=6`（第6章 作用于胆碱受体药，p.70-82，source=local_fallback） |
| flow selftest 同口径 | `familiarity=0.9091`、`n_unfamiliar=1`、同 `unfamiliar_ids` |
| 错误分支 | flow/selftest/status doc=0 与伪造 session submit 全 **200 `ok:false`**（无 500） |

## 3. B 前端 → PASS（20/20）

| 检查 | 证据 |
|---|---|
| 复习页课件卡 | 9 张卡（标题 + 「未自测」徽标 + `开始自测` 按钮）；「课件复习流程」标题；到期队列/月历仍在 |
| 月历「开始复习」 | 点击沙箱种子计划日 → 日清单表格：doc46 第 1/5 轮「待复习」+ **开始复习** 按钮 |
| 自测页 | 11 题、挖空输入（`data-answer` 不显示）、「提交自测」；答案填入提交 → **本次熟悉度 100%（全对 11/11）** |
| 自测结果 | 「进入三步复习」按钮出现 |
| 悬浮窗 + 自测跨页签 | 自测中 `#rev-float.on` 可见；切「问答」再回 → 自测内容与悬浮窗都恢复 |
| 三步 Step1 | 「① 知识点 / ② 笔记 / ③ 重点名词」3 chips；KP 名称/导图位置/页码/总结/要点；「再想想」「记住了」 |
| 三步 Step2 | 22 KP 笔记分组（`.rv-note-grp`），进入第三步按钮 |
| 三步 Step3 | `.rv-term` 名词卡；「显示解释」展开定义；「完成复习」 |
| 完成页 | 「✅ 本次复习完成」+「复习条目：知识点 22 条 · 笔记 5 篇 · 重点名词 11 个」+ 建议 |
| KP 弹窗跨页签 | 打开 KP-N03-1 弹窗 → 切问答 → 回 kps → **自动恢复同一 KP**（`.mmkp-side` 可见） |
| 主动关闭不自弹 | `evPopClose()` 后切问答再回 → 无弹窗 |
| 笔记分组 | 6 个课件组 / 18 个 KP 组 / 24 篇笔记（`.nt-grp/.nt-kp/.nt-note`）；搜索框输入「药理」有过滤响应 |
| 390 窄屏 | `scrollWidth=390=clientWidth` 无横向溢出；自测中悬浮窗可见 |
| JS 异常 | `errors=[]`（无 `Runtime.exceptionThrown`） |

截图：[`shots/POST-02-selftest.png`](shots/POST-02-selftest.png)、[`POST-03-flow-done.png`](shots/POST-03-flow-done.png)、[`POST-04-notes.png`](shots/POST-04-notes.png)、[`POST-05-mobile.png`](shots/POST-05-mobile.png)（+ `POST-review-result.json`）。

## 4. C/D → PASS（4/4 + 2/2）

- 原有：复习页到期队列/遗忘曲线月历、知识点小窗左树（`.mmkp-side`+高亮）、预习页、设置中心（5 管线/分组/搜索）均正常；
- 原 KP/预习产物与 **notes 笔记 sha256 全流程不变**（review 只读；仅沙箱 `logs/review` 写自测结果）；
- 零 AI：review API 无 model/cost 字段与调用记录，纯本地计算。

## 5. 专项/全量/健康检查

| 门禁 | 结果 |
|---|---|
| `tests/test_23_review_flow.py` | **Ran 7 tests · OK** |
| `tests/test_24_review_ui.py` | **Ran 11 tests · OK** |
| `tests/run_all.py` | **Ran 190 tests · OK (skipped=1) · 退出码 0**；190 条 / 189 通过 / 0 失败 / 1 跳过；矩阵 **82/82** |
| 跳过说明 | `test_01_health.test_real_data_untouched`（真实 8756 使用中，设计内跳过） |
| `tools/healthcheck.py` | **PASS 9 / FAIL 0 / SKIP 0** |

原始输出：[`evidence-post-console.txt`](evidence-post-console.txt)、[`evidence-test_23.txt`](evidence-test_23.txt)、[`evidence-test_24.txt`](evidence-test_24.txt)、[`evidence-run_all-post.txt`](evidence-run_all-post.txt)、[`evidence-healthcheck-post.txt`](evidence-healthcheck-post.txt)。

## 6. 基线与终验对比

| 组 | 基线（HEAD e48d4ec，`--code-ref HEAD`） | 终验（ce5f63b） |
|---|---|---|
| A | FAIL（5 条 review API 全 404） | **PASS 11/11** |
| B | FAIL（无自测/三步/月历入口/悬浮窗；笔记平铺表格） | **PASS 20/20** |
| C | PASS（原有功能正常） | **PASS 4/4** |
| D | PASS（sha 不变/零 AI） | **PASS 2/2** |

## 7. 残留风险与边界（如实说明）

1. **月历入口的沙箱数据种子**：沙箱真实 `deck_plans` 为空，门禁按「沙箱可写」约定先 `POST /api/deck-enroll` 给 doc46 录入复习计划（plan_id=16，只写沙箱 DB），再验证日清单「开始复习」按钮 → 生产环境由用户「录入遗忘曲线」后同样出现；**真实 8756 未写入**（Lead 已只读检查）。
2. **自测随机性**：每次自测随机抽 11/22；门禁验证了数量、挖空/答案、全对与错一空两种路径，未覆盖历史 v1 合并文件迁移的 UI 侧（test_23 已覆盖后端 legacy 自洽）。
3. **三步名词来源**：terms 来自 doc50 预习第6章（11 条）；无预习产物时实现有「可直接完成复习」降级（test_23 覆盖）。
4. 覆盖范围：doc46（22 KP）+ 真实 notes 24 篇 + 9 份课件列表；其它课件/多章/空库场景由 test_23/24 与既有回归覆盖。
5. 全量 1 条跳过为真实数据在跑所致（设计内）；未碰真实 8756/logs/notes 的写操作。

## 8. 证据索引

| 证据 | 路径 |
|---|---|
| 门禁结果（A–D 逐项 + 证据 + 截图清单） | [`gate-review-result.json`](gate-review-result.json) |
| 改版前基线 | [`baseline.json`](baseline.json) |
| 判据/契约 | [`PROTOCOL.md`](PROTOCOL.md) |
| 截图（POST 4 张 + result；基线 BASE-*） | [`shots/`](shots/) |
| 专项/全量/健康检查原始输出 | [`evidence-*.txt`](.) |
| 工具 | [`gate_review.py`](gate_review.py)、[`cdp_review.mjs`](cdp_review.mjs) |
