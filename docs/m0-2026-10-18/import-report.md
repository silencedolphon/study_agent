# M0-3 报告 · 整理产物 → 知识点表一键导入

> 任务：task-9（M0-B）· 施工智能体 B（artifact-importer）· 2026-10-18
> 交付物：`tools/import_kp_artifacts.py`、`tests/test_09_import_artifacts.py`、本报告
> 结论：**真实 9 份课件 / 256 条知识点 / 725 条出处全部导入成功；dry-run 零写入；
> 二次导入新增 0；全程 0 次 AI 调用、0 网络请求、0 毫秒模型耗时。**

---

## 1. 背景与问题（Lead 已确认）

`core/kp_build.py` 的「整理知识点」只写
`logs/knowledge/<doc_id>-末级知识点总结.json`，**从不写数据库**。于是真实站点：

- `logs/knowledge` 有 9 份产物（1.2 MB，256 条知识点）；
- `index/study.db` 的 `knowledge_points` = **0**、`kp_sources` = **0**；
- 看板「整理完看不见」，复习队列永远空。

本任务写一个**纯离线**导入脚本，把产物灌进 `knowledge_points` + `kp_sources`。

---

## 2. 产物结构结论（真实 9 份实测 + 跨版本兼容）

9 份产物全部是 `C-0058-v4` 管线，顶层三键：

```
{ "meta": {...}, "structure": {...}, "knowledge_points": [ ... ] }
```

`knowledge_points[]` 关键字段（跨版本差异已核对）：

| 字段 | 本批实际 | 兼容处理 |
|---|---|---|
| `id` | `KP-N03-1`，**仅在单份课件内唯一**（`KP-N03-1` 在 5 份产物里重复出现） | 幂等键必须带 doc_id |
| `name` / `node_name` | 末级节点名 | 取 `name`，缺失退回 `node_name` |
| `node_path`（v2+）/ `path`（v1，如 C-0032 的 `89-…json`） | 完整路径（含根） | 两者都认，`name` 不在路径末项时以 `name` 为准 |
| `summary` | 一段理解（150~400 字） | 落 `knowledge_points.summary` |
| `points` | 3~6 条要点 | 拼进 summary：`… 要点：A；B；C`（表里只有 summary 一列，不能丢） |
| `pages` | 覆盖页（1~7 页） | 出处兜底用；v1 的 `content_pages`/`self_pages` 也认 |
| `sources[].chunk_ids`、`material_block_ids`、v1 `blocks[].chunk_id` | 管线真实读过的材料块 | 出处**首选**（最精确） |
| `meta.doc_id` / `meta.chapter` / `meta.pipeline_version` | 9 份齐全 | doc_id 缺失时退回文件名前缀；chapter 落 subject |
| `structure.root/nodes` | 思维导图树 | 不需要（导入只用 `knowledge_points`） |
| `terms`（v3 有、v4 没有） | — | 名词解释是独立功能，本脚本忽略（不误当知识点） |

极旧格式兜底：无 `knowledge_points` 但顶层有 `tree` 节点树时，递归取末级节点
（name/path/pages/summary）；两份都缺则整份记 `failed_files` 并继续。

坏文件不会中断整批：本次真实目录有 0 个坏文件；测试里专门放了坏 JSON 用例。

---

## 3. 去重 / 幂等策略（核心设计）

1. **稳定键** = `<doc_id>:<产物知识点 id>`，例如 `41:KP-N03-1`。
   产物 id 跨课件重复，所以必须带 doc_id —— 对应任务卡「doc_id + 末级节点稳定 id」。
2. **名称唯一**：`knowledge_points.name` 是全局 `UNIQUE`（core/db.py L103）。
   真实产物有 **4 组重名**需要处理：
   - 跨课件：「课程导入」×2、「临床应用」×3；
   - 同课件内：「临床应用与不良反应」×2（doc 46 两个不同父级）。
   处理：先按完整路径**逐级加父级前缀**（`父级 · 名称`，与旧版 `display` 字段
   同一文案习惯），直到本次待导入集合内唯一；再与库里已有名字避让；
   仍冲突（说明是用户手工建的）→ **跳过并计数，不覆盖**。
   实导结果：7 条被加前缀，0 冲突，库内 0 重名。例：
   `镇静催眠药总论 · 课程导入`、`抗胆碱酯酶药和胆碱酶复活药 · 课程导入`、
   `山莨菪碱 · 临床应用与不良反应`、`新斯的明 · 临床应用`。
