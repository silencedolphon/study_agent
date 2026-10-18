# M0-7 检索 10 题回归集 + 规则级排序修复 · 报告（task-10）

- 施工：施工智能体 C（search-tuner）｜日期：2026-10-18
- 范围：`core/search.py`（规则级改动）+ 新增 `tests/test_10_search_regression.py` + `_work/m0/search/`
- 结论：**10 题命中率 6/10 → 10/10**；Top-1 类（3 题）**1/3 → 3/3**；对比题覆盖 **0/1 → 1/1**；
  `tests/run_all.py --only test_10` **13/13 PASS**；`tools/healthcheck.py` **PASS 9/9**；
  `py_compile` OK；三模式与 corpus 隔离契约 15/15 不变。
- 所有改动都带 `[M0-7 R#]` 标记，逐条可回滚；`search.py` 之外的产品代码**零改动**。

---

## 1. 数据与基线口径（真实库只读）

| 项 | 值 |
|---|---|
| 真实主库 | `index/study.db`（8756 看板正在用的库） |
| 规模 | **9 份药理学课件 / 724 切块 / chunk id 1379–2421**（任务描述里的 322 切块是旧口径，以库里实际为准） |
| 测试副本 | `_work/m0/search/test.db`：`sqlite3` backup API 从真实库复制（`file:...?mode=ro` 打开源库，真实库零写入） |
| 副本校验 | `PRAGMA integrity_check = ok`，6090752 B；before/after 两份结果的 `db_sha256` 相同 |
| 真实库只读证据 | 本卡所有连接真实库的操作都 `file:...?mode=ro`（backup 复制）；复制后真实库文件 mtime 被后台看板/WAL checkpoint 动过（22:54），**13 张表逐表逐行与副本比对完全一致**（9 docs / 724 chunks 未变）；`tests/run_all.py` 全量运行的隔离核对结论也是"运行前后真实数据完全一致" |
| 关键页核对 | chunk 1382=p.5《镇静催眠药分类》、1393=p.16《BDZ增强GABA抑制》、1395=p.18（增加 Cl⁻ 通道开放频率）、1406=p.29 巴比妥机制、1397=p.20 地西泮不良反应 |

跑分工具（改前/改后同一把尺子）：

```powershell
# 只读副本（真实库只读复制）
venv\Scripts\python.exe -X utf8 -c "import sqlite3; s=sqlite3.connect('file:index/study.db?mode=ro',uri=True); d=sqlite3.connect('_work/m0/search/test.db'); s.backup(d); d.close(); s.close()"
# 10 题跑分（直接调 core.search.search，与 /api/search、chat.gather_evidence 同一条路径）
venv\Scripts\python.exe -X utf8 _work\m0\search\run_queries.py --label before --out _work\m0\search\before.json
venv\Scripts\python.exe -X utf8 _work\m0\search\run_queries.py --label after  --out _work\m0\search\after.json
```

## 2. 10 题回归集（清单与期望）

题面在 `tests/test_10_search_regression.py::QUESTIONS`（机器可读副本 `_work/m0/search/questions.json`，
测试里有 in-sync 断言防漂移）。`top_k=8`、`corpus="main"`、默认 `max_per_doc=4`。

