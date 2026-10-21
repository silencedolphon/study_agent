# 教材补充 AI 化 · 独立门禁终验报告（task-31 第二段）

> 终验时间：2026-10-22 · 执行者：独立验收（QA，不写产品代码）
> 被测：**`cb43b09 feat(teach): 教材补充升级混合 AI 管线（AI 课件→章 + 本地小节定位 + 按需 AI 分析）`**
> （HEAD，工作区干净）· 基线：[`baseline.json`](baseline.json) · 判据：[`PROTOCOL.md`](PROTOCOL.md)
> 工具：`gate_teach_ai.py`（两阶段：AI 关闭做 build/校验 → AI 开启做只读 UI）+ `cdp_teach_ai.mjs`（CDP 四状态/横幅/旧版/390/原有功能）
> **零真实 AI 调用**：build/参数校验阶段 `KB_TEACH_AI=0`（本地兜底）；UI 阶段只读 + 确认框取消；全程未发起任何模型请求。

## 0. 结论

| 验证组 | 改版前基线（HEAD 6f30e90） | 合并后终验（cb43b09） | 判定 |
|---|---|---|---|
| A 后端 AI 契约（章/小节/ai/estimate/build 校验/旧版/提示词） | FAIL | **PASS（6/6）** | ✅ |
| B 前端 AI 卡四状态 + 就绪行 + 旧版提示 + 390 + 原有功能 | FAIL | **PASS（12/12）** | ✅ |
| C 导图横幅 AI 文案/成本 | FAIL | **PASS（1/1）** | ✅ |
| D 零 AI / 不动原产物 | PASS（基线安全项） | **PASS（4/4）** | ✅ |
| 专项测试 test_21 / test_22 | — | **11 OK / 12 OK** | ✅ |
| 全量回归 + 健康检查 | — | **168 条·167 通过·0 失败·1 设计内跳过·退出码 0；矩阵 79/79；healthcheck PASS 9/0** | ✅ |

**教材补充 AI 化通过：AI 章映射/本地小节/按需 AI 卡/成本预估/旧版升级/零成本路径全部达标，原知识点与原有功能无回归。**

## 1. 代码指纹与范围

| 项 | 值 |
|---|---|
| git HEAD | `cb43b09`（工作区干净，仅用户数据未跟踪 notes） |
| `tools/dashboard.py` sha256 | `144e7def1bcdcfe9…`（= HEAD） |
| `core/teach.py` sha256 | `f94b8cab3b63ae6d…`（= HEAD；AI MAP/SUPP/estimate/旧版升级） |
| `core/config.py` sha256 | `bb0c2935ed3cf552…`（= HEAD） |
| 实例 | `qa-teach-ai-g:8932`（独立命名；已 down、端口释放） |
| 提示词真源 | `logs/knowledge/提示词-教材补充.md`（6,398 B，**4 个代码块**：MAP-SYS/MAP-USER/SUPP-SYS/SUPP-USER；9 个占位符含 4 核心） |

## 2. A 后端 → PASS（6/6）

| 子检查 | 证据 |
|---|---|
| `POST /api/textbook-supplement/build`（阶段 1） | 200、`status=done`、1.61s；meta `version=C-0125-teach-ai-v1`、**`ai_map_source=local_fallback`、`ai_map_cost_cny=0.0`**（KB_TEACH_AI=0，0 模型调用） |
| `/api/textbook-supplement/kp` | 200、`available=true`；返回 `chapter`（第6章 作用于胆碱受体药）、`sections`（3 小节）、`ai`、`estimate`（含 calls/cost/tokens/model） |
| AI 结果 schema | `verdict/summary/points[{kind,text,pages}]/gaps/citations[{pages,quote}]` 齐全、页越界校验语义保留 |
| `GET /api/textbook-supplement/kp/estimate` | 200，`{estimate:{calls:1, cost_cny_max:0.0644, model:deepseek-flash, est_tokens_in:8296, est_tokens_out:13044, ai_enabled:false…}}` |
| `POST /api/textbook-supplement/kp/build` 参数校验 | 未知 KP → `{ok:false,status:no_kp,error:…}`（1.29s，`ai_enabled:false`）；无产物 doc → `no_supplement_file`（0.06s）；均 0 模型调用、不写其它产物 |
| 旧版 C-0124 产物 | 写入旧版后 `GET status` → `legacy=true`、`stale=true`（supplement 内）、`needs_backfill=true` |
| 提示词 4 块 | 4 个 `PROMPT-TEACH-*-BEGIN` 块可加载；4 个核心占位符（`{courseware_title}/{kp_name}/{chapter}/{section_material}`）齐全 |