3. **导入清单**落 `meta` 表（key=`kp_import.artifacts.v1`，256 条 / 28 KB JSON，
   不建新表、不动 schema）。二次运行先按清单 kp_id 命中 → 已存在；
   **清单丢失时**退回「名称 + 摘要完全一致」认领，仍不会重复建卡（有测试）。
4. **出处**：`kp_sources` 主键 `(kp_id, chunk_id)`，重复插入天然不重复；
   重复导入时只补新增出处，不重复计数。
5. dry-run 用 **sqlite `mode=ro` 只读连接**（不建 -wal/-shm、不碰库文件），
   与 apply 共用同一套规划逻辑，保证「预览即所得」。

---

## 4. CLI

```powershell
# 默认 dry-run（预览，不写库）
venv\Scripts\python.exe -X utf8 tools\import_kp_artifacts.py

# 真写库（默认 config.DB_PATH；先备份！）
venv\Scripts\python.exe -X utf8 tools\import_kp_artifacts.py --apply

# 指定副本库 / 产物目录 / 只导一份 / JSON 输出
venv\Scripts\python.exe -X utf8 tools\import_kp_artifacts.py --apply `
    --db _work\m0\import\real-copy.db --artifacts-dir logs\knowledge --doc-id 41 --json
```

退出码：`0` = 跑完（单份产物坏/单条知识点失败也继续，摘要里列）；
`2` = 前置不满足（库/目录不存在、缺表）。不改 dashboard、不改 core、不联网、不调模型。

---

## 5. 真实副本导入统计（证据文件同目录）

副本制作：真实主库 `index/study.db` 用 sqlite3 backup API 只读快照 →
`_work/m0/import/real-copy.db`。**真实库全程只读，导入后仍 `knowledge_points=0`。**

| 指标 | dry-run | 第 1 次 `--apply` | 第 2 次 `--apply` |
|---|---|---|---|
| 扫描产物文件 | 9 | 9 | 9 |
| 解析成功 / 失败 | 9 / 0 | 9 / 0 | 9 / 0 |
| 命中库内文档 | 9 | 9 | 9 |
| 待导入知识点 | 256 | 256 | 256 |
| **新建知识点** | 0（将建 256） | **256** | **0** |
| 已存在跳过 | 0 | 0 | **256** |
| 名称冲突跳过 / 无效 | 0 / 0 | 0 / 0 | 0 / 0 |
| **kp_sources 写入** | 0（将写 725） | **725** | **0** |
| 无出处知识点 | 2 | 2 | 2 |
| 耗时 | 29 ms | 1194 ms | 67 ms |

证据：`evidence-dryrun.json` / `evidence-apply1.json` / `evidence-apply2.json`
（`--json` 原样落盘）。幂等的直观证据：第 2 次 **新建 0、出处 0**，而
`kps_existing=256`；`meta` 清单里 256 个 `kp_id` 与第 1 次完全一致
（`test_second_apply_idempotent` 断言了这一点）。

导入后副本库（`db-stats.json`）：

- `knowledge_points` = **256**，`kp_sources` = **725**；
- `had_sources=1` 的知识点 = 254（2 条产物本身没有覆盖页/材料块，见 §7 风险）；
- 出处覆盖全部 **9 份课件**（doc 40~49），孤儿出处 0，重名 0；
- 出处来源：**253 条用产物显式材料块**（管线真读过，最精确）、1 条按覆盖页兜底
  （`source_ref` 的 `p.12` / `p.12-16` / `p.12 图` 三种形态都能解析）、2 条无出处；
- `study.compute_mastery()`：新导入全部 0.0（无复习记录，口径正确）；
- `study.dashboard()`：`{"total":256,"due":0,"new":256,"reviews_today":0}`；
- `study.due_queue()` 能取到 256 条 new 条目 ⇒ **「复习队列永远空」已解决**。

每份课件：doc40=18 条/101 出处、doc41=37/54、doc43=31/115、doc44=39/111、
doc45=15/17、doc46=22/25、doc47=56/198、doc48=17/30、doc49=21/74
（`source-origin.json`）。

---

## 6. 看板可见性验证（隔离实例，8912）

按规矩起隔离实例（真实数据备份进沙箱，不碰 8756 / 不碰真实库）：

```powershell
# 存在先删目录，再 up
venv\Scripts\python.exe -X utf8 _work\eval\evalkit.py down --name qa-import   # 如有
Remove-Item -Recurse -Force _work\eval\instances\qa-import                    # 如有
venv\Scripts\python.exe -X utf8 _work\eval\evalkit.py up --name qa-import --port 8912

