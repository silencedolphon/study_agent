# 复习后端报告（C-0127：自测挖空/熟悉度 + 三步复习数据 + 5 条路由）

> 任务：task-32 · 施工智能体 B（artifact-importer）· 2026-10-23
> 用户需求：复习前自测（随机抽一半 KP 挖名词填空，不能全对=不熟悉）→ 课件熟悉度；
> 之后按"思维导图逐个 KP → 整份课件笔记 → 预习重点名词"三步复习；与遗忘曲线月历/进度悬浮窗打通。
> **协作约定**：`core/review.py` 由我写；`tools/dashboard.py` 的 5 条路由由 dash-fixer（task-33）
> 按我给的契约统一落地（该文件他正在大改，我**没有改 dashboard.py**）；我用隔离实例做了真实 HTTP 验收。

## 1. 结论

- 新增 `core/review.py`（669 行，零 AI/零新依赖）：自测抽题/挖空/判分/持久化 + 三步复习数据 +
  课件复习列表，5 个稳定函数供 dashboard 路由直接调用。
- **自测**：`n_sample=ceil(n_total/2)`（22→11）随机不放回；每条 KP 用 summary/points 文本
  抽 2–4 个名词挖空（启发式，**无 jieba**）；session 存进程内存（TTL 30min，加锁），
  **开始自测不写盘**；提交时归一化判分（全半角/大小写/空白标点），**只要有一个空错该 KP 即
  unfamiliar**；`familiarity=熟悉数/抽样数` 原子写 `logs/review/<doc>-自测.json`。
- **三步复习数据**：`build_flow` 返回 KP（按产物顺序 + 自测 unfamiliar 标记 + note_count）、
  笔记（`study.notes_for(doc_id=)` 按 KP 分组 + unbound）、预习重点名词
  （supplement `meta.chapter_map` → 本地章映射兜底 → `<教材id>-预习总结-第N章.json` 的 terms
  → 名词解释产物 → 空），**全程零 AI、零 DB 写**。
- **真实沙箱（0 模型/0 真实写）**：doc46 自测 22→11、11 条各 4 空、全对 familiarity=1.0、
  错一空 familiarity=0.9091/unfamiliar=1；flow 22 KP / 5 篇笔记全部按 KP 分组 / 第6章预习名词 11 条；
  真实 DB/KP 产物/笔记 sha256 前后一致、真实 `logs/review` 仍未创建。
- **路由验收（隔离实例真实 HTTP）**：dash-fixer 已落地 5 条 `/api/review/*` 路由，我在
  KB_* 全指沙箱的实例上逐条真实打了一遍——全部 200/预期 400、字段与契约一致、无 500；
  结果见 `http-verify.json`（详见 §6）。

## 2. 前端契约（稳定签名 / 返回结构）

> 同样写进了 `core/review.py` 顶部 docstring，dashboard 路由按此调用。

| 路由 | 调用 | 返回 |
|---|---|---|
| `GET /api/review/coursewares` | `list_courseware_reviews(conn)`（**只读 conn**，`_lock` 内） | `{"ok":true,"items":[{doc_id,title,n_kps,selftest:{exists,familiarity,n_sample,n_unfamiliar,updated_at},terms:{available,n,source},chapter:{no,name}}]}` |
| `GET /api/review/flow?doc_id=` | `build_flow(conn, doc_id)`（**只读 conn**，`_lock` 内） | `{ok,doc:{id,title},kps:[{kp_id,name,summary,points,pages,node_path,note_count,unfamiliar}],notes:{groups:[{kp_id,kp_name,notes:[{file,title,chars,created,source_label}]}],unbound:[...],n_notes},terms:{source,artifact?,chapter:{no,name,page_label,heading_path,source},items:[{term,en,def,why}],n},selftest:{...},stages:[{key,title,n,done?}]}` |
| `GET /api/review/selftest?doc_id=` | `start_selftest(doc_id, db_path=None, rng=None)`（**无需 conn**，锁外） | `{ok,doc_id,title,session_id,n_total,n_sample,ttl_seconds,kps:[{kp_id,name,unfamiliar}],items:[{kp_id,name,segments:[{t:"text",text}|{t:"blank",i,answer,hint}]}],note}` |
| `POST /api/review/selftest/submit` | `submit_selftest(doc_id, session_id, answers)`（**锁外**，原子写文件） | `{ok,familiarity,n_sample,n_total,n_familiar,n_unfamiliar,unfamiliar_ids,results:[{kp_id,name,familiar,correct,n_blanks,answers,user_answers}],generated_at}` |
| `GET /api/review/selftest/status?doc_id=` | `selftest_status(doc_id)`（**无需 conn**，锁外） | `{ok,exists,familiarity,n_sample,n_total,n_unfamiliar,updated_at,unfamiliar_ids,kps}` |