## 3. B 前端 AI 卡 → PASS（12/12）

| 状态 | 证据 |
|---|---|
| **已生成** | `AI 已生成` 徽标、`has_supplement` verdict、summary、π补充点（kind/text/pages）、`课件没讲 / 没讲清` gaps、`教材出处（2 条，点开看原文）` citations、`模型 deepseek-flash · 费用 ¥0.0031 · 耗时 3.5s`、`重新生成` 按钮 |
| **未生成** | `未生成` 徽标 + `预计 1 次调用 · 成本上界 ≤¥0.0644 · 模型 deepseek-flash` + `生成 AI 补充`；点击出现确认框「生成 AI 教材补充？预计 1 次调用｜成本上界 ≤¥0.0644｜模型…确认后才调用外接大模型」→ **点「取消」，未执行** |
| **no_supplement** | `AI：无需补充` 徽标 +「AI 判断教材对应小节没有需要补充的内容」+ `no_supplement` verdict |
| **failed / 缺 key** | `生成失败` 徽标 + 错误说明 `模型不可用（QA 假产物）` + **「本地「教材原文（参考）」仍可在下方查看」**（`.mteach-local/.mteach-ps` 4 项）+ `重试生成` 按钮 |
| 就绪行 | 「教材补充已就绪 · 章节：第6章 作用于胆碱受体药 · 本地小节：3 个 · 可逐条生成 AI 补充（AI 定位 · 映射成本 ¥0.0009）」 |
| 旧版提示 | `⚠ 旧版教材补充（纯本地匹配，无 AI 章节映射）` + `重新 AI 定位并补做` 按钮（只读未点） |
| 390×844 | 教材补充（含 AI）抽屉可展开、`scrollWidth=390=clientWidth` 无横向溢出 |
| 原有功能 5 项 | 小窗左侧导图（`.mmkp-side`+高亮）、教材页图（`/api/teach-page` img）、笔记计数（doc46 作用位点及药理作用=1）、设置中心（5 管线/分组/搜索）、预习页签——全部正常 |
| JS 异常 | `errors=[]`（无 `Runtime.exceptionThrown`） |

截图：[`shots/POST-01-ai-done.png`](shots/POST-01-ai-done.png)、[`POST-02-ungenerated-cancel.png`](shots/POST-02-ungenerated-cancel.png)、[`POST-03-mobile.png`](shots/POST-03-mobile.png)、[`POST-04-ready.png`](shots/POST-04-ready.png)、[`POST-05-banner-ai.png`](shots/POST-05-banner-ai.png)、[`POST-06-legacy.png`](shots/POST-06-legacy.png)。

## 4. C 导图横幅 → PASS（1/1）

- needs_backfill 场景横幅：**「检测到教材《药理学 第6版 刘晓东》已入库，可先 AI 定位本章并补做教材补充（≤¥0.0178，不改动原知识点）」** + 按钮「AI 定位章节并补做」；探针只读未点执行；
- 已有产物：就绪行「章节：第6章 … · 本地小节：3 个 · 可逐条生成 AI 补充（映射成本 ¥0.0009）」。

## 5. D 零 AI / 不动原产物 → PASS（4/4）

- **零真实 AI 调用**：阶段 1 `KB_TEACH_AI=0` → build meta `ai_map_source=local_fallback / ai_map_cost_cny=0.0`；kp/build 无效参数响应 `ai_enabled:false` 且 1.29s/0.06s（无模型）；阶段 2 只读 + 生成确认框取消；
- 无效 kp/build 调用 0 AI、不写其它产物（允许本地 rebuild 更新 `46-教材补充.json` 及其 `.bak`）；
- 原知识点/章节细目 sha256 全流程不变（`46-末级知识点总结.json`、`50-章节细目.json`）；
- knowledge 目录仅允许 `46-教材补充.json*` 变化。