| # | 查询 | 期望（doc/页/关键短语） | 判定 |
|---|---|---|---|
| Q01 | 镇静催眠药按化学结构分哪几类 | doc41 p.5《镇静催眠药分类》chunk 1382 | Top-1 |
| Q02 | 苯二氮卓类的作用机制 | doc41 p.16《BDZ增强GABA抑制》chunk 1393；**不得**是 p.29 巴比妥(1406) | Top-1 |
| Q03 | 苯二氮卓类和巴比妥类的作用机制有什么区别 | 覆盖 1393（p.16）+ 1395（p.18 增加Cl⁻通道开放频率）+ 1406（p.29 巴比妥） | 对比覆盖 |
| Q04 | 癫痫持续状态首选什么药 | 地西泮 i.v.：1388(p.11)/1928(p.35)/1938(p.45) 任一 | Top-3 |
| Q05 | 抗精神病药的作用机制 | doc47 p.17《典型抗精神病药作用机制》chunk 2130（阻滞 D2） | Top-3 |
| Q06 | 胆碱受体激动剂分为哪几类 | 1679(doc45 p.12 两种ACh受体) / 1727(doc43 p.59 分类列举) 任一 | Top-8 |
| Q07 | 新斯的明的作用和临床应用 | 2080(doc48 p.9 药理作用) / 2081(p.10 临床应用) 任一 | Top-8 |
| Q08 | 地西泮的不良反应 | doc41 p.20 chunk 1397；**不得**是 p.32 巴比妥不良反应(1409) | Top-1 |
| Q09 | 巴比妥类过量为什么会导致呼吸抑制 | 1409(p.32 大剂量抑制呼吸中枢) / 1410(p.33 中毒表现) 任一 | Top-8 |
| Q10 | M胆碱受体阻断药有哪些 | 1841(doc46 p.1 分类) / 1843(p.3 阿托品) 任一 | Top-8 |

## 3. before / after（Top-1 命中数：1/10 → 8/10，Top-k 判定 6/10 → 10/10）

| 题 | 判定 | 改前名次 | 改后名次 | 改前 Top-1 | 改后 Top-1 | 结果 |
|---|---|---|---|---|---|---|
| Q01 | Top-1 | 未进前8 | **1** | 1386（镇静催眠作用 p.9） | **1382（p.5 分类）** | FAIL→PASS |
| Q02 | Top-1 | 未进前8 | **1** | 1421（p.6 图） | **1393（p.16 BDZ机制）** | FAIL→PASS |
| Q03 | 对比 | BDZ 侧 0 条 | 2 | 1402（巴比妥药物 p.25） | 1391（p.14 机制节） | FAIL→PASS |
| Q04 | Top-3 | 3 | 1 | 1900（癫痫临床分型） | 1928（BDZ抗癫痫：地西泮首选） | PASS→PASS |
| Q05 | Top-3 | 未进前8 | 1 | 2170（异丙肼作用机制） | **2130（p.17 抗精神病D2）** | FAIL→PASS |
| Q06 | Top-8 | 2 | 5 | 1678 | 1678 | PASS→PASS |
| Q07 | Top-8 | 3 | 1 | 2082 | 2081（临床应用） | PASS→PASS |
| Q08 | Top-1 | 1 | 1 | 1397（p.20） | 1397（p.20） | PASS→PASS |
| Q09 | Top-8 | 2 | 1 | 1402 | 1409（p.32 呼吸抑制） | PASS→PASS |
| Q10 | Top-8 | 7 | 1 | 1856 | 1841（p.1 分类） | PASS→PASS |

汇总（`before.json` / `after.json::summary`）：

| 指标 | 改前 | 改后 |
|---|---|---|
| 判定通过 | **6/10** | **10/10** |
| Top-1 判定题通过 | **1/3** | **3/3** |
| 对比题覆盖 | **0/1** | **1/1** |
| Top-3 判定通过 | 1/2 | 2/2 |
| Top-8 判定通过 | 4/4 | 4/4 |
| 期望位次命中（Top-1 题） | 1 | 3 |

> 逐一 10 题的 Top-8 明细（chunk_id/页/标题/加权理由）在 `before.json`、`after.json`，可直接核对。

## 4. 改了什么（5 条规则，全部在 `core/search.py`）

> 代码里每条都有 `[M0-7 R#]` 注释 + 编号常量，便于逐条回滚。最终文件 sha256 前缀 `63f047e4`；
> 改前（HEAD `251886f`）sha256 前缀 `637fae08`。

