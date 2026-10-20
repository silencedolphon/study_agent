# 设置中心改版 · 独立门禁终验报告（task-22 第二段）

> 终验时间：2026-10-20 · 执行者：独立验收（QA，不写产品代码）
> 被测：**`3e6abb0 feat(settings): 按现有功能改版设置中心（仅前端 +376/−82）`**（HEAD；工作区干净）
> 基线：[`baseline.json`](baseline.json)（改版前，2026-10-20）· 判据：[`PROTOCOL.md`](PROTOCOL.md)
> 工具：`gate_settings.py`（API 契约 + 编排）+ `cdp_settings.mjs`（浏览器 DOM/交互）

## 0. 结论

| 验证组 | 改版前基线 | 改版后终验 | 判定 |
|---|---|---|---|
| 功能保留（5 管线 / 字段 / 保存 / 重置 / 密钥 / 提示词 / 切页确认） | PASS 13/13 | **PASS 14/14**（基线 13 项 + 新增「42 字段 key 与基线一致」对照） | ✅ 功能不丢 |
| 新版排版（分组 / 标题 / 入库方式 / 折叠标注 / 搜索 / 未保存计数 / 390） | FAIL 2/8 | **PASS 8/8** | ✅ 改版达标 |
| 全量回归 + 健康检查 | — | **107/107 · 矩阵 74/74 · 退出码 0；healthcheck PASS 9/0** | ✅ |
| 原有功能抽检 | — | 设置保存接口行为与基线一致；总览/知识点页 **0 个 JS 异常** | ✅ |

**设置中心改版通过：功能零丢失、后端契约零改动、新排版 8/8 达标、全量回归全绿。**

## 1. 代码指纹与改动范围

| 项 | 值 |
|---|---|
| git HEAD | `3e6abb0`（工作区无产品/测试未提交改动，仅有用户数据未跟踪 notes） |
| `tools/dashboard.py` sha256 | `ef0b56cadaf4f6ce…`（= HEAD；相对改版前 `e939fcfb…` 变化 → 前端改版生效） |
| `core/config.py` sha256 | `d9006ae7685552bc…`（= HEAD = 改版前，**后端零改动**，与 Lead 说明一致） |
| 实例 | `qa-settings-b`（8923，settings 只落沙箱；已 down、端口释放） |

## 2. 新版排版 8/8（基线 → 终验）

| # | 子检查 | 基线 | 终验 | 证据 |
|---|---|---|---|---|
| L1 | 导航分组存在 | FAIL | **PASS** | 左栏「搜索 + 两组导航（AI 能力 / 资料）」；`.sn-group` 分组标签出现 |
| L2 | 标题管线数正确（5 条）且不再「四条」 | FAIL | **PASS** | 标题「⚙ 设置中心 · 5 条管线」；全文无「四条管线」 |
| L3 | 入库页「入库方式」卡片 | FAIL | **PASS** | 入库页首卡「入库方式」+ 主开关徽标（整页切块/旧模式） |
| L4 | 模式横幅随整页切块开关显示 | PASS | **PASS** | 横幅「当前：整页切块 · 每页一块；有图页识图文字并入同一块；不跑 AI 结构分析、不建独立识图块」 |
| L5 | 旧模式（AI 结构分析）折叠且标注 | FAIL | **PASS** | 「AI 结构分析（旧模式专用）」卡片默认折叠，标注「仅当整页切块关闭时生效 · 当前不生效」 |
| L6 | 设置搜索框过滤/恢复 | FAIL | **PASS** | `input.set-search#set-q`（名称/键/说明）；输入「模型」隐藏不匹配行并显示「命中 N 项」，清空恢复 |
| L7 | 编辑后未保存计数 | FAIL | **PASS** | 底部「N 项未保存」实时计数（无改动时「无未保存改动」） |
| L8 | 390×844 无横向溢出、保存可点 | PASS | **PASS** | `scrollWidth=390=clientWidth`；保存按钮可见可点；导航横向滚动（预期设计） |

截图：[`shots/POST-settings-main.png`](shots/POST-settings-main.png)、[`POST-settings-ingest.png`](shots/POST-settings-ingest.png)、[`POST-settings-search.png`](shots/POST-settings-search.png)、[`POST-settings-390.png`](shots/POST-settings-390.png)。

## 3. 功能保留 14/14（基线 13 项全保 + 字段 key 对照）