## 6. 全量回归与健康检查

| 门禁 | 结果 |
|---|---|
| `tests/test_21_teach_ai.py` | **Ran 11 tests · OK** |
| `tests/test_22_teach_ai_ui.py` | **Ran 12 tests · OK** |
| `tests/run_all.py` | **Ran 168 tests · OK (skipped=1) · 退出码 0**；168 条 / 167 通过 / 0 失败 / 1 跳过；矩阵 **79/79** |
| 跳过说明 | `test_01_health.test_real_data_untouched`（真实 8756 使用中，设计内跳过） |
| `tools/healthcheck.py` | **PASS 9 / FAIL 0 / SKIP 0** |

原始输出：[`evidence-post-console.txt`](evidence-post-console.txt)、[`evidence-test_21.txt`](evidence-test_21.txt)、[`evidence-test_22.txt`](evidence-test_22.txt)、[`evidence-run_all-post.txt`](evidence-run_all-post.txt)、[`evidence-healthcheck-post.txt`](evidence-healthcheck-post.txt)。

## 7. 基线与终验对比

| 组 | 基线（HEAD `6f30e90`，`--code-ref HEAD`） | 终验（`cb43b09`） |
|---|---|---|
| A | FAIL（无估价接口、无 `/kp/build`、无 4 块提示词、无旧版识别；假 AI 字段仅被透传） | **PASS 6/6** |
| B | FAIL（无 AI 卡/生成按钮/兜底/旧版提示；390 与原有功能正常） | **PASS 12/12** |
| C | FAIL（旧纯本地横幅无 AI 文案） | **PASS 1/1** |
| D | PASS（零写盘基线项） | **PASS 4/4** |

## 8. 成本/时间估算核对

- 章映射（MAP，1 次/课件）：就绪行/横幅显示映射成本 **¥0.0009**（沙箱假产物值）；真实估算以 `kp/estimate` 的 `cost_cny_max` 为准（单 KP 上界 **≤¥0.0644**，tokens 8296/13044、模型 deepseek-flash、thinking/max）。
- 本次全程实际费用 **¥0**（未发起模型请求；`ai_map_cost_cny=0.0`，UI 只读+取消）。

## 9. 残留风险与边界（如实说明）

1. **真实 AI 效果未验证**：本次只验证管线/契约/UI 状态与零成本路径；真实模型返回质量、MAP 选章准确率、SUPP 补充内容质量需用户试用（Lead 已在 8756 只读检查 status legacy/estimate 零调用）。
2. **成本/耗时上界为估算值**：`cost_cny_max/tokens` 按本地 token 估算规则计算，未实测真实调用账目；正式使用前以实际记账为准（产物 `ai.cost_cny` 会记录真实费用）。
3. **沙箱保真处理**：doc50 的 `abs_path` 指向沙箱内教材副本（真实 8756 路径天然在 KB 根内）。
4. **假 AI 产物**：四状态通过注入假 artifact 验证 UI；合并实现真实字段已通过 test_21/test_22 与 API 结构校验。
5. **覆盖范围**：仅 doc46↔doc50 一对、KP-N03-1/N04-1/N07-1/N08-1 样例；其它课件/教材、多章切换、AI key 缺失/超时、旧版备份链未逐一覆盖。
6. 全量 1 条跳过为真实数据在跑所致（设计内）；未碰真实 8756/真实 logs 的补充产物（全部在沙箱）。

## 10. 证据索引

| 证据 | 路径 |
|---|---|
| 门禁结果（A–D 逐项 + 证据 + 截图清单） | [`gate-teach-ai-result.json`](gate-teach-ai-result.json) |
| 改版前基线 | [`baseline.json`](baseline.json) |
| 判据/契约/假 AI schema | [`PROTOCOL.md`](PROTOCOL.md) |
| 截图（POST 6 张 + 各模式 json；基线 BASE-*） | [`shots/POST-*`](shots/)、[`shots/BASE-*`](shots/) |
| 专项/全量/健康检查原始输出 | [`evidence-*.txt`](.) |
| 工具 | [`gate_teach_ai.py`](gate_teach_ai.py)、[`cdp_teach_ai.mjs`](cdp_teach_ai.mjs) |
