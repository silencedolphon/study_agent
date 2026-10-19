# M0 回退终验报告（task-17 · 独立 QA）

> 终验时间：2026-10-19 18:33–18:37 · 执行者：独立验收（QA，不写产品代码）
> 被测：回退提交 **`25e530c revert(M0): 按用户要求回退 M0 功能型新增（保留排版/文案/移动端/掌握度修复）`**
> 数据：已从 M0 前备份恢复（真实看板 `/api/kps=0`、`/api/mistakes=0`、`/api/queue=0`、
> `stats.knowledge.total=0`、**notes 保留 19 篇**）；8756 已用回退代码重启（QA 观测 PID 28052）。
> 工具：`_work/rollback/gate_rb.py` + `cdp_rb.mjs`（基线见 [`gate_rb-baseline.json`](gate_rb-baseline.json)）

## 0. 结论

| 验证组 | M0 代码基线 | 回退后终验 | 判定 |
|---|---|---|---|
| 功能已撤（HTTP + DOM） | **FAIL** | **PASS** | ✅ 撤销确认 |
| 排版保留（文案/空态/摘要/移动端/掌握度） | PASS | **PASS** | ✅ 保留确认 |
| 检索回基线（search.py == `251886f`） | **FAIL** | **PASS** | ✅ 回退确认 |
| **回退门禁（3 组）** | **FAIL 1/3** | **PASS 3/3** | ✅ **通过** |

配套：全量 `tests/run_all.py` **83 条 · 82 通过 · 0 失败 · 1 跳过 · 退出码 0**（矩阵 70/70）；
`tools/healthcheck.py` **PASS 9 / FAIL 0 / SKIP 0**。无阻塞项。

## 1. 被测代码与数据指纹

| 项 | 值 |
|---|---|
| git HEAD | `25e530c`（回退提交；工作区无产品/测试未提交改动，仅有用户数据的未跟踪 notes 文件） |
| `tools/dashboard.py` sha256 | `006946244de5b3b2…`（= HEAD；与 `251886f` 不同 → 保留了排版/移动端/掌握度修复，符合预期） |
| `core/search.py` sha256 | `637fae0824770a11…`（= HEAD = **`251886f` 基线**） |
| `core/study.py` sha256 | `60f5d31bed98b171…`（= HEAD = `251886f`，未回退会话/调度逻辑） |
| 实例 | `_work/eval/instances/qa-rb2`（8919，工作区代码快照；已 down）；按 handoff 建议只读复制真实 `logs/knowledge`（16）与 `logs/outline`（14）进沙箱以保真"有产物"场景 |
| 真实数据核对 | 只读调用 8756 API：kps=0 / mistakes=0 / queue=0 / stats.knowledge=0 / notes=19（笔记保留 ✓） |

## 2. 功能已撤（HTTP + DOM）→ PASS

| 子检查 | 基线（M0） | 回退后 |
|---|---|---|
| `POST /api/mistake` | 200 | **404** ✓ |
| `GET /api/kp-import/preview` | 200 | **404** ✓ |
| `POST /api/kp-import/apply` | 200 | **404** ✓ |
| 评分 2 分自动记错题 | 错题 3→4 | **数量不变**（不自动记）✓ |
| 知识点页「＋新建知识点」 | 存在 | **不存在** ✓ |
| 知识点页「导入已整理」 | 存在 | **不存在** ✓ |
| 错题页「＋记一道错题」 | 存在 | **不存在** ✓ |
| 顶栏场景标识 `#scene-badge` | 存在（场景 A · 日常） | **不存在** ✓ |
| 设置弹窗场景项 `.scene-opt` | 3 个 | **0 个** ✓ |

## 3. 排版保留 → PASS

| 子检查 | 结果 |
|---|---|
| 8 视图可见文本开发者术语（`logs/`、`tools/`、`卡 C-`、`doc_id`、`.json`） | **0 命中** ✓ |
| 空态 CTA 仍在 / 无死链（错题空态允许无按钮） | ✓（含"去整理这份课件""去「知识点」写笔记"等真实按钮，无失效指引） |
| 知识点摘要截断（`.kpsum` 2 行 `-webkit-line-clamp`） | ✓（长摘要 202 字渲染为 2 行裁切） |
| 掌握度四处一致（/api/kps、/api/kp、/api/graph、/api/stats，差 ≤0.01） | ✓ diff=0 |
| 390×844 八视图无横向滚动 + 首屏 scrollY=0 | ✓（8/8 视图全过，资料页 scrollWidth=390=clientWidth） |

