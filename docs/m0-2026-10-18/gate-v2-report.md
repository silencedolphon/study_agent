# M0 门禁 v2 终测报告（task-14 第二段 · 独立 QA）

> 终测时间：2026-10-18 23:41–23:56 · 执行者：独立验收（QA，不写产品代码）
> 被测：M0 剩余改动合并后 **`f18e977`（waveB：M0-6b 导入入口 + M0-8 移动端）**，
> 前置 **`1674ede`（waveA：文案/空态 CTA + 场景骨架 + 导入内核）**；HEAD 即 `f18e977`。
> 工具：`gate_v2.py` + `cdp_v2.mjs`（协议见 [`PROTOCOL.md`](PROTOCOL.md) §11）

## 0. 结论（一句话）

**v2 门禁 3/4 PASS（M0-1 / M0-6b / M0-8 通过；M0-6 仍 FAIL）**；
**v1 门禁 4/4 PASS（M0-2/4/5/7 无回归）**；全量回归 **107/107、退出码 0**；healthcheck **PASS 9/0**。
唯一未过项是**真实残留缺陷**：知识点页在有整理产物时仍可见两处开发者术语
（`.json` 文件名与 `卡 C-` 卡号）——不是门禁误报，最小复现见 §5。

## 1. 代码指纹（确认测的是合并后代码）

| 项 | 值 |
|---|---|
| git HEAD | `f18e977`（waveB）；`1674ede`（waveA）均已在历史中 |
| `tools/dashboard.py` sha256 | `58cd58c5c8e4017a99190857950054ff6d6a9c311ad48745791bb03dfaee8119`（实例 = 本地 = `git show f18e977:tools/dashboard.py`） |
| `core/search.py` sha256 | `63f047e4be9d09d6957e0accac997587e457a23d1f82973ccb5b3c09ad5e8788`（三方一致） |
| 工作区 vs `f18e977`（tools/core/tests） | `git diff --stat` → 空（未改产品/测试代码） |
| 实例 | `_work/eval/instances/qa-gate`（端口 8914，已 down）；按 handoff 卡建议额外只读复制真实 `logs/knowledge`（16 个）+ `logs/outline`（14 个）进实例沙箱，提升"有整理产物"场景的保真度 |

## 2. v2 门禁逐项（baseline → post）

| 项 | v2 基线（wave1 代码） | 终测（f18e977） | 判定 |
|---|---|---|---|
| M0-1 场景预设 A/B/C | FAIL：设置里无场景控件 | **PASS**：设置中心三选一 `A · 日常 / B · 备考 / C · 冲刺` + 顶栏 `#scene-badge`；切换后导航变化、刷新保持、切回 A 恢复 | ✅ |
| M0-6 文案/空态/摘要 | FAIL：kps/docs 术语、3 视图空态无 CTA、摘要未截断 | **FAIL（仅剩 1 条子项）**：空态 CTA ✓、summary 2 行截断 ✓、**kps 页仍见 `.json` / `卡 C-`** | ❌ |
| M0-6b 导入入口 | FAIL：按钮不存在、API 全 404 | **PASS**：`＋ 导入已整理好的知识点（0 费用）` 按钮存在；`GET /api/kp-import/preview` 200、`POST /api/kp-import/apply` 200（实例内真实导入 262 条，隔离库） | ✅ |
| M0-8 移动端 390 | FAIL：docs 横向 788>390、assistant scrollY=396 | **PASS**：8 视图 `scrollWidth<=clientWidth+2`（docs=390/390）、资料页不横滑、8 视图首屏 `scrollY=0` | ✅ |

## 3. v1 门禁（M0-2/4/5/7 不回归）→ 4/4 PASS

| 项 | 结果 | 关键证据 |
|---|---|---|
| M0-2 建点→列表→queue(new)→评分→history | PASS | 5/5 子检查 ✓ |
| M0-4 手动错题 + 评分 0–2 自动错题 + 间隔归零 | PASS | 6/6 子检查 ✓（数量 +1、`resolved=0`、`interval_days=0`） |
| M0-5 掌握度四口径一致 | PASS | 四处均 0.13，diff=0 |
| M0-7 检索回归 | PASS | 10/10 + 三条硬规则全过（p.5 / Top-1 p.16 / p.18） |