- **失败一律 200/400 JSON**：函数返回 `{"ok":false,"error":"人话","_code":400}`，路由用
  `_json_auto` 发送（会把 `_code` 映射成 HTTP 400 并剥离，不会 500）。
- `conn`/`db_path` 均可注入：`build_flow/list_courseware_reviews` 接现成只读 conn；
  `start_selftest` 接 `db_path`（查标题用）；`submit_selftest/selftest_status` 只碰文件。
- 提交格式：`answers` 支持 `{kp_id:[blank1,blank2,...]}`（推荐）或扁平 list（按 items/blank 顺序）。

## 3. 数据口径（可复现）

### 自测抽题与挖空
1. `n_sample = ceil(n_total/2)`，至少 1、最多全部；`random.Random` 可注入（测试给种子，生产随机）。
2. 每条 KP 的题面：`summary`，空则拼 `points`，再空用 name；超 400 字截断。
3. 候选名词优先级：① 该 KP 名称 ② 全部 KP 名称 ③ points 短语（2–24 字）④ 正文启发式
   汉字连续段 2–8 字/英文数字词；全部要求能在题面文本里找到，**过滤停用词/纯虚词**。
4. 选 2–4 个**不重叠**的候选；不足 2 个用 ≥4 字长短语兜底，仍不足则保证至少 1 个空
   （否则这条 KP 无法判熟悉度）。blank 带 `i`（顺序）、`hint`（首字+字数）。
5. 判分归一化 `_norm_answer`：NFKC 全半角统一 + 小写 + 去掉所有空白/标点；
   **一条 KP 的全部空都对才算 familiar**（用户口径"不能全对=不熟悉"）。
6. 持久化（原子写）：`{meta:{version:C-0127-review-v2,doc_id,title,generated_at,created_at,session_id,n_total,n_sample,n_familiar,n_unfamiliar,familiarity,norm},kps:{kp_id:{familiar,correct,n_blanks,answers,user_answers}}}`；
   **v2 口径（2026-10-23 修）**：`kps` **只保留本次抽中的 KP**，与 meta 的本次计数严格自洽
   （`familiarity == n_familiar/n_sample`、`n_unfamiliar == len(unfamiliar_ids)`）；
   status/flow 一律从 `kps` 现算，**不再合并历史会话**。旧 v1 文件（曾合并历史）按同一规则
   现算并标 `legacy=true`，下一次提交自动升级 v2。
7. session：进程内 `_SELFTEST[session_id]`（加锁 + TTL 30min 清理），提交成功即销毁；
   服务重启后需重新开始自测（持久化结果不丢）。

### 三步复习数据
- `kps`：按 KP 产物顺序；`unfamiliar` 来自自测持久化；`note_count` 来自笔记元数据。
- `notes`：`study.notes_for(doc_id=doc)`（只读元数据，不搬正文）→ 按 KP 分组成
  `groups`（顺序与 kps 一致）+ `unbound`（有 doc_id 但无/未知 kp_id 的课件级笔记）。
- `terms`（零 AI）：章映射优先读 supplement `meta.chapter_map`（任意版本字段可用）；
  无则 `teach.map_courseware_chapter(doc_id, allow_ai=False, out_path=<不存在路径>)` 本地兜底
  （**只读，不写 supplement 缓存**）→ 读 `<教材id>-预习总结-第<章>.json`.terms →
  退 `<教材id>/<课件id>-名词解释.json` → `{source:"none",items:[]}`。
- `stages`：三步的 `{key:title,n,done?}`，供前端画步骤条。

### 课件复习列表
- 遍历 `documents`，只保留有 KP 产物的；附 selftest 摘要、terms 可用性/数量/来源、映射章节。

## 4. 改动文件:行

| 文件 | 改动 |
|---|---|
| `core/review.py` | **新增 669 行**：`REVIEW_VERSION/SESSION_TTL`、`review_dir/selftest_out_file`、`selftest_status`、`_resolve_chapter/_resolve_terms`、挖空启发式（`_candidate_terms/_choose_blanks/_segments/_make_item`）、`start_selftest`、`_norm_answer/submit_selftest`、`build_flow`、`list_courseware_reviews`；顶部 docstring 含前端契约。 |
| `tests/test_23_review_flow.py` | 新增 6 条用例（见 §5）。 |
| `_work/review-backend/` | `report.md`、`real-verify.json`、`http-verify.json`、验证脚本、全量输出。 |
| `tools/dashboard.py` | **本任务未改**（dash-fixer 按契约落地 5 条路由；他改动了 +955/-119，含前端） |
| `tests/matrix.py` 等 | 未改（矩阵建议见 §8）。 |