### R1 关键词 LIKE 路归一化方向写反（重建缺陷，影响最大）
- 位置：`_normalize_high()`（新增，L265 附近）、`_keyword_search()` LIKE 块（L406 附近）。
- 问题：LIKE 路的分数是"命中极大窗口数"，**越大越好**，但重建版套用了 `_normalize`（bm25 的**越小越好**方向）：
  于是命中窗口最多的块得 0 分被丢弃、只命中一个词碎片的块反而得 1.0 —— 这是"搜'作用机制'时一堆
  「XX作用机制」标题页顶上来"的主因（源码审计 §5.2 已注明 `_keyword_search` 的 LIKE 主体是重建重写）。
- 改法：新增 `_normalize_high` 做正向归一（全相等/单命中给 1.0），LIKE 覆盖率改用它；FTS 路不动。
- 效果：Q01/Q05/Q10 直接转 Top-1；是其余规则生效的前提。

### R2 标题加权：实体词 > 通用意图词（对应评测 P0-B 第 2 条）
- 位置：`_heading_boost()` + `_specific_terms()`/`_entity_in_text()` 等（L452–L595 附近）。
- 问题：旧版"查询与叶子标题任意 ≥2 字公共片段 → 最多 +0.024"。`作用机制/不良反应` 这类词每章都有，
  谁标题带它谁涨分；而 p.16 标题是「BDZ增强GABA抑制」，一个字都不重合 → 永远漏。
- 改法：
  1. 先用 `_specific_terms` 去掉通用意图词，得到药名/类名（如"苯二氮卓类""巴比妥类"）；
  2. **实体词**命中叶子或整条面包屑 → 按长度加权（≤0.024，允许沿面包屑认亲）；
     实体词还认别名（`_ENTITY_ALIASES`：BDZ/Benzodiazepine/苯二氮䓬、barbiturate 等）；
  3. **通用意图词**只给固定 +0.006（不再按长度加权）；
  4. 分类题（"按…分/分几类/分型"）：标题含「分类/分型」再 +0.012（把 p.5 分类页挑出来）；
  5. 机制题：标题同时含"实体 + 机制动作词（增强/抑制/开放…）"再 +0.010（把 p.16 挑出来）。
- 效果：Q01（1382 分类页）、Q02（1393 机制动作页）、Q05（2130 实体机制页）转 Top-1；Q08 稳。

### R3 LIKE 关键词路补上 heading_path（FTS 有、LIKE 漏）
- 位置：`_keyword_search()` LIKE 块（L366–L400 附近）。
- 问题：FTS 的索引列是 `(text, heading_path)`，但重建版的 LIKE 兜底只搜正文。实测目标页 1393 的祖先标题
  写着「作用机制」，正文却没有"作用机制"四个字 ⇒ 关键词分直接 0；2080（新斯的明药理作用）、1841（M 受体
  阻断药分类）同理。这解释了"答案页正文不重复标题⇒关键词路召回不到"。
- 改法：每个窗口除了搜 `text_norm/text`，再搜一次 `heading_path`（带同一个 corpus 过滤），命中计 1 个窗口。
- 效果：Q02/Q08（1397 标题命中"地西泮"）、Q07/Q10 明显改善；三模式语义不变。

### R4 识图块"正文优先"降权（补回 v1.1 丢失的 `_is_figure_hit`）
- 位置：`_is_figure_hit()`/`_prefers_text()` + hit 循环里 `boost -= 0.012`（L598–L620、L825 附近）。
- 问题：p.6/p.15 的识图块（视觉模型对页图的解读）靠"标题='第一节苯二氮卓类'/语义相似"占据 Top-1，
  把 p.16 正文机制页挤出去；审计 §5.2 里 `_is_figure_hit` 正是 v1.1 有、重建版丢掉的函数。
- 改法：`source_ref` 含"图"识别识图块；问句是"机制/分类/不良反应/首选…"等**正文题**时降权 -0.012；
  问句含"图/结构图/长什么样/看图"时不降权（那时图就是答案）。仍保留识图块（CHAT_FIGURES 开时照常进证据）。
