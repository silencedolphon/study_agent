# M0-A 报告：仪表盘闭环（M0-2 新建知识点入口 + M0-4 错题闭环 + M0-5 掌握度口径统一）

- 任务：`task-8`（M0-A）· 执行人：dash-fixer · 时间：2026-10-18 22:44–22:58
- 只改：`tools/dashboard.py`（+255 / -24 行，行尾 CRLF 保持原样）+ 新建 `tests/test_08_dashboard_fixes.py`；证据全在 `_work/m0/dash/`
- 未动：`core/`、其它 `tools/`、`tests/matrix.py`、`tests/run_all.py`、真实数据、8756/3080、无 git commit、无 AI 调用

## 1. 一句话结果

三项全部完成：空库能从「知识点」页 2 击建知识点并进复习队列；`POST /api/mistake` 与「评分 0–2 自动记错题」全程打通（API + 浏览器 toast 都已实测）；同一知识点的掌握度在 `/api/kps`、`/api/kp?id=`、`/api/graph`、`/api/stats` 四处完全一致（本次实测 4 处都是 0.05，spread=0）。新增 7 条自动化用例，全量 `tests/run_all.py` **74/74 通过、退出码 0**。

## 2. 改动清单（tools/dashboard.py，行号为改后）

### M0-2 恢复「＋ 新建知识点」入口（前端）
| 位置 | 改动 |
|---|---|
| `dashboard.py:3126` | 新增 `kpCreateCard()`：常驻卡片 +「＋ 新建知识点」按钮（`#kp-create-toggle`）+ 默认收起的表单容器（`#kp-create-form`，内容=现有 `kpForm()`） |
| `dashboard.py:3141` | 新增 `bindKpCreate()`：点按钮展开/收起，展开时调用 `bindKpForm()` 并把焦点给名称框 |
| `dashboard.py:3157` | `viewKps()`：**空态分支与列表分支都**渲染 `kpCreateCard()` 并绑定；列表分支卡片顺延为第二张卡 |
| `dashboard.py:5250` / `5263` | 复用并增强原来的 `kpForm()` / `bindKpForm()`：保存按钮改「保存知识点」，成功后 `toast` 提示 + `await go('kps')` 刷新；失败 toast（原来不看响应，失败时「点了没反应」）；名称仍必填 |

### M0-4 错题闭环
| 位置 | 改动 |
|---|---|
| `dashboard.py:11493` | 新增 `_opt_int()`：写接口的可选整数参数校验（非法明确拒绝，区别于只读接口的 `_int_arg` 兜底） |
| `dashboard.py:11513` | 新增 `api_add_mistake()`：校验 question 必填、kp_id/doc_id/chunk_id 必须为正整数**且行存在**、reason 必须是 6 个标签之一或空；非法一律 `_code:400`；成功调 `study.add_mistake()` 返回 `{"ok":True,"mistake_id":N,"kp_id":...}` |
| `dashboard.py:12983`（do_POST） | 新增 `POST /api/mistake` 分派（在写库 `_lock` 块内，走 `_json_auto` 认 400） |
| `dashboard.py:11829` | `api_review()`：评分 0–2 时**先** `record_review()`（写 reviews+SM-2）**再** `study.add_mistake(question=本次题面或 KP 名称, reason="记忆遗忘", kp_id=...)`，响应带 `auto_mistake_id`；`mastery`/`due_in_days` 改为按写后新行重算（答错后间隔归零、`due_in_days=0`）。重复评分各记一条，不去重；grade 非 0–5 返回 error（不再因非数字 500） |
| `dashboard.py:5285`（`openKp()` 知识点详情弹窗） | 评分区加说明「0–2 视为答错，会自动记一条错题」；底部新增「记一道错题」按钮 `#kp-add-mistake`；评分 toast：答错时追加「已记入错题本，间隔归零」，`due_in_days=0` 时显示「现在就该重学」而不是「0 天后复习」 |
| `dashboard.py:5351-5411` | 新增 `MISTAKE_REASONS`（6 个错因标签）+ `openMistakeForm()`：题干（必填）/我的答案/正确答案/错因 6 选 1（默认记忆遗忘），POST `/api/mistake`（带 kp_id），成功 toast + `go(CURRENT)` 刷新 |
| `dashboard.py:5436`（错题页） | 错题本卡片右上角常驻「＋ 记一道错题」（空态/非空态都有，统一走 `openMistakeForm({})`）；空态文案改为「点右上角「＋ 记一道错题」手动记一条；复习时评分 0–2 也会自动记入。」 |
| `dashboard.py:1531-1532`（复习页空态） | 「今天没有到期的复习」的指引从**不存在的**「让助手从资料中生成新的知识点」改为「去「知识点」页点「＋ 新建知识点」」 |
| `dashboard.py:1551`（复习页评分说明） | 改成「评分 0–2 视为答错（自动记入错题本、复习间隔归零，详情弹窗里也能点「记一道错题」手动记），3 勉强、4 正确、5 轻松」 |

