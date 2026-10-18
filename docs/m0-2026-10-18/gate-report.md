# M0 门禁复测报告（task-11 第二段 · 独立 QA）

> 复测时间：2026-10-18 23:12–23:15 · 执行者：独立验收（QA，不写产品代码）
> 被测：M0 wave1 合并提交 **`12ee155`**（HEAD `f46426b` 仅在其上加交接卡文档）
> 工具：`_work/m0/gate/gate_check.py`（协议见 [`PROTOCOL.md`](PROTOCOL.md)）

## 0. 结论

| 门禁项 | 基线（改前） | 复测（合并后） | 判定 |
|---|---|---|---|
| M0-2 新建知识点 → 复习闭环 | PASS | **PASS** | ✅ |
| M0-4 错题闭环（手动 + 评分 0–2 自动） | **FAIL** | **PASS** | ✅ 修复确认 |
| M0-5 掌握度四口径一致 | PASS* | **PASS** | ✅ |
| M0-7 检索回归（10 题） | **FAIL 4/10** | **PASS 10/10** | ✅ 修复确认 |
| **门禁（4 项全过）** | **FAIL 2/4** | **PASS 4/4** | ✅ **通过** |

配套门禁：`tests/run_all.py` **93/93 通过、退出码 0**；`tools/healthcheck.py` **PASS 9/0**；
浏览器 P0 路径复测全通（6 张关键截图见 [`shots/`](shots/)）；未发现阻塞项。

> \* 基线快照（22:47）时工作区里已有施工中的 M0-5 改动，故基线 M0-5 已 PASS；
> HEAD `251886f` 上的 M0-5 是 FAIL（评测报告 P0-C 已实证）。本次复测以合并后代码为准。

被测实例：`_work/eval/instances/qa-gate/sandbox`（端口 8914，`evalkit` 隔离实例；
真实主库快照 + 真实 raw 副本；不碰 8756/3080、不写真实数据、全程 0 次 AI 调用）。

## 1. 代码指纹（确认复测的是合并后代码）

| 项 | 值 |
|---|---|
| git HEAD | `f46426b`（`12ee155..f46426b` 只有 `docs/交接/2026-10-18-2312-M0wave1.md`，产品/测试零改动） |
| 实例 `tools/dashboard.py` sha256 | `ee09c9efc8403b4faa4f4857b7c41a1996cb9f7098cf23893a9b5e504f2b45de`（= `git show 12ee155:tools/dashboard.py` = 本地文件） |
| 实例 `core/search.py` sha256 | `63f047e4be9d09d6957e0accac997587e457a23d1f82973ccb5b3c09ad5e8788`（= `git show 12ee155:core/search.py` = 本地文件） |
| 工作区 vs `12ee155`（tools/core/tests） | `git diff --stat 12ee155 -- tools/ core/ tests/` → **空** |

> 注：`gate_check` 指纹里的“+ 工作区改动”来自一个未跟踪文件
> `notes/传出神经传输特点---知识点笔记.md`（真实看板上的用户/他人操作产生，**不属于本门禁**；
> 门禁全部写操作都在实例沙箱内）。产品/测试代码本身与 `12ee155` 完全一致。

## 2. 门禁逐项结果

### M0-2 新建知识点 → 列表 → 复习队列 → 评分闭环：**PASS**
- `POST /api/kp` 200 且返回 `kp_id`；`GET /api/kps` 立即可见；
- `GET /api/queue` 出现该点且 `status="new"`；`POST /api/review (grade=4)` 后 `reps=1`、`due_at>now`；
- `GET /api/kp?id=` 有 `last_review` 与复习历史。
- 证据：`baseline.json` / `gate-result.json` 中 M0-2 的 5 条子检查全部 ✓。

### M0-4 错题闭环：**FAIL → PASS**（本次修复核心）
| 子检查 | 基线 | 复测 |
|---|---|---|
| `POST /api/mistake` 200 | 404（路由不存在） | ✓ 200，返回 `mistake_id` |
| 错题落库、可按问题找回（数量 +1） | ✗ | ✓ 数量 5→6，问题文本一致 |
| 错题带 `kp_id`、`resolved=0` | ✗ | ✓ |
| 评分 `grade=2` 自动新增错题（关联该 KP） | ✗（错题数不变） | ✓ 数量 6→7，`kp_id` 匹配 |
| 自动错题 `resolved=0` | ✗ | ✓ |
| 自动记错后该 KP `interval_days=0`（间隔归零） | ✗（仍 1.0） | ✓ `interval_days=0.0` |

