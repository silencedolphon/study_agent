# 复习自测 v3 + 预习名词驱动名词整理（C-0128 / C-0129，task-35）

> 任务：task-35 · 施工智能体 B（artifact-importer）· 2026-10-24
> 用户新要求（复习板块改造）：①自测不挖空，改展示"从次一级节点到该 KP"的结构路径 + 已掌握/未掌握；
> ②三步复习每步可跳过（前端）；③名词整理参考预习 `key_terms` 高质量做法、减少无关名词；
> ④页签切换弹窗修复（前端）；⑤化学结构式（另一智能体）。**本任务零 AI、不碰 dashboard。**

## 1. 结论

- `core/review.py` 升 **`C-0128-review-v3`**：自测不再挖空，每个抽中 KP 返回
  `{kp_id,name,node_path,branch,summary,unfamiliar}`（`branch` = 父级→自身的结构路径，供展开看总结）；
  `submit_selftest(doc_id,session_id,results={kp_id:bool})` 直接收「已掌握/未掌握」，
  熟悉度=已掌握/抽样；持久化 v3 `{meta,kps:{kp_id:{mastered}}}`，只保留本次抽样；
  旧 v1/v2（熟悉度/挖空时代）文件 legacy 兼容（familiar→mastered 推算，不崩）。
- 新增 `core/terms_preview.py`（**零 AI**）：课件→教材章 → 读教材预习 `terms`（key_terms 同源）
  → 用**该课件语料**（KP name/points/summary + chunks）做相关度过滤（原文命中 / n-gram 覆盖率
  ≥0.60 / 本地 embed cosine ≥0.62）→ 只保留相关词，写 `logs/knowledge/<doc>-名词解释.json`
  （旧 schema 兼容、`meta.source="preview_key_terms"`、`calls=0/cost=0`、覆盖前 `.bak`）。
  无预习产物 → `{ok:false,source:"none"}`，**不自动调旧 AI 管线**；旧 `kp_build.build_terms` 未动。
- 真实沙箱（0 AI/0 真实写，见 §6）：doc46 自测 **22→11**、items 无挖空、全掌握 familiarity=1.0、
  一个未掌握 0.9091；flow 22 KP/5 篇笔记/第6章预习名词 11；
  名词过滤 **候选 11 → 保留 10 / 剔除 1（调节痉挛）**，1.47s（本地 embed），0 模型调用、0 DB 写。

## 2. 前端契约（review v3；dashboard 由 dash-fixer 改）

| 路由 | 后端调用 | 关键返回 |
|---|---|---|
| `GET /api/review/coursewares` | `list_courseware_reviews(conn)`（只读 conn，锁内） | `{ok,items:[{doc_id,title,n_kps,selftest:{exists,familiarity,n_sample,n_mastered,n_unmastered,updated_at,legacy},terms:{available,n,source},chapter:{no,name}}]}` |
| `GET /api/review/flow?doc_id=` | `build_flow(conn, doc_id)`（只读 conn，锁内） | `{ok,doc,kps:[{kp_id,name,summary,points,pages,node_path,note_count,mastered,unfamiliar}],notes,terms,selftest,stages}` |
| `GET /api/review/selftest?doc_id=` | `start_selftest(doc_id, db_path=None)`（无需 conn，锁外） | `{ok,doc_id,title,session_id,n_total,n_sample,ttl_seconds,kps:[{kp_id,name,unfamiliar}],items:[{kp_id,name,node_path:[str],branch:[{name,level}],summary,unfamiliar}],note}`；**v3 无 segments/answers** |
| `POST /api/review/selftest/submit` | `submit_selftest(doc_id,session_id,results)`（锁外，原子写） | body `{"doc_id":46,"session_id":"…","results":{"KP-1":true,"KP-2":false}}`（也接受已掌握 id 数组）；返回 `{ok,familiarity,n_sample,n_total,n_mastered,n_unmastered,unfamiliar_ids,results:[{kp_id,mastered,familiar}]}`；未提交的抽样 KP 按未掌握 |
| `GET /api/review/selftest/status?doc_id=` | `selftest_status(doc_id)`（锁外，纯读） | `{ok,exists,familiarity,n_sample,n_total,n_mastered,n_unmastered,unfamiliar_ids,updated_at,legacy,kps:{kp_id:{mastered,familiar}}}` |

- 错误：函数返回 `{"ok":false,"error":"人话","_code":400}`，路由 `_json_auto` → HTTP 400 JSON（不 500）。
- 过渡别名：`n_familiar`/`n_unfamiliar`/`familiar` 仍返回（= mastered 口径），前端旧代码不至于崩；
  **新 UI 建议统一用 `mastered/n_mastered/n_unmastered`**。