### M0-5 掌握度口径统一（后端）
| 位置 | 改动 |
|---|---|
| `dashboard.py:7204` | `api_kps()`：`mastery` 改为 `study.compute_mastery(row)` 现算（不写库）；去掉 SQL `ORDER BY mastery`，改 Python 侧 `out.sort(key=(mastery,name))` 保持升序语义；其余字段/类型不变 |
| `dashboard.py:7259` | `api_graph()`：节点 `mastery` 同样 `compute_mastery(row)` 现算，字段名/结构不变 |

## 3. 逐项验收证据

### M0-2（空库 ≤2 击出表单 → 建成 → 可见 → 进复习流）
- 自动化：`tests/test_08_dashboard_fixes.py::TestM0CreateKp`
  - `test_empty_library_create_kp`：空库（`empty.db`）`/api/kps` = [] → `POST /api/kp` 200 → `/api/kps` 立即出现该 id → `/api/queue` 出现 `status="new"` → 同名再建 upsert 同一 id；
  - `test_frontend_entry_rendered`：页面含 `#kp-create-toggle`、`kpCreateCard()`/`bindKpCreate()` 各在 `viewKps()` 调 **2 次**（空态+列表）；`kpForm()` 的 4 个字段（名称必填/科目/摘要/chunk_id）齐全；含 `go('kps')`。
- 浏览器（qa-dash 全新隔离实例，8911）：
  - `shots/M0-03-kps-empty.png`：空库知识点页顶部常驻「＋ 新建知识点」；
  - `shots/M0-04-kp-form.png`：**1 次点击**展开表单（`#kp-create-form.hidden=false`，按钮变「收起表单」；result.json `M0-04-form-visible`）；
  - `shots/M0-06-kp-list.png`：填名称/科目/摘要保存后刷新，列表出现「苯二氮卓类作用机制（M0-2 验收）」；`M0-06-kp-list-mastery` 抓到行文本；
  - `shots/M0-15-review-queue.png`：该知识点进入复习队列（已复习后状态 due，掌握度 3%）。
- API：`_work/m0/dash/api-evidence.json` 中 `kps_status=200`、`kp.id=1`。

### M0-4（错题写入闭环）
- 自动化：`tests/test_08_dashboard_fixes.py::TestM0Mistake`
  - `test_post_mistake_ok_and_invalid`：200 且 `/api/mistakes` 数量 +1、字段正确；6 组非法参数（缺 question/空白/kp_id 非整数/chunk_id 非整数/非法错因/不存在的 kp_id）全部 **400 + error**，且数量不变；
  - `test_review_wrong_auto_mistake`：评分 4 无 `auto_mistake_id`；评分 2 有，错题 +1、`reason=记忆遗忘`、`question`=本次题面；该 KP `interval_days=0`、`lapses+1`；无题面时回落知识点名称；重复评分各记一条；评分 3 不记错题；
  - `test_frontend_mistake_entry`：页面有「记一道错题」/`openMistakeForm`/`/api/mistake`/`auto_mistake_id`/「已记入错题本」；**旧误导文案已消失**（`复习答错时让助手记录`、`让助手从资料中生成新的知识点` 断言 NotIn）；错题页有「＋ 记一道错题」、复习空态指向「＋ 新建知识点」。