> 工具修正（非产品问题）：v1 门禁的队列检查原来固定 `limit=100`，在"实例内已导入 262 个新知识点"的
> 大库下，新建知识点可能排到第 100 条之外导致误判；已把 `gate_check.py` 该处改为 `limit=5000` 并复跑通过。

## 4. 配套门禁

| 门禁 | 命令 | 结果 |
|---|---|---|
| 全量回归 | `venv\Scripts\python.exe -X utf8 tests\run_all.py` | **107/107 通过 · 退出码 0**；功能矩阵 **75/75** 覆盖；未覆盖 10 项不变；历史缺陷 8 个全转正（含本批 BUG-7 文案/BUG-8 移动端） |
| 健康检查 | `venv\Scripts\python.exe -X utf8 tools\healthcheck.py` | **PASS 9 / FAIL 0 / SKIP 0** |
| v2 门禁 | `gate_v2.py --label post` | **3/4 PASS**（唯一未过 M0-6，见 §5） |
| v1 门禁 | `gate_check.py --label post` | **4/4 PASS** |

原始输出：[`evidence/gate-v2-post-console.txt`](evidence/gate-v2-post-console.txt)、
[`evidence/gate-v1-post-console.txt`](evidence/gate-v1-post-console.txt)、
[`evidence/run_all-v2.txt`](evidence/run_all-v2.txt)、[`evidence/healthcheck-v2.txt`](evidence/healthcheck-v2.txt)。
机读结果：[`gate_v2-result.json`](gate_v2-result.json)、[`gate-result.json`](gate-result.json)、
[`gate_v2-baseline.json`](gate_v2-baseline.json)（基线对比）。

## 5. 唯一未过项：M0-6 开发者术语残留（真实缺陷，非误报）

- **现象**：知识点页（8 视图之一 `kps`）在"有整理产物"时，批量整理/产物状态卡可见文本含：
  `数据源 40-末级知识点总结.json · 产出卡 C-0058` → 命中开发者术语 **`.json`** 与 **`卡 C-`**。
- **为什么基线与终测前半段没抓到**：无产物时该卡不渲染这些字段（显示"未整理"），
  装上真实 `logs/knowledge` 产物后才出现；**真实看板有产物（16 份），真实用户会看到**。
- **渲染位置**：`tools/dashboard.py` 第 4246–4250 行（`lkpLine`：`数据源 ${lkp_source}` 与
  `产出卡 ${lkp_source_card}`）；同类字段在 4845 行（思维导图节点信息）也可能出现。
- **验收口径**：计划 M0-6 要求"全站用户可见文本清 `logs/`、`tools/`、`卡 C-`、`doc_id`、`.json`；
  grep 命中 0"。当前 kps 视图命中 2 类 → 该项不通过。
- **门禁证据**：`gate_v2-result.json::browser.m0_6.evidence.views.kps`
  （`terms: [".json","卡 C-"]` + `termCtx` 原句）；截图 `v2-shots/POST-06-kps.png`。
- **最小复现**（隔离实例，不碰真实库）：
  ```powershell
  cd D:\study_agent
  # 1) 建最新代码的隔离实例，并把真实整理产物只读复制进沙箱（真实看板本来就有）
  venv\Scripts\python.exe -X utf8 _work\eval\evalkit.py up --name qa-gate --port 8914
  New-Item -ItemType Directory -Force _work\eval\instances\qa-gate\sandbox\logs\knowledge | Out-Null
  Copy-Item logs\knowledge\* _work\eval\instances\qa-gate\sandbox\logs\knowledge\
  # 2) 浏览器打开 知识点 页：批量整理卡片会显示 “数据源 …-末级知识点总结.json · 产出卡 C-0058”
  #    或直接跑门禁，M0-6 子项会打印 terms/termCtx：
  venv\Scripts\python.exe -X utf8 _work\m0\gate\gate_v2.py --base http://127.0.0.1:8914 --label post
  # 3) 回收
  venv\Scripts\python.exe -X utf8 _work\eval\evalkit.py down --name qa-gate
  ```