- session 仍在进程内存（TTL 30min）；服务重启需重新开始自测（持久化结果不丢）。
- 前端三步复习：`stages` 给 `{key:kps|notes|terms,title,n,done?}`，每步"跳过"只是前端状态，后端已给全数据。

## 3. 名词整理契约（`core/terms_preview.py`，建议 dash-fixer 加 2 条路由）

| 路由（建议） | 后端调用 | 说明 |
|---|---|---|
| `GET /api/terms/preview/status?doc_id=` | `terms_preview_status(doc_id, db_path=None, out_path=None, conn=None)` | `{ok,source,chapter,preview:{exists,artifact,n_candidates,textbook_doc_id},current:{exists,is_current,source,n_terms,generated_at,path},can_rebuild,calls:0,cost_cny:0}` |
| `POST /api/terms/preview/build` | `build_terms_from_preview(doc_id, *, db_path=None, out_path=None, force=False)` | body `{doc_id, force?}`；产物同上；**0 AI/0 成本**；返回 `{ok,status,source,n_candidates,n_selected,n_dropped,selected:[{term,first_kp_id,match}],dropped:[term],out_path,backup,embed_used,calls:0,cost_cny:0,elapsed_s}` |

### 过滤算法（`filter` 版本 `C-0129-terms-filter-v1`）

1. 候选 = 教材预习产物 `<教材id>-预习总结-第<章>.json` 的 `terms`（与预习页签 key_terms 同源；
   预习已做过"必须出自本章材料"过滤）。
2. 语料 = 本课件 KP 的 `name/points/summary` + 该课件 `chunks.text`（只读 SQLite，≤40 万字符防极端）。
3. 判定（按顺序）：
   - `term` 原文出现在语料 → 保留（`match="exact"`）；
   - 否则字符 2–3-gram 覆盖率 ≥ **0.60** → 保留（`match="ngram"`）；
   - 否则本地嵌入 `embed_query(term+def)` 与该课件每条 KP 文本的 **max cosine ≥ 0.62** → 保留（`embed`）；
   - 都不满足 → **剔除**（可以为 0 个，`status="no_relevant_terms"`）。
   模型不可用/加载失败时只看前两条（不报错、不硬凑）。
4. 每条选中词：`first_kp_id/first_kp_name` = 相关度最高的 KP（无则空），
   `kp_ids` = 有字面/ngram 命中的 KP 列表；保留预习的 `en/def/why`。
5. 产物字段兼容旧 schema：`term/en/en_source/def/first_page/first_kp_id/first_kp_name/
   kp_ids/n_occurrences/evidence/seg_id/seg_name/page_from_model` + `source/match/match_detail`；
   `meta`：`pipeline/pipeline_version/source="preview_key_terms"/filter_version/doc_id/deck/
   chapter/textbook_doc_id/textbook_title/preview_artifact/preview_sha256/generated_at/
   n_candidates/n_selected/n_dropped/filter/embed/dropped_terms/terms_fields/
   usage_total:{calls:0,...}/cost_cny:0.0/price_rule/prev_backup?`。
6. 覆盖旧产物前自动 `.bak-<ts>`；同版本+同预习 sha+同章再跑 → `skipped`（0 写）；`force=True` 重写。

## 4. 改动文件:行

| 文件 | 改动 |
|---|---|
| `core/review.py` | 升 `C-0128-review-v3`（572 行）：`_branch_of/_make_item`（结构路径题面）；`start_selftest` 去挖空；`_selftest_summary/selftest_status` mastered 口径 + v1/v2 legacy；`submit_selftest` 收 `{kp_id:bool}`、v3 持久化；`build_flow` kps 加 `mastered/unfamiliar`、只标本次未掌握；`list_courseware_reviews` 字段同步。 |
| `core/terms_preview.py` | **新增**（~460 行）：章定位（supplement map→本地 `map_courseware_chapter(allow_ai=False)`）、预习词读取、`_ngram_coverage/_embed_scores/_kp_relevance` 过滤、`build_terms_from_preview`、`terms_preview_status`；备份/幂等/原子写；0 AI。 |
| `tests/test_26_review_v3.py` | **新增 4 条**：22→11 题面契约、results 多种写法、旧 v2 legacy→v3 升级、flow/status mastered 自洽。 |
| `tests/test_27_terms_preview.py` | **新增 8 条**：过滤保留/剔除、first_kp、旧 schema、无预习/无章/无 KP 错误、备份+幂等+force、status、0 AI（mock chat 调用会炸）。 |
| `tests/test_23_review_flow.py` | 按 v3 契约更新（5 条：题面/提交/会话边界/旧 v1 兼容/notes+terms+列表）。 |
| `tools/dashboard.py` | **未改**（路由/前端由 dash-fixer 统一改；本报告 §2/§3 给契约） |
| `tests/matrix.py`、其它 core | 未改（矩阵建议见 §8）。 |

