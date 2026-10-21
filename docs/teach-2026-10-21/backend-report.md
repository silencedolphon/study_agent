# 教材补充后端报告（C-0124：按章节匹配 + 每课件补充产物 + 整理完成回调）

> 任务：task-26 · 施工智能体 B（artifact-importer）· 2026-10-21
> 用户需求：知识点窗口右侧显示教材对应页；**以知识点总结为基础判断教材是否有可补充内容**；
> 教材内容**必须按章节切块的库**里找、**不允许整本扫描**；知识点整理时触发；无教材走原管线；
> 之后教材入库 → 在课件思维导图栏提醒"补做教材补充"，**只补教材、不动原知识点**。
> 前端侧栏/提醒由前端智能体并行改 dashboard（本任务**不碰 dashboard**）。

## 1. 结论

- `core/teach.py` 新增一套**零模型**教材补充后端（旧 `textbook_passages`/`kp_textbook` **一个字未改**）：
  按 `heading_path` 分组出章候选 → 每条 KP 只匹配**一章** → **只 SELECT 该章块**做段落打分 →
  每课件写一个 `logs/knowledge/<doc_id>-教材补充.json`；`supplement_status` 给"要不要提醒补做"；
  `supplement_for_kp` 给侧栏读取。0 次模型调用、0 次 DB 写、原子写、幂等。
- `core/kp_build.py::start_background` 只加了一个**可选** `on_done` 回调（整理产物落盘后调用，
  异常吞掉）；不传时行为逐字不变。
- 真实沙箱（真实库副本 + doc46 真实 KP 产物）：**22 条 KP → 22 条匹配到章/段落**，
  42 个章候选，耗时 **0.41s**，产物 153KB；二次 build `skipped`、force 才重写；
  真实 DB 与真实 KP 产物 sha256 前后一致（0 写入、0 模型）。
- 单测 `tests/test_19_textbook_supplement.py` 8/8；全量回归见 §7。

## 2. API 契约（前端按此对接）

### `teach.build_supplement(doc_id, *, out_path=None, force=False, db_path=None, conn=None, log=print) -> dict`

- 读 `kp_build.out_file(doc_id)` 的知识点产物（只读；无产物 → `{ok:false, status:"no_kps"}`，不写文件）。
- **无教材** → 写 `status="no_textbook"` 状态文件（`meta` + 空 `kps`）并返回（供后续提醒判断）。
- 有教材 → 逐条 KP：`_match_chapter_for_kp` 只挑**一章**（0 分 → `no_match`，**不回退整本扫描**）；
  `_chapter_passages` **只查该章**块做词项 IDF 打分（≤3 段）。
- 每条产物结构：
  ```json
  {"kp_id":"KP-N07-1","kp_name":"…","status":"done|no_match",
   "chapter":{"heading_path":"… > 第6章 作用于胆碱受体药","name":"第6章 …","no":6,
              "score":12.3,"hit_terms":["胆碱受体",…]},
   "pages":[70,72],"page_label":"p.70、p.72",
   "passages":[{"chunk_id":123,"page_start":70,"page_end":72,"page_label":"p.70–p.72",
                "text":"…","score":8.7,"hit_terms":["阿托品",…]}],
   "added_terms":["东莨菪碱",…],"note":"人话说明"}
  ```
- `meta`：`version / doc_id / doc_title / status(done|no_match|no_textbook|failed) /
  textbook_doc_id / textbook_title / kp_artifact / kp_artifact_mtime / kp_artifact_sha256 /
  generated_at / created_at / n_kps / n_matched / n_no_match / textbook_chapters / note`。
- **幂等**：非 force 且已有产物、`version`/`textbook_doc_id`/KP 产物 mtime 都没变 → `{ok:true,status:"skipped"}`（0 写）。
- **原子写**（临时文件 + `os.replace`）；**不写数据库、不调模型**。

### `teach.supplement_status(doc_id, *, db_path=None, conn=None) -> dict`