- 浏览器（qa-dash）：
  - `shots/M0-07-kp-detail.png`：知识点详情底部「记一道错题」按钮 + 评分说明；
  - `shots/M0-08-mistake-form.png`、`M0-09-mistake-form-filled.png`：表单（题干/我的答案/正确答案/错因 6 选 1，默认记忆遗忘；result.json `M0-08-mistake-form-fields`）；
  - `shots/M0-16-manual-mistake-toast.png` + `result.json::M0-16-toast-text`：toast「已记入错题本（#3）；该知识点的复习间隔已归零」；
  - `shots/M0-17-auto-mistake-toast.png` + `M0-17-toast-text`：评分 2 后 toast「苯二氮卓类作用机制（M0-2 验收）：现在就该重学（掌握度 5%） · 已记入错题本，间隔归零」；列表里「遗忘 6、间隔 0 天、下次 10/18 22:52」；
  - `shots/M0-01-mistakes-empty.png`：错题空态文案 + 「＋ 记一道错题」按钮；`shots/M0-12-mistakes-list.png`、`M0-18-mistakes-after-both.png`：错题本累计 5 条（手工 3 + 自动 2）；
  - `api-evidence.json`：`invalid_mistake_status=400`（body 为「题目不能为空」）、`valid_mistake_status=200`（`mistake_id=5`），`mistakes_count` 4→5。

### M0-5（掌握度口径统一）
- 自动化：`tests/test_08_dashboard_fixes.py::TestM0Mastery`
  - `test_mastery_consistent_across_surfaces`：评分 5 后 5 个值（review 响应 / kps / kp 详情 / graph / stats.weakest）最大差 ≤0.01；且 `/api/kps`、`/api/graph` 不再返回恒 0；断言 **库列 `mastery` 仍为 0**（只读现算、不写库）；
  - `test_kps_sorted_by_mastery`：列表整体掌握度升序；未复习(0%) 排在已复习(>0%) 之前。
- 浏览器（qa-dash）：
  - `api-evidence.json`：同一 KP 四处 `mastery` 全为 **0.05**，`mastery_spread=0.0`，而 `interval_days=0.0`（评分 2 后归零）；
  - `shots/M0-13-graph.png`：图谱节点显示「掌握 3%」（与当时列表一致）；`shots/M0-14-kps-mastery.png`：列表掌握度 3%（评分前是同屏 0% 的矛盾已消除）。
- 注：计划里「评分 5 后列表 32%」是特定历史数据下的示例值；本实现口径 = `compute_mastery()`（间隔 log 压缩 + reps 增益 - lapses 惩罚），本机空库新建点评分 5 后约 13%，实测四次面一致即达标。

## 4. 测试输出（证据文件）

```text
# 单模块（7 条全过）
venv\Scripts\python.exe -X utf8 tests\run_all.py --only test_08
→ 用例：7 条 · 通过 7 · 失败 0 · 退出码 0        （log：run_all_only_test08.log）

# 全量（74 条全过；67 旧 + 7 新）
venv\Scripts\python.exe -X utf8 tests\run_all.py
→ 用例：74 条 · 通过 74 · 失败 0 · 已知缺陷 0 · 耗时 58.3s · 端口全部释放 · 退出码 0
→ 功能矩阵：67 行已覆盖 67 行、未覆盖 10 项（与改动前一致）   （log：run_all_full.log）
```

- 说明：全量 log 产生于 22:47 左右（三项改动完成后），当时 `core/search.py`、`tools/import_kp_artifacts.py` 等其它智能体的改动正在工作区，但沙箱代码指纹一致、无失败；最终全量以 Lead 合并后统一跑为准。
- 22:53 重跑 `--only test_08` 时曾遇到 `PermissionError: _work\verify\data\main.db 被占用`（另一个智能体正在跑全量，测试环境互斥），非代码问题；稍后重试通过（见重试版 log）。
- 另用 `node --check` 校验了内嵌页面 JS（从 `INDEX_HTML` 抽出 `_work/m0/dash/page.js`，332,945 字节）语法 OK。