**零 AI / 零 DB 写证据**：`terms_preview` 全程只读 SQLite；单测 mock `chat.chat_once`/`kp_build.build_terms`
（被调用即 AssertionError）；沙箱 DB sha256 前后一致；仅写沙箱产物文件。

## 5. 测试

- `run_all.py --only test_23`：**5/5**；`--only test_26`：**4/4**；`--only test_27`：**8/8**（均退出码 0）。
- 单测覆盖：
  - v3 题面：每 item 恰好 6 键、无 `segments/answers`；`branch[-1].name == name`、level 连续；
    `node_path` 来自 KP 产物；22→11 不放回；开始不写盘。
  - 提交：`{kp_id: bool/str/0/1/已掌握}` 与 id 数组两种写法；缺失按未掌握；非法类型报错；
    familiarity=已掌握/抽样；v3 文件只含本次 11 条且与 meta 自洽；重复/伪造/过期 session 报错。
  - legacy：v1/v2 文件（`familiar` 键）→ status 按 mastered 现算 + `legacy=true`；
    新提交升级 v3 且 `legacy=false`。
  - flow：`mastered/unfamiliar` 与 status 一致；未抽中的 KP 不标 unfamiliar；notes 分组/terms/stages 保留。
  - 名词：原文命中/嵌入兜底/无关剔除；首条 KP 关联；旧 schema 字段；无预习 → `ok:false source:none` 不写盘；
    覆盖前 `.bak`；幂等 skipped/force 重写；status 的 `can_rebuild`；0 AI 调用。

## 6. 真实沙箱（`_work/review-v3/real-verify.json`）

| 项 | 结果 |
|---|---|
| 自测 | doc46 22 条 → **抽 11**（0.080s）；items **无 answers/segments**；样例 branch=`M胆碱受体阻断药 > 东莨菪碱 > 中枢抑制与抗晕动` |
| 全掌握提交 | **familiarity=1.0**、n_mastered=11、n_unmastered=0 |
| 一个未掌握 | **familiarity=0.9091**、n_mastered=10、unfamiliar_ids=[KP-N29-1]；status 同口径、legacy=false |
| flow | 22 KP、**10 mastered / 1 unfamiliar**、5 篇笔记全部按 KP 分组、terms=第6章预习 11 词（0.124s） |
| 名词过滤 | 候选 **11 → 保留 10 / 剔除 1（调节痉挛）**；保留里 exact/ngram/embed 三种命中都有；1.47s（本地 embed，首次加载模型）；`embed_used=true`；**calls=0 / cost=0 / DB 未写** |
| 产物 | `<sandbox>/logs/knowledge/46-名词解释.json`：meta `source=preview_key_terms`、version C-0129、`usage_total.calls=0`、`cost_cny=0`；10 词 |
| 只读边界 | 真实 DB、doc46 KP 产物、doc50 第6章预习产物 sha256 前后一致；真实 `logs/review` 仍不存在；真实 `<46>-名词解释.json` 仍不存在；0 模型调用 |

## 7. 全量回归

- 后端相关三套：`--only test_23` 5/5、`--only test_26` 4/4、`--only test_27` 8/8（均退出码 0）。
- 全量 `tests/run_all.py`（`full-suite.txt`）：**200 条 / 通过 195 / 失败 5**，5 条**全部**在
  `test_24_review_ui`（前端复习 UI 用例仍断言旧挖空契约如 `reviewSegmentsHtml`/segments，
  如 `TestReviewRoutes.test_answer_hidden_from_initial_html`、`TestSelftest.test_selftest_ui` 等）——
  这正是"字段改动影响前端 test_24，由前端同步"的预期情况；dash-fixer 已收到 v3 契约（含
  items 无 segments、submit results、status mastered 字段），由其随前端同步后由 Lead 串行收口。
  本任务后端三套与真实沙箱全绿，**未改 dashboard/前端测试**。

## 7.1 隔离实例真实 HTTP 验收（dash-fixer 路由落地后）

做法：独立进程把 `KB_*` 全指 `_work/review-v3/sandbox`，patch `subjects.restore` 阻止切回真实库，
起真实 `ThreadingHTTPServer(127.0.0.1:8899)`（Handler/路由 = dash-fixer 落地代码），逐条打接口；
结果 `http-verify.json`。**全程 0 模型调用、真实 `logs/review` 与真实 `46-名词解释.json` 始终不存在。**