```json
{"ok":true,"doc_id":46,"has_artifact":true,"artifact_mtime":1761…,
 "has_textbook":true,"textbooks":[{"doc_id":50,"title":"药理学 第6版 刘晓东",…}],
 "supplement":{"exists":true,"status":"done","generated_at":"…","n_kps":22,
               "n_matched":22,"textbook_doc_id":50,"version":"C-0124-teach-supplement-v1"},
 "needs_backfill":false,"reason":"教材补充已是最新"}
```

`needs_backfill`：有 KP 产物 + 有教材 +（无补充产物 / 产物是 `no_textbook` / `failed` /
比 KP 产物旧 / 教材换了一本）→ True；`no_match` 不算 needs（已补过，只是没命中）。
无教材/无 KP 产物时给可操作 `reason`。**前端提醒直接读 `needs_backfill` + `reason`。**

### `teach.supplement_for_kp(doc_id, kp_id) -> dict`

```json
{"ok":true,"available":true,"textbook":{"doc_id":50,"title":"…"},
 "entry":{…上面每条 KP 的对象…},"note":"…"}
```
`available=false` 表示这条 KP 没有可展示的教材补充（无产物/该 KP 不在产物里/no_match），
`note` 给人话。**不自动生成**（生成由 `build_supplement` 负责）。

### `teach.supplement_out_file(doc_id)` / 常量

`SUPPLEMENT_VERSION="C-0124-teach-supplement-v1"`；产物
`<当前学科 logs>/knowledge/<doc_id>-教材补充.json`（`-教材补充.json` 不与知识点/名词解释/预习产物撞后缀）。

## 3. 改动文件:行

| 文件 | 改动 |
|---|---|
| `core/teach.py` | 顶部 import 加 `hashlib/json/os`；`:879` 起新增 **522 行**（追加，旧函数零改动）：`SUPPLEMENT_VERSION`、`supplement_out_file`、`_fmt_pages/_page_span_label`、`_chapter_candidates`（只读 heading_path 分组）、`_match_chapter_for_kp`（章名打分/0 分 None）、`_chapter_passages`（**只查选中章**）、`_supplement_added_terms`、`_atomic_write_json`、`_open_db`、`_doc_title`、`build_supplement`、`supplement_status`、`supplement_for_kp`、`_load_supplement`。 |
| `core/kp_build.py` | `start_background`（`:4599`）加可选 `on_done`：`:4620` 从 kw 弹出、`:4625-4629` build 成功且 `out_file(doc_id)` 存在时调用、异常吞掉。其余未动。 |
| `tests/test_19_textbook_supplement.py` | 新增 8 条用例（见 §6）。 |
| `tests/matrix.py` | **未改**（按分工由 Lead 加行，建议见 §8）。 |

**"不允许整本扫描"的实现证据**：`_chapter_passages` 的 SQL 固定为
`SELECT … FROM chunks WHERE doc_id=? AND heading_path=? ORDER BY ord`；
`_chapter_candidates` 只 `GROUP BY heading_path`（不读块正文）。单测用 SQL 代理断言：
出现了 `heading_path=?` 查询，且**没有**旧 `textbook_passages` 那种整本 `WHERE doc_id=? ORDER BY ord` 全扫描。

## 4. 章匹配与打分口径（可解释、零模型）

1. 候选词 = `_kp_query_text(kp)`（名称/要点/摘要）+ 课件标题词项（`_terms()` 切窗口，含停用词表）。
2. 对**章名**打分：`w=(词长-1)×log((K+1)/(df+0.5))`，`K`=章数，`df`=该词出现在几个章名里；
   过于普遍的词（>50% 章名）跳过；只来自课件标题的词 ×0.5 降权。
3. 章得分 = Σ命中词权重；知识点文本点名"第N章"且章号一致 **+3.0**；
   知识点名称与章名 ≥3 字词重合 ×0.8。