- 效果：Q02 的 p.16 超过 p.6/p.15 识图块；Q01/Q08 也受益。

### R5 对比题：A/B 各检索一次再合并（对应评测 P0-B 第 3 条）
- 位置：`_compare_entities()` + `search()` 尾部合并块（L625–L641、L854–L900 附近）。
- 问题：两实体同属一份课件时，整句检索+单文档配额会把另一侧挤掉：Q03 改前 BDZ 的 p.16/p.18 一条都不在前 8。
- 改法：查询同时含对比标记（区别/不同/比较/vs…）且能抽出 ≥2 个实体时，对每个实体各跑一次检索
  （实体查询里其它实体已剔除，不会递归识别成对比题），取前 3 条"代表命中"提到前面，再重新施加单文档配额；
  代表命中的 `why` 会写明"对比题实体「X」代表命中"。
- 效果：Q03 覆盖 1393+1395+1406，PASS；其它 9 题不受影响（不带对比标记不触发）。
- 代价：仅对比问句多 2 次检索（本地 20–40ms/次）。

## 5. 测试与验证证据

| 验证 | 命令 | 结果 |
|---|---|---|
| 语法 | `venv\Scripts\python.exe -m py_compile core\search.py tests\test_10_search_regression.py` | OK |
| 新增用例（只跑 test_10） | `tests\run_all.py --only test_10` | **13 条用例 13 通过 0 失败，退出码 0** |
| 新增用例（unittest 直跑） | `python -m unittest discover -s tests -t tests -p "test_10*.py"` | 13 tests OK |
| 契约 | `_work/m0/search/check_contract.py` | **15/15 PASS**（hybrid/vector/keyword 三模式、corpus main/aux/all/None、空查询、doc_ids、top_k、cite/why、对比合并） |
| 环境 | `tools\healthcheck.py` | **PASS 9 / FAIL 0** |
| 10 题跑分 | `run_queries.py` before/after | **6/10 → 10/10**（见 §3） |
| **全量回归** | `tests\run_all.py` | **93 条用例 93 通过 / 0 失败 / 0 跳过，退出码 0；功能矩阵 67/67 行覆盖；耗时 68.3s；隔离核对"运行前后真实数据完全一致"**。其中原有 test_01–test_07 全绿（含 test_02 `test_search`、test_04 `test_search`），test_10 新增 13 条全绿。改造过程前 3 次全量因另两个施工智能体同时在跑 run_all（共用 `_work/verify/data` 被锁/清空）失败，第 4 次在无并发时通过 —— 最终结果以本行为准。 |

新增用例共 13 条（10 题 + 契约 + 数据页快照 + questions.json 同步），
命名都含 `test_search`，全量跑时会被现有矩阵行 **F14「混合检索」**（`tests=["test_search"]`）自动收纳，
**不需要改 `tests/matrix.py` 也能显示**；若 Lead 想单列，建议新增一行：

```python
dict(id="F96", feature="检索 10 题回归（真实药理课件库，M0-7）",
     entry="core.search.search（只读副本）", kind="unit", priority="P0",
     tests=["test_search_regression"], evidence="json"),
```

（本轮**没有**改 `tests/matrix.py` / `run_all.py` / dashboard / 其它 core，遵守任务边界。）

## 6. 回滚点（逐条）

| 级别 | 操作 | 说明 |
|---|---|---|
| 整体 | `git checkout -- core/search.py` | 回到 HEAD `251886f` 的搜索实现（改前基线，sha 前缀 `637fae08`） |
| 单条 | 搜 `[M0-7 R1]`～`[M0-7 R5]` 标记，删除对应块/调用点 | 五条规则彼此独立；R1 是其它规则的前提，单独回滚 R2–R5 不影响 R1 |
| 数据 | `_work/m0/search/test.db` | 只读副本，删掉会自动由测试 `setUpModule` 从真实库重新 backup |

## 7. 哪些属于 `core/search.py` 重建缺口（本次只能规则级兜底）