| 接口 | HTTP | 结果 |
|---|---|---|
| `GET /api/review/coursewares` | 200 | ok=true、2 张卡（doc46 n_kps=22 selftest exists）；字段含 n_mastered/n_unmastered/legacy |
| `GET /api/review/flow?doc_id=46` | 200 | ok=true、22 KP、mastered/unfamiliar、kps 带 node_path；notes 5、terms preview_chapter 11、stages 3 步 |
| `GET /api/review/selftest?doc_id=46` | 200 | ok=true、n_total=22/n_sample=11；items 11 条，键恰为 `branch/kp_id/name/node_path/summary/unfamiliar`，**无 segments/answers** |
| `POST /api/review/selftest/submit`（全已掌握） | 200 | familiarity=1.0、n_mastered=11、n_unmastered=0 |
| `GET /api/review/selftest/status` | 200 | familiarity=1.0、n_mastered=11、n_unmastered=0、unfamiliar_ids=[]、legacy=false |
| 错误分支 | 200/400 | flow doc0→200 ok:false「缺少 doc_id」；selftest doc999999→400；伪造 session→400；results 非对象→200 ok:false（均 JSON，无 500） |
| `GET /api/terms/preview/options` / `/status` | 200 | 均 ok=true、source=preview_key_terms、第6章、候选 11、can_rebuild=false；两套命名结果一致 |
| `POST /api/terms/build-preview` / `/preview/build` | 200 | 均 ok=true、候选 11 → 保留 10 / 剔除 1（调节痉挛）、calls=0/cost=0、有 `.bak`；两套命名结果一致 |
| `GET /api/terms?doc_id=46` | 200 | 10 条；`terms_source=own_file`（独立产物优先）、`terms_pipeline_version=C-0129-terms-preview-v1`；产物 meta `source=preview_key_terms`、`usage_total.calls=0`、`cost_cny=0` |
| doc40（无预习） | 200 | options ok=true、source=none、preview.exists=false、can_rebuild=false；build ok:false +「没有可用的预习 key_terms，未生成（也不会自动调 AI）」人话（无 500、无写入） |

结论：**5 条 review 路由与 4 条名词路由全部验收通过**（字段/状态码/锁外写行为与契约一致）；
沙箱只写 `logs/review/46-自测.json` 与 `logs/knowledge/46-名词解释.json`，真实库/真实产物未动。

## 8. 矩阵建议

当前矩阵最大编号 F118 → 建议两行（F119/F120）：
- **F119** 复习自测 v3（结构路径 + 已掌握/未掌握）：`core/review.py::start_selftest/submit_selftest/`
  `selftest_status/build_flow`，tests 取 `test_23_review_flow` 5 条 + `test_26_review_v3` 4 条。
- **F120** 预习名词驱动名词整理（零 AI）：`core/terms_preview.py::build_terms_from_preview/`
  `terms_preview_status`，tests 取 `test_27_terms_preview` 8 条。

## 9. 风险 / 已知限制

1. **"结构路径"依赖 KP 产物的 `node_path`**：老产物没有/为空时 `branch` 退化为 `[自身]`（不崩、不编造）。
2. **已掌握/未掌握是用户自评**：不再有客观判分，熟悉度完全取决于点击；抽样一半意味着每次只更新本次
   抽中的 KP（`legacy` 旧文件另算），未抽中的保持"无标记"（flow 里 `unfamiliar=false`，前端可用
   `selftest.kps` 是否有该 id 区分"未抽中"与"已掌握"）。
3. **名词过滤阈值是经验值**：`ngram≥0.60 / embed≥0.62`。真实 doc46 只有"调节痉挛"被剔除（该词确实
   不在课件语料，embed 也未达阈值）；若某课件与教材章主题高度相关，保留数可能接近候选数，
   这是"同章预习词本来就相关"的合理结果，不是过滤失效（`match` 字段可见依据）。
4. **嵌入模型不可用时**自动退回"原文+ngram"两条规则（不报错、不硬凑）；模型文件路径见
   `config.MODEL_DIR`，首次加载约 1s，之后进程内单例复用。
5. **预习产物覆盖范围**：真实库目前只有第 5/6/9 章预习；其它章调用返回 `ok:false source:none`
   （前端显示"先做该章预习"，不要自动跑旧 AI 管线）。
6. 旧 V1/V2 自测文件保留原样，只读兼容；第一次提交新结果后升级 v3。
7. 未 commit；真实数据/8756 未动；测试/沙箱 0 真实 AI、0 真实写。

## 10. 证据文件（`_work/review-v3/`）

| 文件 | 内容 |
|---|---|
| `real-verify.json` | 真实沙箱：review v3 22→11/全掌握/一个未掌握/flow；名词过滤 11→10/剔除 1；只读边界 |
| `verify_sandbox.py` | 可重跑的真实副本验证脚本（`KB_TEACH_AI=0`，只写沙箱） |
| `full-suite.txt` | 全量回归输出（后端三套全绿；5 条失败均在前端旧 test_24） |
| `http-verify.json` / `http_verify.py` | **隔离实例真实 HTTP 验收**（5 条 review + 4 条名词路由，含错误分支/doc40 无预习） |