## 5. 浏览器实例与隔离

- 实例：`_work/eval/evalkit.py up --name qa-dash --port 8911`（代码=最新工作区；数据=真实主库只读快照 + raw 副本）。
- `evalkit.py verify --name qa-dash`：**9/9 PASS**（raw/学科/统计都在沙箱内、有 9 份已入库资料）。
- 场景脚本：`scenario-m0.json`（15 张主流程截图）、`scenario-toast.json`（toast 补拍），输出 `shots/`（18 张 PNG）。
- 结束：`evalkit.py down --name qa-dash`（端口 8911 已释放、stale 实例目录已删除），未触碰真实 8756/3080。

## 6. 矩阵行建议（matrix.py 由 Lead 改）

```python
dict(id="F96", feature="M0-2 新建知识点入口（空库可建成→进复习流）", entry="GET /api/kps + POST /api/kp + GET /api/queue", kind="write", priority="P0",
     tests=["test_08_dashboard_fixes.TestM0CreateKp"]),
dict(id="F97", feature="M0-4 错题写入闭环（POST /api/mistake + 评分 0–2 自动记）", entry="POST /api/mistake / POST /api/review", kind="write", priority="P0",
     tests=["test_08_dashboard_fixes.TestM0Mistake"]),
dict(id="F98", feature="M0-5 掌握度四处同口径（不写库）", entry="GET /api/kps + /api/kp + /api/graph + /api/stats", kind="read", priority="P0",
     tests=["test_08_dashboard_fixes.TestM0Mastery"]),
```
建议同时把 P0-A（写入闭环断裂）/P0-C（掌握度口径不一致）以已修复缺陷登记进 `FIXED_BUGS`（fixed_in 填 Lead 的合并 commit，verify 填本报告 §3）。

## 7. 风险 / 遗留（如实）

1. **手动「记一道错题」带 kp_id 时会自动降级该知识点**（`add_mistake` 的 core 既有语义：间隔归零+lapses+1）——这是计划要的「错题闭环」，但计划同句提到的「可撤销」本次**没做**（需新增撤销 API/UI，超出 task-8 允许的改动面；建议排进 M1）。
2. `POST /api/mistake` 的 `reason` 严格限定为 `study.REASON_TAGS` 6 个标签或空：其它脚本若想写自定义错因会被 400。当前无其它调用方，且前端就是 6 选 1。
3. `api_kps` 排序从 SQL 移到 Python：语义为（mastery 升序, name）；中文排序在当前数据一致，但若将来 SQLite 换 NOCASE/自定义 collation 需注意。
4. 评分 0–2 的 `due_in_days` 现在返回 0（`add_mistake` 把 due 设为现在），前端已改成「现在就该重学」文案，**没有**沿用「0 天后复习」。
5. `/api/kp` 旧表单保存失败路径原来静默（不看响应），本次改成 toast；若将来有人依赖"点了不报错"，行为略有变化。
6. 浏览器实测用的 `logs/knowledge`/`logs/outline` 未复制进隔离实例（evalkit 现状）：知识点页的「批量整理/名词解释/导图」卡在这台实例上仍是"未整理"空态，与本任务三项验收无关；新建知识点入口与错题闭环不依赖这些产物。
7. 与 M0-3（产物导入）协同：导入工具写入 `knowledge_points` 后，`/api/kps`/`/api/graph` 会立刻显示真实掌握度（不再是 0），无需回填迁移。

## 8. 回滚

只涉及一个产品文件：`git checkout -- tools/dashboard.py`（本任务未提交，回滚即恢复 HEAD 版本）；新测试文件 `tests/test_08_dashboard_fixes.py` 随任务一并保留或删除。无数据迁移、无配置改动。