- **修复建议（由施工方决定，QA 不改产品代码）**：这两处面向学生的产物状态展示应去掉
  `lkp_source`（文件名）与 `lkp_source_card`（卡号），或收进"高级/诊断"折叠并对学生隐藏；
  清完重跑 `gate_v2.py` 应 4/4。

## 6. 残留风险与边界（如实说明）

1. M0-6 只扫描 8 个主视图 `#view` 的可见文本，**不含**设置弹窗/详情弹窗；设置弹窗里仍保留了
   `config.local.json` 等实现说明（属设置/诊断语境，本门禁未计入门禁口径，是否算"用户可见术语"
   由计划 M0-6 的"全站"定义决定）。
2. M0-1 场景持久化按设计只存 `localStorage`：换浏览器/无痕模式会回默认 A，属预期，不是缺陷。
3. M0-8 只验证 390×844 单档 + 8 视图无横向滚动/首屏不自动滚；未覆盖 504 宽、点击区域 ≥40px 等
   M3-2 项。
4. M0-6b 的 `apply` 在门禁实例里真实导入了 262 条知识点（隔离库，用于验证 preview/apply 返回 200
   与幂等写入）；真实使用仍需用户点确认，未产生 AI 费用。
5. 真实看板 8756 / DSH 3080 全程未触碰；本次 0 次大模型调用；实例已 down、8914 已释放。

## 7. 证据索引

| 证据 | 路径 |
|---|---|
| v2 终测 JSON（含逐项子检查/M0-6 termCtx） | [`gate_v2-result.json`](gate_v2-result.json) |
| v2 基线 JSON | [`gate_v2-baseline.json`](gate_v2-baseline.json) |
| v1 门禁 JSON | [`gate-result.json`](gate-result.json) |
| 命令原始输出 | [`evidence/`](evidence/) |
| 浏览器截图（22 张：5 场景 + 8 桌面视图 + 8 移动视图 + result.json） | [`v2-shots/POST-*`](v2-shots/) |
| 门禁工具 | [`gate_v2.py`](gate_v2.py)、[`cdp_v2.mjs`](cdp_v2.mjs)、[`gate_check.py`](gate_check.py) |
| 协议 v2 判据 | [`PROTOCOL.md`](PROTOCOL.md) §11 |

---

## 8. v2b 独立复核（2026-10-18T23:53:33+0800，`816c9d8`）

> 触发：M0-6 残留修复提交 `816c9d8`（只改 `tools/dashboard.py::loadMindmapTree` 的 `lkpLine`：
> 隐藏 `数据源 <file>.json · 产出卡 C-xxxx`，详细来源保留进折叠区）。QA 独立重建实例复核。

- 被测代码：git HEAD `816c9d8`；`tools/dashboard.py` sha256
  `26494a46d24b964f99ce69c597f908f590f4c5b615553156c8d72cc5c9307986`（实例 / 本地 / `git show 816c9d8:` **三方一致**：
  是）；工作区 vs `816c9d8`（tools/core/tests）无差异。
- 复核方法（严格按真实场景）：删除旧实例 → `evalkit.py up --name qa-gate --port 8914` →
  **把真实 `logs/knowledge`（16 个）与 `logs/outline`（14 个）只读复制进实例沙箱**
  （不复制会因"无产物"导致 M0-6 假通过）→ `gate_v2.py --label post`。
- 结果：**4/4 PASS**
  - M0-1 场景：PASS（设置三选一 + 切换/刷新保持/切回恢复）
  - M0-6 文案：**PASS**（8 视图可见文本 `terms` 全为空，kps 页 `卡 C-`/`.json` 残留已消失；
    空态 CTA、summary 两行截断均 PASS）
  - M0-6b 导入入口 + preview/apply API：PASS
  - M0-8 移动端 390：PASS（8 视图无横向溢出、首屏 scrollY=0）
- 证据：`gate_v2b-result.json`（本次 v2 结果存档）、`evidence/gate-v2b-post-console.txt`、
  `v2-shots/POST-*`（本次运行的 22 个文件，含 5 场景 + 8 桌面视图 + 8 移动视图 + result.json）。
- 结论：**M0-6 残留解除，v2 门禁 4/4 通过**；v1 门禁（M0-2/4/5/7）与本轮代码无交集，沿用
  上一轮 4/4 结论（Lead 侧全量 107/107 亦通过）。