4. 取最高分章；**最高分为 0 / 无命中 → None**（`no_match`，绝不回退整本扫描）。
5. 章内段落：同章块做词项 IDF 打分，相对截断 0.4、每块一词只计一次、`_clip` 截 1200 字，
   取 ≤3 段；`added_terms` = 教材段落里出现、而 KP 文本没有的长词（≤8），供侧栏"可补充"。

## 5. 真实沙箱验证（0 模型 / 0 真实写）

脚本 `_work/teach-supplement/verify_sandbox.py`（结果 `real-verify.json`）：
真实 `index/study.db` backukp 副本 + 真实 `logs/knowledge/46-末级知识点总结.json` 副本，
`KB_DB/KB_LOG` 全指 `_work/teach-supplement/sandbox/` 后调用新 API。

| 指标 | 结果 |
|---|---|
| 教材 | doc#50《药理学 第6版 刘晓东》，`role=textbook`、42 章候选 |
| doc46 KP 数 | **22**（真实产物） |
| 匹配结果 | **22 条 done / 0 条 no_match**；0 次模型调用 |
| 章节分布 | 第6章 7 · 第3章 3 · 第5章 2 · 第10章 2 · 第16章 2 · 第19章 2 · 第34/9/24/32 章各 1 |
| 每章块数（证据） | 只读所选章：第6章 24 块、第5章 15 块、第3章 27 块…（`chapter_blocks` 记录了分母） |
| 耗时 | **0.406s**（22 条 KP、42 章候选） |
| 产物 | `sandbox/logs/knowledge/46-教材补充.json`，**153 KB**、22 个 KP 键 |
| 幂等 | 二次 build = `skipped`；force = `done` 重写 |
| 只读边界 | 真实 DB sha256 前后一致、真实 KP 产物 sha256 前后一致、真实 `logs/knowledge` 无 `*-教材补充.json` |

侧栏样例（`real-verify.json.matched_sample`）：`KP-N10-1 临床应用` → 第6章 作用于胆碱受体药，
p.70–p.72，2 段，`added_terms` 含教材里出现而 KP 没写的词（≤8）。

## 6. 单测（`tests/test_19_textbook_supplement.py`，8/8）

| 用例 | 断言 |
|---|---|
| `test_chapter_scope_no_whole_book_scan` | 临时库两章（A 胆碱受体药 / B 抗高血压药）；KP 只命中 A → passages 全来自 A 章、页码 70–74；B 章块永不出现；SQL 代理断言有 `heading_path=?`、无整本全扫描 |
| `test_no_textbook_then_needs_backfill` | 无教材 → 写 `no_textbook` 状态文件、`has_textbook=False/needs_backfill=False`；教材入库后 → `needs_backfill=True` 且 reason 含"补做"；补做后 `needs_backfill=False` |
| `test_done_for_kp_and_kp_artifact_untouched` | status done；`supplement_for_kp` 可读（textbook/entry/added_terms≤8）；**KP 产物字节 hash 前后一致**；无 `.tmp` 残留 |
| `test_idempotent_and_force` | 二次 `skipped` 且文件 mtime 不变；force `done`；KP 产物 mtime 变化后不再 skipped |
| `test_no_kps_no_write` | 无 KP 产物 → `no_kps`，不写补充文件 |
| `test_match_zero_returns_none` | 0 分 / 空章列表 → `None`；命中 → 第6章且 score>0 |
| `test_on_done_called_after_success` | `start_background(..., on_done=cb)`：整理成功后回调 `cb(doc_id)` |
| `test_on_done_exception_swallowed_and_not_called_without_artifact` | 回调抛异常不影响线程收尾；无产物不回调；不传 on_done 行为不变 |

- `run_all.py --only test_19`：**8/8 通过、退出码 0**（`_work/teach-supplement/test19-runall.txt`）。
- `py_compile core/teach.py core/kp_build.py tests/test_19_textbook_supplement.py` exit 0。

## 7. 全量回归

**全量 `tests/run_all.py`：133 条 / 通过 133 / 失败 0 / 退出码 0**（`_work/teach-supplement/full-suite.txt`；
原 125 条 + 本任务 test_19 的 8 条 = 133 条；矩阵 75 行全覆盖）。