# 在沙箱里用沙箱副本的脚本导入（写的是沙箱 index/study.db）
<sb>\..\venv\Scripts\python.exe -X utf8 <sb>\tools\import_kp_artifacts.py --apply `
  --db <sb>\index\study.db --artifacts-dir D:\study_agent\logs\knowledge --json
# 结果：kps_created=256、kp_sources_added=725（与副本库一致）

# 实测 API（GET）：
GET /api/kps        -> 200，knowledge_points 256 条，n_sources 合计 725
GET /api/kps        -> 前 5 条：5-羟色胺功能与通路 / BDZ增强GABA抑制 / CNS主要递质及效应 …
GET /api/stats      -> 200，knowledge={"total":256,"due":0,"new":256,"reviews_today":0}
GET /api/queue?limit=5 -> 200，5 条（首条「课件封面」）  ← 复习队列有内容了
GET /api/kp?id=1    -> 200，详情带 sources（抽查 7 条出处）、mastery=0.0
```

验证完 `down` 并删掉 `_work/eval/instances/qa-import`（端口 8912 已释放）。

---

## 7. 测试

`tests/test_09_import_artifacts.py`（6 条，复用 `tests/env.py` 的辅助函数，
但**不复用共享 `_work/verify/data/`**：fixture 只读复制到本模块私有目录
`_work/verify/t09_private/`，避免与其他并发套件抢 `main.db`）：

| 用例 | 断言要点 |
|---|---|
| `test_dry_run_does_not_write` | 默认 dry-run：扫描 3（2 好 + 1 坏 JSON）/解析 2/失败 1 不中断；将建 4；库表与 meta 清单**零写入** |
| `test_apply_writes_and_api_visible` | --apply：建 4、出处>0；跨课件同名同 id 靠父级前缀区分且库内无重名；had_sources / note 溯源正确；起隔离看板 GET `/api/kps`=4、`/api/stats` total=4 |
| `test_second_apply_idempotent` | 二次 apply：新建 0、出处 0、行数不变、清单 kp_id 映射不变 |
| `test_manifest_lost_still_idempotent` | 删掉 meta 清单后再 apply：靠「名称+摘要一致」认领，新建 0 |
| `test_doc_id_filter` | --doc-id 只导指定课件（3 条） |
| `test_v1_artifact_layout` | 旧 v1 产物（`path` 而非 `node_path`、无 material_block_ids）也能导入 2 条，`sources.chunk_ids` 落出处 |

运行结果：

```
venv\Scripts\python.exe -X utf8 tests\run_all.py --only test_09
-> 用例：6 条 · 通过 6 · 失败 0；退出码 0（输出存 test09-run.txt）
py_compile tools/import_kp_artifacts.py tests/test_09_import_artifacts.py -> exit 0
```

**全量 `tests/run_all.py`**：本机同时有 2 个别的施工智能体在跑 `run_all.py`
（`Get-CimInstance Win32_Process` 可见 2 个并发进程），而 `tests/env.py` 的隔离
数据目录 `_work/verify/data/` 是**全进程共享**、没有锁 —— 并发跑会互相删/占
`main.db`、`deck.db`。前几次全量尝试（`full-suite-run2/3/4.txt`）都撞上共享数据
目录争用：test_04/06/07/08 的 `setUpModule`/CLI 用例因文件被占或其他智能体正在改
的 dashboard/search 失败。为此本模块已改为**私有数据目录**（不再调
`env.prepare()`，只从 `env.FIXTURE_DIR` 只读复制），不再受共享目录争用影响。

**最新一次全量（`full-suite-run5.txt`）：93 条 / 通过 93 / 失败 0 / 退出码 0**，
其中 `test_09_import_artifacts` 6 条全 PASS（与其他智能体新增的 test_08 等共存）。

---

## 8. 风险与已知限制

1. **2 条无出处知识点**（`had_sources=0`）：产物自身没有覆盖页/材料块
   （如「课件封面」）。它们能建卡、能复习，但看板「真删课件」的孤儿清理只清
   `had_sources=1` 的，所以这类卡不会被自动带走 —— 量小（2/256），建议 M0-6
   或数据清理任务里补一条「无出处且产物已不在的卡」清理规则。