## 5. 测试

`tests/test_23_review_flow.py`（**7/7**，全离线、无 AI、真实目录隔离）：

| 用例 | 断言 |
|---|---|
| `test_sample_half_segments_and_all_correct` | 6 KP→抽 3；每条 1–4 空；blank 的 `i` 连续、答案能还原原文；开始不写盘；带空格/标点答案仍判对 → familiarity=1.0；落盘 kps=3 且与 meta 自洽；重复提交同一 session 报错 |
| `test_wrong_one_blank_unfamiliar_and_flow` | 错一空 → unfamiliar、familiarity=2/3；新一次全对后 **status/flow 不留历史 unfamiliar**（dash-fixer 现场 bug 回归）；扁平 answers 也支持 |
| `test_session_forged_expired_and_no_artifact` | 伪造 session / TTL 过期 / doc_id=0 / 无 KP 产物 → 明确 `ok:false`，不抛异常 |
| `test_legacy_merged_file_status_is_self_consistent` | **旧 v1 合并文件**（meta 说全对、kps 里还有 1 条 unfamiliar）→ status 按 kps 现算（21/22、n_unfamiliar=1、legacy=true），不再"100% 但不熟 1" |
| `test_notes_grouped_by_kp_and_unbound` | 2 篇绑 KP + 1 篇课件级 → 6 组、顺序同 kps、note_count 正确、unbound 1；笔记文件 sha256 不变 |
| `test_terms_preview_available_and_empty_fallback` | 无预习产物 → source=none 不崩；有 → source=preview_chapter、2 词、章号 6；章映射缺失 → 也 none 不崩 |
| `test_list_courseware_reviews` | 只列有 KP 产物的课件；selftest 摘要/terms/chapter 字段；提交后卡片 familiarity 更新 |

- `run_all.py --only test_23`：**7/7 通过、退出码 0**。
- 全量 `tests/run_all.py`：见 §7（本任务交付时 **178 条全绿**；最终复核见报告末）。

## 6. 真实沙箱（零 AI / 零真实写）：`real-verify.json`

| 项 | 结果 |
|---|---|
| doc46 自测 | 22 条 KP → **抽 11 条**；11 条各 **4 个空**；生成耗时 **0.074s** |
| 全对提交 | **familiarity=1.0**、n_familiar=11、n_unfamiliar=0；落盘文件 `kps=11` 且与 meta 自洽（`artifact_consistency=true`） |
| 错一空提交 | **familiarity=0.9091**、unfamiliar_ids=[KP-N16-1] |
| flow | 22 KP；笔记 **5 篇全部按 KP 分组**（KP-N07/N08/N09/N10/N28 各 1）；terms=`preview_chapter` **第6章 11 条**（首词：胆碱受体激动药/调节痉挛/调节麻痹/阿托品化/胆碱酯酶）；耗时 0.148s |
| 课件列表 | 仅 doc46 有 KP 产物 → 1 条卡片；selftest/terms/chapter 字段齐全 |
| 边界 | 真实 DB、doc46 KP 产物、真实 notes sha256 前后一致；真实 `logs/review` 仍不存在；沙箱只写 `<sandbox>/logs/review/46-自测.json`；**0 模型调用** |

## 7. 路由验收（隔离实例真实 HTTP）：`http-verify.json`

做法：独立进程把 `KB_*` 全指 `_work/review-backend/sandbox` 后 import dashboard，
patch `subjects.restore` 阻止其按真实学科注册表切回真实库，再起真实
`ThreadingHTTPServer(127.0.0.1:8899)`（Handler/路由都是 dash-fixer 落地的真代码）。

| 路由 | HTTP | 结果 |
|---|---|---|
| `GET /api/review/coursewares` | **200** | ok=true、1 条（doc46 n_kps=22、terms 11、chapter 第6章） |
| `GET /api/review/flow?doc_id=46` | **200** | ok=true、22 KP、5 篇笔记全分组、terms=preview_chapter/11 词、selftest、stages 齐全 |
| `GET /api/review/selftest?doc_id=46` | **200** | ok=true、n_total=22、n_sample=11、11 items、blank_stats 全 4、session_id 有 |
| `POST /api/review/selftest/submit`（全对） | **200** | ok=true、familiarity=1.0、n_familiar=11 |
| `GET /api/review/selftest/status?doc_id=46` | **200** | ok=true、exists=true、familiarity=1.0 |
| `POST submit`（错一空，新 session） | **200** | familiarity=0.9091、unfamiliar=1 |
| 错误分支：flow doc_id=0 | 200 | `{"ok":false,"error":"缺少 doc_id"}`（前端路由自己的校验） |
| 错误分支：selftest doc_id=999999 | **400** | `{"ok":false,"error":"这份课件还没有末级知识点产物（先整理知识点）"}` |
| 错误分支：伪造 session | **400** | `{"ok":false,"error":"自测会话不存在或已过期（30 分钟）…"}` |