## 4. 检索回基线 → PASS（并记录召回水平）

- `core/search.py` sha256 = `637fae0824770a114cdd6da4be2d873759fbe10c40696db7ad8dac19edc0b1b1`，
  与 `git show 251886f:core/search.py` **完全一致** ✓（M0-7 规则级修复已回退）。
- 10 题回归（只记录、不判定）：**4/10**（回落到 M0 前基线水平；M0 期间的 10/10 已随回退消失）。
  → 评测报告 P0-B 的**原有召回/排序问题仍在**（分类题 p.5、机制题 p.16、对比题 p.18 等不再被规则命中），
  属本次回退的预期代价，若后续想保留检索修复需单独立项（不依赖 M0 功能型改动）。

## 5. 全量回归与健康检查

| 门禁 | 命令 | 结果 |
|---|---|---|
| 全量回归 | `venv\Scripts\python.exe -X utf8 tests\run_all.py` | **Ran 83 tests · OK（skipped=1）· 退出码 0**；82 通过 / 0 失败 / 1 跳过；功能矩阵 70/70 覆盖；历史缺陷 6 个全转正 |
| 跳过说明 | `test_01_health.test_real_data_untouched` | 真实看板 8756 在跑且被用户使用，运行期间真实数据指纹变化 → 该用例按设计跳过并说明（非产品问题） |
| 健康检查 | `venv\Scripts\python.exe -X utf8 tools\healthcheck.py` | **PASS 9 / FAIL 0 / SKIP 0**（看板 8756 OK） |

原始输出：[`evidence-run_all-post.txt`](evidence-run_all-post.txt)、
[`evidence-healthcheck-post.txt`](evidence-healthcheck-post.txt)、
[`evidence-gate_rb-post-console.txt`](evidence-gate_rb-post-console.txt)。

## 6. 残留风险与边界（如实说明）

1. **数据已按要求清空**：知识点/错题/复习记录清零（`kps=0 / mistakes=0 / queue=0`），
   这是本次回退的**预期结果**；**笔记 19 篇已保留**（真实看板核对）。若用户需要找回旧 KP/错题，
   只能从 M0 期间备份另行导入，且无 UI 入口（导入入口已撤）。
2. **检索能力回落到 M0 前水平**：10 题回归 4/10；原 P0-B 召回问题仍在，属预期。
3. **M0 功能型入口全部撤销**：新建知识点、记错题、场景预设、导入已整理产物均无 UI；
   日常"建卡→复习→错题"闭环回到 M0 前状态（需靠 API/CLI 或后续新方案）。
4. **保留项已确认**：新排版/文案（术语 0 命中、空态 CTA、摘要截断）、移动端 390 修复、掌握度口径修复。
5. **验证边界**：门禁在隔离实例（8919）上验证；真实 8756 仅做只读 API 数据核对（未在真实看板做点击操作，
   避免污染用户环境）；未重复做三角色复测（非本任务范围）。
6. 8756 进程由 Lead/用户重启（QA 观测 PID 28052，非 39904），QA 未操作真实服务。

## 7. 证据索引

| 证据 | 路径 |
|---|---|
| 回退门禁结果（本次） | [`gate_rb-result.json`](gate_rb-result.json) |
| 回退门禁基线（M0 代码） | [`gate_rb-baseline.json`](gate_rb-baseline.json) |
| 门禁/回归/健康检查原始输出 | [`evidence-gate_rb-post-console.txt`](evidence-gate_rb-post-console.txt)、[`evidence-run_all-post.txt`](evidence-run_all-post.txt)、[`evidence-healthcheck-post.txt`](evidence-healthcheck-post.txt) |
| 浏览器截图（20 个：知识点/错题/设置 + 8 桌面视图 + 8 移动视图 + result.json） | [`rb-shots/POST-*`](rb-shots/) |
| 门禁工具 | [`gate_rb.py`](gate_rb.py)、[`cdp_rb.mjs`](cdp_rb.mjs) |
| 回退基线控制台（对照） | [`evidence-gate_rb-baseline-console.txt`](evidence-gate_rb-baseline-console.txt) |