- 证据：`gate-result.json::checks[M0-4].evidence`（含手动/自动两条完整记录）。

### M0-5 掌握度四口径一致：**PASS**
- 同一 KP（评分 5 后）四处值：`list=0.13 / detail=0.13 / graph=0.13 / stats=0.13`，
  `max-min = 0.0 ≤ 0.01`；评分后列表不再是 0。
- 证据：`gate-result.json::checks[M0-5].evidence`。

### M0-7 检索回归（10 题 + 3 条硬规则）：**FAIL 4/10 → PASS 10/10**
- 命中率：**10/10**（基线 4/10）；三条硬规则全部通过：
  ① 分类题引用 **p.5**；② 机制题 Top-1 为 **p.16**；③ 对比题覆盖 **p.18**（并含巴比妥 p.29）。
- 逐题（基线 → 复测 Top-1）：

| 题号 | 问题 | 基线 | 复测 Top-1 |
|---|---|---|---|
| Q01 | 镇静催眠药分为哪几类？ | ✗ | ✅ p.5（chunk 1382） |
| Q02 | 苯二氮卓类包括哪些常用药物？ | ✗ | ✅ p.23（1400） |
| Q03 | 苯二氮卓类的作用机制是什么？ | ✗ | ✅ p.16（1393） |
| Q04 | 地西泮为什么能增强GABA的抑制作用？ | ✓ | ✅ p.16（1393） |
| Q05 | 苯二氮卓类和巴比妥类的作用机制有什么区别？ | ✗ | ✅ 覆盖 p.16/18 + p.29（Top-1 p.14，硬规则 p.18 ✓） |
| Q06 | 地西泮有哪些药理作用和临床应用？ | ✗ | ✅ p.7（1384） |
| Q07 | 地西泮的不良反应有哪些？ | ✓ | ✅ p.20（1397） |
| Q08 | 氟马西尼的用途是什么？ | ✓ | ✅ p.24（1401） |
| Q09 | 巴比妥类药物中毒怎么解救？ | ✗ | ✅ p.33（1410） |
| Q10 | 唑吡坦的作用特点是什么？ | ✓ | ✅ p.36 课件（chunk 1910） |

- 证据：`gate-result.json::checks[M0-7].evidence.cases`（逐题 top-4 命中、why、耗时）。

## 3. 浏览器 P0 路径复测（隔离实例，headless Edge）

场景脚本：[`scenarios/gate_browser.json`](scenarios/gate_browser.json)；原始结果：
[`shots/result.json`](shots/result.json)（0 个 JS 异常）；截图 14 张在 [`shots/`](shots/)。

| 步骤 | 结果 | 关键证据 |
|---|---|---|
| 错题页空态 | ✅ 真实主按钮「＋ 记一道错题」，文案为一句人话 | ![空态](shots/GATE-01-mistakes-empty.png) |
| 知识点页空态 | ✅ 「＋ 新建知识点」入口 + 表单说明 | ![空态](shots/GATE-02-kps-empty.png) |
| 新建知识点 → 列表可见 | ✅ toast：`已新建知识点：门禁浏览器知识点（#1）`；列表出现该点 | ![列表](shots/GATE-04-kps-after-create.png) |
| 复习页队列 | ✅ 「到期复习队列 · 共 1 项」，标注「新」 | `shots/GATE-05-review-queue.png` |
| 知识点详情 | ✅ 6 个评分 chip + 「记一道错题」按钮 + 掌握度/间隔/历史 | `shots/GATE-06-kp-detail.png` |
| **评分 0–2（点 2 分）** | ✅ toast：`门禁浏览器知识点：现在就该重学（掌握度 4%）· 已记入错题本，间隔归零` | ![评分](shots/GATE-07-score2-toast.png) |
| 错题本自动出现 | ✅ 错题本 · 1 道，状态「未解决」，错因「记忆遗忘」 | `shots/GATE-08-mistakes-auto.png` |
| 详情「记一道错题」→ 手动保存 | ✅ toast：`已记入错题本（#2）；该知识点的复习间隔已归零` | `shots/GATE-10-manual-mistake-toast.png` |
| 错题本两条（自动+手动） | ✅ 错题本 · 2 道，手动条目含我的答案/正确答案 | `shots/GATE-11-mistakes-final.png` |
| 掌握度四处一致（浏览器内 fetch 四接口） | ✅ `{"list":0.032,"detail":0.032,"graph":0.032,"stats":0.032}`，`diff=0` | `shots/GATE-12-kps-mastery.png`、`shots/GATE-13-overview.png`、`shots/GATE-14-graph.png` |