## 8. 建议测试矩阵行（Lead 改 `tests/matrix.py`）

当前矩阵最大编号 F110 → 建议：

```python
dict(id="F111", feature="教材补充（按章匹配 + 每课件产物 + 课后提醒 + 整理完成回调）",
     entry="core/teach.py::build_supplement / supplement_status / supplement_for_kp；"
           "core/kp_build.py::start_background(on_done)",
     kind="read", priority="P1",
     tests=["test_chapter_scope_no_whole_book_scan",
            "test_no_textbook_then_needs_backfill",
            "test_done_for_kp_and_kp_artifact_untouched",
            "test_idempotent_and_force",
            "test_no_kps_no_write",
            "test_match_zero_returns_none",
            "test_on_done_called_after_success",
            "test_on_done_exception_swallowed_and_not_called_without_artifact"],
     evidence="json"),
```

## 9. 前端接法建议（Lead/前端参考，本任务不改 dashboard）

1. 知识点侧栏：`GET /api/teach-supplement?doc_id=<课件id>&kp_id=<kp_id>` →
   `teach.supplement_for_kp(doc_id, kp_id)`，`available=false` 时显示一句话空态。
2. 课件思维导图栏提醒：`GET /api/teach-supplement/status?doc_id=<课件id>` →
   `teach.supplement_status(doc_id)`，`needs_backfill=true` 时显示「补做教材补充」按钮（零费用）。
3. 补做按钮：`POST /api/teach-supplement/build`（body: doc_id, force 可选）→
   `teach.build_supplement(doc_id)`；建议按钮旁边明写"0 次模型调用、不改原知识点"。
4. 整理时自动触发：`api_kp_build_start` 调 `kp_build.start_background(doc_id, on_done=lambda d: teach.build_supplement(d, log=lambda *a: None))`
   ——回调失败不影响整理；无教材时它只写 `no_textbook` 状态文件（后续教材入库即可提醒补做）。

## 10. 风险 / 已知限制

1. **章匹配是词项启发式**：真实沙箱 22/22 全命中（doc46 内容几乎都能落到药理学章），
   但弱命中可能出现"配到相邻章"；产物里带 `chapter.score/hit_terms` 与 `note`（"可补充内容仅供参考"），
   侧栏只做定位、不替用户下结论。0 分一律 no_match，**绝不整本扫描兜底**。
2. **教材判定沿用现有口径**：`role='textbook' AND kind='pdf'`（`pick_textbook`）；
   没有章级 `heading_path`（整本只有书名一段）的教材 → 章候选为空 → 全部 no_match，
   属于"未按章节切块"的如实降级（不整本扫）。
3. **needs_backfill 依赖 mtime**：KP 产物重写后 mtime 变化即提醒补做；同一秒内重写且内容变化理论上可能漏判（极小概率，可 force 重做）。
4. **产物路径随当前学科 `LOG_DIR`**：与预习/知识点产物同目录；切学科后旧产物不自动迁移（沿用项目既有口径）。
5. 旧 `textbook_passages/kp_textbook` 保持原行为（仍会整本扫描），新流程不使用；若后续要下线旧接口需单独评审。
6. 本任务未 commit；真实数据/8756 未动；未调用任何模型/网络。

## 11. 证据文件（`_work/teach-supplement/`）

| 文件 | 内容 |
|---|---|
| `real-verify.json` | doc46 真实沙箱全量数字（22 KP/22 matched/章节分布/耗时/只读边界/幂等） |
| `verify_sandbox.py` | 可重跑的真实副本验证脚本（只写沙箱） |
| `test19-runall.txt` | `run_all.py --only test_19`（8/8、退出码 0） |
| `full-suite.txt` | 全量回归输出 |
| `sandbox/` | 沙箱 DB 副本 + 真实 KP 产物副本 + 生成的 `46-教材补充.json` |
| `supplement_block.py` | 追加到 `core/teach.py` 的代码块（便于 review/回溯） |