| 子检查 | 结果 |
|---|---|
| `GET /api/settings` 200 且 5 管线齐全（chat/kp/ingest/deck/preview） | ✓ |
| 每页字段数量与基线一致 **7/7/12/9/7** | ✓ |
| **42 个字段 key 清单与基线完全一致（改版未增删字段）** | ✓（基线新增对照项） |
| 保存语义：`POST /api/settings {values}` 部分更新只改指定 key，其余字段不动 | ✓ |
| 密钥留空＝不改（保存空串不清除/覆盖已配密钥） | ✓ |
| 本区恢复默认：`POST /api/settings/reset` 恢复默认值 | ✓ |
| 提示词槽位元数据一致（kp/struct/terms 3 个可编辑 + 内置只读） | ✓ |
| 设置中心可打开（`#setmodal.on` + 左栏导航） | ✓ |
| 5 条管线 tab 均可打开、字段数一致 | ✓ |
| 切页有未保存改动确认（确认框出现，不静默丢失） | ✓ |
| 提示词查看弹窗可打开 | ✓ |
| UI 保存 bool 生效且可改回（`/api/settings` 值同步） | ✓ |
| 「本区恢复默认」按钮存在且可点 | ✓ |
| 密钥字段存在且「留空＝不改」提示语义可见（credential 就绪信息） | ✓ |

## 4. 全量回归与健康检查

| 门禁 | 命令 | 结果 |
|---|---|---|
| 全量回归 | `venv\Scripts\python.exe -X utf8 tests\run_all.py` | **Ran 107 tests · OK · 退出码 0**；107 条 / 107 通过 / 0 失败 / **0 跳过**；功能矩阵 **74/74** 覆盖；历史缺陷 7 个全转正（含改版新增 `test_16`） |
| 健康检查 | `venv\Scripts\python.exe -X utf8 tools\healthcheck.py` | **PASS 9 / FAIL 0 / SKIP 0** |

原始输出：[`evidence-post-console.txt`](evidence-post-console.txt)（设置门禁）、[`evidence-run_all-post.txt`](evidence-run_all-post.txt)、[`evidence-healthcheck-post.txt`](evidence-healthcheck-post.txt)。

## 5. 原有功能抽检（不回归）

- **设置保存接口行为与基线一致**：`/api/settings` GET 结构、POST 部分更新、reset 语义、密钥留空语义均与基线实测一致（见 §3）。
- **其它页面无 JS 异常**：总览（overview）与知识点（kps）各抽 1 页，CDP 记录 **新增 JS 异常 0 个**、全局 `errors=[]`；截图 [`POST-page-overview.png`](shots/POST-page-overview.png)、[`POST-page-kps.png`](shots/POST-page-kps.png)。
- 设置流程全程未出现 `Runtime.exceptionThrown`。

## 6. 残留风险与边界（如实说明）

1. **仅前端改动**：`core/config.py` sha256 与改版前一致（后端零改动），保存/重置/密钥语义不变；改版风险集中在前端渲染与交互。
2. **搜索为前端过滤**：按「名称 / 键 / 说明」过滤当前页面的设置行（隐藏 DOM 不删除，`setCollect` 仍能读到被隐藏字段的改动，保存不受影响）；不是后端检索，也不跨页搜索字段值。
3. **未保存计数只统计当前页（pane）**，切页/清空以确认框保护；属改版设计，符合「切页确认」语义。
4. **390 布局**：左栏导航变为横向滚动条（预期设计），搜索框收窄；已确认无横向溢出、保存按钮可点，但未覆盖 320 宽等更极端档位。
5. **验证在隔离实例**（settings 只写沙箱）；真实 8756 未由 QA 重启/实操，待 Lead 统一线上只读验证。
6. 本次 0 次 AI 调用；未改真实 `config.local.json`、未碰 8756/3080；实例已 down、8923 已释放。

## 7. 证据索引

| 证据 | 路径 |
|---|---|
| 门禁结果（功能保留 14 项 + 排版 8 项 + 截图清单 + pages 抽检） | [`gate-settings-result.json`](gate-settings-result.json) |
| 改版前基线（对比用） | [`baseline.json`](baseline.json) |
| 判据与「必须保留」清单 | [`PROTOCOL.md`](PROTOCOL.md) |
| 截图（POST：设置主界面/入库页/搜索/390 + 总览/知识点抽检） | [`shots/POST-*`](shots/) |
| 命令原始输出 | [`evidence-post-console.txt`](evidence-post-console.txt)、[`evidence-run_all-post.txt`](evidence-run_all-post.txt)、[`evidence-healthcheck-post.txt`](evidence-healthcheck-post.txt) |
| 工具 | [`gate_settings.py`](gate_settings.py)、[`cdp_settings.mjs`](cdp_settings.mjs) |