1. **R1 直接命中重建缺陷**：`_keyword_search` 的 LIKE 主体是重建重写（审计 gap-report §5.2），归一化方向写反；
2. **R4 补回 v1.1 真值函数 `_is_figure_hit`**（审计 §5.2 missing_funcs 之一）；
3. **R3 是重建时"FTS 与 LIKE 行为不一致"**（FTS 索引 heading_path、LIKE 没搜）；
4. **仍缺、本次未碰的 13 个函数**：`expand_neighbors` 的 `base_of/group_of/same_theme/rows_near/wrap/expand_one`
   （195 行 vs 现 49 行）、`expand_continuations.fetch/topic_candidates`（182 vs 64 行）、`_flag_confidence` 等 ⇒
   **"同主题续页补全/邻页补全/置信度标注"仍是残缺实现**。本次 Q03 的"跨页机制链"是靠 R5 对比题规则
   局部兜底，并非常规检索路径已修好 —— 普通问句仍可能漏相邻页。
5. 建议：彻底重建 `core/search.py`（`tools/ai_decompile.py --pyc core\search.pyc` 逐函数，约 ¥0.3 + 人工核对）
   需**另行申请**（涉及真实 AI 调用）；本次按计划只做零成本规则级修复。

## 8. 风险与后续建议

- **风险 1（中）**：R2/R4 是启发式加权（通用词小额、识图降权），极端问句可能误伤；已用 10 题 + 契约用例兜底，
  建议后续补 3 类反例（纯看图问句、纯术语题、跨文档对比）。
- **风险 2（低）**：R1 让关键词候选更"严格"，个别查询 Top-k 不足 8 条（如 Q01 只有 4 条）——噪声少了但证据条数
  可能变少；chat 侧本来靠多路检索+改写补量，观察真实问答的引用覆盖率即可。
- **风险 3（中，运维）**：**看板 8756 仍跑着旧进程**，新 `core/search.py` 要重启看板才生效（重启不属于本卡范围，
  且当前有其它看板类改动在并行施工，建议由 Lead 统一重启并复测）。
- **风险 4（低）**：期望 chunk id 基于当前真实库；若用户重新入库导致 chunk id 漂移，用例的 `_fallback_rank`
  会按 doc/页/标题兜底，另有 `test_search_regression_fixture_pages` 给出"数据已变、需重拍 test.db"的明确报错。
- 后续建议（按性价比）：
  1. Lead 在合并其它卡改动后再跑一次全量（本轮最后一次已 93/93 全绿，退出码 0）；
  2. 矩阵已验证自动收纳：全量里 **67/67 行全部覆盖**，F14 同时挂了原有两条与 `test_10` 的 13 条，无需改
     `tests/matrix.py`；若想单列则用 §5 的 F96 建议；
  3. 计划表 M0-7 状态改"已完成（规则级）"，并登记"search.py 彻底重建"为后续独立任务；
  4. 若要继续提升检索，优先重建 `expand_continuations/expand_neighbors`（续页补全比排序加权更影响"机制链"完整性）。

## 9. 产物清单（全部在允许范围内）

```
core/search.py                                  # 唯一产品代码改动（+308/-27，含注释）
tests/test_10_search_regression.py              # 新增：10 题 + 契约 + 数据守卫，13 条用例
_work/m0/search/questions.json                  # 10 题机器可读清单（与测试内嵌同步）
_work/m0/search/test.db                         # 真实库只读副本（sqlite backup）
_work/m0/search/before.json / after.json        # 改前/改后 10 题 Top-8 明细（含判定）
_work/m0/search/run_queries.py                  # 跑分器（改前/改后同一把尺子）
_work/m0/search/check_contract.py               # 三模式/corpus 契约核查 15/15
_work/m0/search/diag.py / debug_like.py / probe_*.json   # 定位过程的诊断脚本与中间结果
_work/m0/search/report.md                       # 本报告
```