- 全程无 500；沙箱 `logs/review/46-自测.json` 有写、**真实 `logs/review` 仍不存在**；0 模型调用。
- 结论：**5 条路由已验收通过**（字段/错误码/锁外写行为符合契约）。

## 8. 全量回归与矩阵

- 全量 `tests/run_all.py`（v2 修复后最终跑）：**190 条 / 通过 190 / 失败 0 / 已知缺陷 0 /
  跳过 0、退出码 0**（`_work/review-backend/full-suite-final2.txt`；等 dashboard 稳定后运行，
  `test_23_review_flow` 7 条全部在其中）。
- 建议矩阵行：当前矩阵最大 **F116** → 建议 **F117**（task 原建议 F115 已被占用）：

```python
dict(id="F117", feature="复习后端（自测挖空/熟悉度 + 三步复习数据 + 5 条路由）",
     entry="core/review.py::start_selftest/submit_selftest/selftest_status/"
           "build_flow/list_courseware_reviews",
     kind="read", priority="P1",
     tests=["test_sample_half_segments_and_all_correct",
            "test_wrong_one_blank_unfamiliar_and_flow",
            "test_session_forged_expired_and_no_artifact",
            "test_notes_grouped_by_kp_and_unbound",
            "test_terms_preview_available_and_empty_fallback",
            "test_list_courseware_reviews"],
     evidence="json"),
```

## 9. 风险 / 已知限制

1. **挖空是启发式**：候选按规则挑 2–4 个不重叠名词，个别 KP 可能挖到偏难/偏泛的词；
   blank 带 `hint`（首字+字数）供前端提示；判分只做字符串归一化（**不做语义等价**），
   用户写同义词会判错——这与"自测"目的（逼回忆原词）一致，但要在 UI 上说清"按原词判分"。
2. **全对才 familiar**：一条 KP 的 4 个空必须全对；用户口径"不能全对=不熟悉"已落实。
3. **session 在进程内存**：TTL 30min；看板重启/切库会丢未提交的自测（持久化结果不丢），
   前端应提示"过期请重新开始"。
4. **自测结果只代表最近一次抽样**（v2）：`kps`/`familiarity` 都是本次 11 条的结果，
   没抽中的 KP 不再保留旧标记；旧 v1 文件读出来按 kps 现算并标 `legacy=true`。
   前端/月历读 `selftest.familiarity + unfamiliar_ids` 即可，与文件严格自洽。
5. **terms 依赖预习产物**：真实库目前只生成了第 5/6/9 章预习；其它章回退名词解释/空
   （前端按 `source` 显示空态，不要报错）。章映射本地兜底可能选错章（`chapter.source=local_fallback` 如实标注）。
6. **零 DB 写**：自测结果只写 `logs/review/`；不改 KP 产物/笔记（沙箱已用 sha256 证明）。
7. 未 commit；真实数据/8756 未动；测试与验收全离线（0 AI/0 网络请求）。

## 9.1 修订记录（2026-10-23 收尾）

- dash-fixer 在现场报告：v1 的 `kps` 跨会话合并 + `meta` 本次结果 → 可能出现
  `familiarity=1.0` 却 `n_unfamiliar=1`（"100% 但不熟 1"）。已修：**版本升
  `C-0127-review-v2`**，提交只写本次抽中的 KP，status/flow 一律从 kps 现算；
  旧 v1 文件 `legacy=true` 并同口径现算；新增回归用例
  `test_legacy_merged_file_status_is_self_consistent`。前端卡可继续显示"熟悉度 100%（全对 11/11）"。

## 10. 证据文件（`_work/review-backend/`）

| 文件 | 内容 |
|---|---|
| `real-verify.json` | 真实沙箱：22→11、全对/错一空、flow notes/terms、只读边界 |
| `http-verify.json` | 隔离实例真实 HTTP：5 条路由 + 错误分支的状态码/字段 |
| `verify_sandbox.py` / `http_verify.py` | 可重跑的验证脚本（`KB_TEACH_AI=0`，只写沙箱） |
| `full-suite*.txt` | 全量回归输出 |
| `server.log` | 早先子进程起服务时的日志（保留作排查） |