2. **名称加了父级前缀的 7 条**：跨课件重名只能靠前缀区分（库约束 name 全局唯一），
   名称比单节点名长（如 `抗胆碱酯酶药和胆碱酶复活药 · 课程导入`）。这是对
   `UNIQUE(name)` 的妥协；若未来允许改 schema，建议加 `doc_id`/`node_path` 列
   替代「名称即身份」。
3. **摘要较长**：summary = 理解 + 要点拼接（平均 300~600 字），知识点列表每行会
   显示整段 summary（`viewKps` 的 `<div class="small muted">`）。M0-6 做文案/空态
   清理时可考虑列表只显示第一句、详情看全文（本任务按约束不改 dashboard）。
4. **清单依赖**：幂等主依据是 meta 清单；清单被人为清掉时靠「名称+摘要全等」
   认领（已测）。若之后产物重新生成、摘要变了且清单也丢了，会走「名称冲突→跳过」，
   宁可少导不覆盖用户数据。
5. **`subject` 用章节名**：根库没有学科概念，`api_kps` 的「科目」列显示
   `meta.chapter`（如「1中枢神经系统药理学概论」）作为分组线索；不是 bug。
6. **不处理 `terms`**：名词解释是独立功能（C-0058 已摘出），本脚本不导入。
7. 真实库应用顺序建议：`tools/backup_data.py` → `--dry-run` 看统计 →
   `--apply` → 再跑一次确认新建 0 → 刷新看板。

---

## 9. 建议：dashboard 一键入口怎么接（Lead 后续做 UI）

**目标**：知识点页「批量整理知识点」卡片旁加一个「导入整理产物到知识点」按钮
（0 费用、可重复点）。推荐接法（按实施成本排序）：

1. **最省事（推荐给 M0 收口）**：把本脚本的 `run(args)` 逻辑原样抽到
   `core/kp_import.py`（它只依赖 `argparse.Namespace` 的
   `db/artifacts_dir/apply/doc_id/max_sources_per_kp` 五个字段，已按可复用写）。
   dashboard 加只读接口 `GET /api/kp-import/preview`（dry-run 统计）与
   写接口 `POST /api/kp-import/apply`（内部调 `run()`，返回 JSON 统计）。
   优点：不依赖子进程，**打包成 exe 也能用**；前端点一次出「将建 256 / 已存在 0」。
2. 过渡方案：dashboard 用 `subprocess` 调 `sys.executable -X utf8 tools/import_kp_artifacts.py --apply --json`。
   源码模式可用；exe 分发时 `sys.executable` 是看板 exe，可行性需另测，故只作过渡。
3. 入口文案建议：「把已整理好的知识点导入复习（0 费用）」+ 空态里也放一个；
   点击后先弹 dry-run 统计（`将新建 N / 已存在 N`），确认再 apply，避免误以为重复建卡。
   与 M0-2「＋新建知识点」、M0-4 错题闭环同属「写入闭环」入口组。

**测试矩阵行建议**（我们不动 `tests/matrix.py`，请 Lead/verifier 加）：

```python
dict(id="F96", feature="整理产物导入知识点表（M0-3，零 AI、幂等）",
     entry="CLI tools/import_kp_artifacts.py（dry-run/--apply）；写后 GET /api/kps",
     kind="write", priority="P0",
     tests=["test_dry_run_does_not_write", "test_apply_writes_and_api_visible",
            "test_second_apply_idempotent", "test_manifest_lost_still_idempotent",
            "test_doc_id_filter"],
     evidence="json"),
```

---

## 10. 证据文件清单（`_work/m0/import/`）

| 文件 | 内容 |
|---|---|
| `real-copy.db` | 真实主库只读快照 + 已 apply 两次（256 KP / 725 出处），可复跑 |
| `evidence-dryrun.json` / `evidence-apply1.json` / `evidence-apply2.json` | 三次 `--json` 原始输出 |
| `db-stats.json` | 导入后计数、重名/孤儿、dashboard/compute_mastery 抽查 |
| `source-origin.json` | 出处来源（显式材料块 253 / 页兜底 1 / 无 2）与每份课件统计 |
| `test09-run.txt` | `run_all.py --only test_09` 输出（6/6 通过） |
| `full-suite-run2.txt` … `run5` | 全量回归输出（run2–4 含与他人并发跑套件、共享 `_work/verify/data` 冲突的证据；run5 是测试模块改私有目录后的证据） |