## 4. 全量回归与健康检查

| 门禁 | 命令 | 结果 |
|---|---|---|
| 全量回归 | `venv\Scripts\python.exe -X utf8 tests\run_all.py` | **Ran 93 tests · OK · 退出码 0**；用例 93/93 通过；功能矩阵 71/71 覆盖；未覆盖 10 项不变；历史缺陷 6 个（含本批 BUG-4/5/6）全部转正 |
| 健康检查 | `venv\Scripts\python.exe -X utf8 tools\healthcheck.py` | **PASS 9 / FAIL 0 / SKIP 0**（含看板 8756 当前 `documents=9`） |
| 门禁脚本 | `gate_check.py --label post` | **PASS 4/4**（HTTP 33 次 / 4.0s） |

原始输出：[`evidence/run_all.txt`](evidence/run_all.txt)、[`evidence/healthcheck.txt`](evidence/healthcheck.txt)、
[`evidence/gate-post-console.txt`](evidence/gate-post-console.txt)。
改前/改后机读对比：[`compare.json`](compare.json)。

## 5. 边界与未覆盖（诚实说明）

- 本门禁只覆盖 **M0-2/4/5/7 的判定层**；M0-1（场景骨架）、M0-3（产物导入，Lead 侧已有脚本验证）、
  M0-6（空态/文案全站清理）、M0-8（移动端）不在本协议内。
- 浏览器复测走的是三类学生 **P0 主动线**（建点→复习→错题→详情→掌握度），不含移动端 390 宽、
  AI 问答、入库/识图长任务（这些在 M0 门禁之外，且本轮约定不调用真实 AI）。
- M0-7 题表锁定在真实库 `镇静催眠抗焦虑药` 课件（doc 41，42 块）的页码上；若该课件重新入库导致
  页码变化，需按 `PROTOCOL.md §5` 重新核对题表（脚本会直接报出未命中题）。

## 6. 环境安全与清理

- 全程只使用隔离实例 `_work/eval/instances/qa-gate`（端口 8914）：代码副本 + 主库/raw 快照；
  **未写真实 `index/ subjects/ raw/ notes/ logs/`**，未碰真实看板 8756 与 DSH 3080。
- 全程 **0 次大模型调用**：门禁/浏览器只用本地 API 与本地嵌入检索。
- 复测完成后实例已 `down`，8914 端口已释放、无残留看板进程；本报告所有命令输出/截图已归档。

## 7. 未通过项最小复现

本次 **无未通过项**。回归时若某项 FAIL，按以下方式最小复现（完整见 PROTOCOL §10）：

```powershell
cd D:\study_agent
# 1) 全新隔离实例（若目录已存在：evalkit down → 删目录）
venv\Scripts\python.exe -X utf8 _work\eval\evalkit.py up --name qa-gate --port 8914
# 2) 门禁（逐项打印失败子检查）
venv\Scripts\python.exe -X utf8 _work\m0\gate\gate_check.py --base http://127.0.0.1:8914 --label post
# 3) 单接口/单题复现
venv\Scripts\python.exe -X utf8 _work\eval\evalkit.py api --name qa-gate --path /api/mistakes
venv\Scripts\python.exe -X utf8 _work\eval\evalkit.py api --name qa-gate --path "/api/search?q=苯二氮卓类的作用机制是什么？&k=8"
# 4) 回收
venv\Scripts\python.exe -X utf8 _work\eval\evalkit.py down --name qa-gate
```

## 8. 证据清单

| 证据 | 路径 |
|---|---|
| 门禁协议 | [`PROTOCOL.md`](PROTOCOL.md) |
| 门禁脚本 | [`gate_check.py`](gate_check.py) |
| 基线与复测结果 | [`baseline.json`](baseline.json)、[`gate-result.json`](gate-result.json) |
| 改前/改后对比 | [`compare.json`](compare.json) |
| 门禁/回归/健康检查原始输出 | [`evidence/`](evidence/) |
| 浏览器复测截图 14 张 + result.json | [`shots/`](shots/) |
| 浏览器场景脚本 | [`scenarios/gate_browser.json`](scenarios/gate_browser.json) |
