# 教材补充 AI 化报告（C-0125：MAP AI + 本地小节 + 按需 SUPP AI）

> 任务：task-29 · 施工智能体 B（artifact-importer）· 2026-10-22
> 用户反馈：教材补充"排版好但结果差"，要求改用**外接 AI**（与知识点总结管线同一模型）并改提示词。
> 前端侧栏/按钮由前端智能体并行改 dashboard（本任务**不碰 dashboard**）。

## 1. 结论

- `core/teach.py` 升级为**混合 AI 管线**（版本 `C-0125-teach-ai-v1`），保留 C-0124 本地能力作兜底：
  1. **MAP（AI，1 次/课件）**：课件标题 + KP 大纲 → 教材唯一章节；缓存进
     `meta.chapter_map`；AI 不可用/失败/章号非法 → **本地章投票兜底**（`source=local_fallback`）。
  2. **LOCAL（0 AI）**：每条 KP → 教材《章节细目》的具体小节 + 页码 + 只取这些页的原文摘录；
     无细目/无标题命中时用章内段落页范围兜底，**始终只查所选章**。
  3. **SUPP（AI，按 KP 按需 + 缓存）**：只在打开/点击某条 KP 时调 1 次，输出
     `verdict/summary/points/gaps/citations`；缓存键 = prompt sha + KP sha + 教材 id + 章号 + 小节页范围；
     AI 失败写 `entry.ai.status="failed"`，**本地 passages/sections 兜底不变**。
- **旧版产物识别**：真实站点已有的 `40/41-教材补充.json`（C-0124、无 chapter_map/ai/sections）会被
  `supplement_status` 标成 `legacy=true/stale=true/needs_backfill=true`，reason 明说
  "旧版产物（C-0124 纯本地，无 AI 章节映射/小节）→ 需要重新 AI 定位/补做"；build 升级时
  **先备份 `.bak-<ts>`**（`meta.prev_backup`/`legacy_prev_version` 留痕），不静默覆盖。
- 所有 AI 调用记账（attempts/tokens/cost/elapsed，成功失败都记）；**测试与真实沙箱 0 次真实 AI 请求**。

## 2. API 契约（前端按此对接）

### `teach.map_courseware_chapter(doc_id, *, force=False, allow_ai=True, out_path=None, db_path=None, conn=None, prompt_path=None, log=print) -> dict`
```json
{"ok":true,"chapter_no":6,"name":"第6章 作用于胆碱受体药","heading_path":"… > 第6章 …",
 "page_start":70,"page_end":82,"page_label":"p.70–p.82","confidence":"high|medium|low",
 "reason":"…","source":"ai|local_fallback","cached":false,
 "cost_cny":0.0031,"tokens":{"in":1200,"out":600},"elapsed_s":3.5,"attempts":1,
 "textbook_doc_id":50,"kp_artifact_sha256":"…","prompt_sha256":"…","error":""}
```
缓存命中返回 `cached=true / cost 0`（0 调用）；AI 给的章号不在章列表 → 重试 1 次 → 仍非法转本地兜底。

### `teach.build_supplement(doc_id, *, out_path=None, force=False, allow_ai=True, db_path=None, conn=None, prompt_path=None, log=print)`
- 流程：读 KP 产物 → 无教材写 `no_textbook` → MAP（AI/缓存/本地兜底）→ **逐 KP 本地小节定位**
  （**此处不调 per-KP AI**）→ 原子写 `meta + kps`。
- 每条 KP：`{kp_id,kp_name,status,chapter{chapter_no,name,chapter_name,heading_path,page_start,
  page_end,source},sections[{index,title,level,page_start,page_end,page_label,score,hit_terms}],
  pages,page_label,section_material_chars,material_preview,passages,added_terms,note}`。
- `meta` 新增：`prompt_path/prompt_sha256/prompt_error`、`ai_enabled/ai_model`、
  `chapter_map`、`ai_map_source/cost_cny/tokens/elapsed_s/error`、
  `legacy_prev_version`、`prev_backup`、`ai_kp_calls/ai_kp_cost_cny`（SUPP 累积）。
- 幂等：版本 / 教材 id / **KP 产物 sha256 + mtime** / prompt sha 都没变 → `skipped`（0 调用）。
- 旧版/force 重写前自动 `.bak-<ts>`。

### `teach.analyze_kp_supplement(doc_id, kp_id, *, force=False, allow_ai=True, out_path=None, db_path=None, conn=None, prompt_path=None, log=print)`
- 确保产物存在/升级到 C-0125（旧版会先本地 build）；AI 不可用 → `{ok:false,status:"ai_disabled"}`；
- 1 次 AI → 校验：`points[].pages`/`citations[].pages` **只保留章内页**（越界剔除）、points ≤5 且去重、
  kind 白名单（概念/机制/分类/临床/注意/例子）、quote ≤80、gaps ≤5；
- 写 `entry.ai = {status:"done|no_supplement|failed", verdict, summary, points, gaps, citations,
  model, cost_cny, tokens, elapsed_s, prompt_sha256, cache_key, section_pages, generated_at, error}`；
- 缓存命中返回 `cached=true`（0 调用）；`force=true` 强制重跑；AI 失败保留本地 passages。

### `teach.estimate_map(doc_id, ...)` / `teach.estimate_kp_supplement(doc_id, kp_id, ...)`
- 0 模型、0 写入；返回 `{calls:1, est_tokens_in, est_tokens_out, cost_cny_max, model, thinking,
  reasoning_effort, max_tokens, rule, ai_enabled, ...}`，供前端按钮显示"点一下大概多少钱"。

### `teach.supplement_status(doc_id, ...)`
- 保留原字段，`supplement` 新增 `version/legacy/stale/ai_map_source/ai_map_cost_cny`；
  新增 `ai:{enabled,model,thinking,reasoning_effort, map:{exists,source,chapter_no,chapter_name,
  confidence,cost_cny}, n_ai_ready, n_ai_missing, ai_kp_calls, ai_kp_cost_cny}`。
- `needs_backfill`：有 KP + 有教材 +（无产物 / 旧版 / 非新版无 chapter_map / no_textbook / failed /
  教材换了 / 比 KP 旧）。**旧版 C-0124 一定 stale→needs=true**。

### `teach.supplement_for_kp(doc_id, kp_id)`
- 返回 `{ok, available, textbook, entry, ai, estimate, legacy, needs_rebuild, note}`；
  旧版产物 `legacy=true/needs_rebuild=true` 并提示先"重新 AI 定位/补做"；
  `estimate` 让侧栏在按钮上显示预估费用（不自动生成、不写文件）。

## 3. 提示词（新文件，用户点名改写）

- 运行时真源：`logs/knowledge/提示词-教材补充.md`（**新建**，6398B）；tracked 审查副本：
  `docs/teach/提示词-教材补充-v1-2026-10-22.md`。
- 4 个代码块（`kp_build._grab` 逐字提取）：
  - `PROMPT-TEACH-MAP-SYS/USER`：课程-教材对齐助手；输入 `{courseware_title} {kp_outline} {chapters}`；
    只选**唯一一章**、输出 `{chapter_no,chapter_name,confidence:high|medium|low,reason}`；不确定选最接近并说明。
  - `PROMPT-TEACH-SUPP-SYS/USER`：教材补充分析老师；读者已掌握课件；找**课件没讲/没讲清**的补充；
    输出 `{verdict:has_supplement|no_supplement, summary, points[{text≤80字,pages,kind}], gaps,
    citations[{pages,quote≤60字原文}]}`；pages 必须在给定范围、不重复课件、≤5 点、无补充给空；
    输入 `{courseware_title} {chapter} {kp_name} {kp_points} {kp_summary} {section_outline} {section_material}`。
- sha256 = `3632d3effd4fcdd2…`（写进 `meta.prompt_sha256` / `entry.ai.prompt_sha256`；缓存键的一部分）。

## 4. 改动文件:行

| 文件 | 改动 |
|---|---|
| `core/teach.py`（+1085/-126，现 2338 行） | `SUPPLEMENT_VERSION` 升 `C-0125-teach-ai-v1`；新增 `_ref_span`（修 p.70-82 区间解析）、`teach_prompt_file/_load_prompts`、`_kp_model/_kp_thinking/_kp_effort/_kp_max_tokens/_ai_enabled/_cost_of/_ai_chat`、`_chapter_page_ranges/_chapter_map_entry/_kp_outline_text/_chapters_text/_map_cache_valid/_cache_map_into_file/_local_chapter_vote/map_courseware_chapter`、`_load_sections_detail/_local_kp_sections`、`_kp_sha/_supp_cache_key/estimate_map/estimate_kp_supplement`、`_backup_supplement/build_supplement`、`_scale_pages/analyze_kp_supplement/_update_supplement_entry`、`supplement_status`（加 legacy/stale/ai 摘要）、`supplement_for_kp`（加 ai/estimate/legacy）。旧 `textbook_passages/kp_textbook/_chapter_passages` 未删未改语义。 |
| `logs/knowledge/提示词-教材补充.md` | 新建（4 块提示词真源）。 |
| `docs/teach/提示词-教材补充-v1-2026-10-22.md` | 新建（tracked 审查副本）。 |
| `tests/test_21_teach_ai.py` | 新增 11 条用例（见 §6）。 |
| `tests/matrix.py` / dashboard / 其它 core | **未改**（矩阵建议见 §8）。 |

**不整本扫描**：`_local_kp_sections`/`_chapter_passages` 的教材正文 SQL 固定
`WHERE doc_id=? AND heading_path=?`；`_chapter_page_ranges` 只读 `heading_path, source_ref`
（章级元数据，不读正文）；`_chapter_candidates` 仍只 `GROUP BY heading_path`。

## 5. 成本估算（真实 doc#46 + doc#50，`estimate_*` 0 调用实测）

| 步骤 | 输入 | est_tokens_in | est_tokens_out（上界） | cost_cny_max（上界） | 说明 |
|---|---|---|---|---|---|
| MAP（每课件 1 次） | 课件标题 + 22 条 KP 大纲 + 42 章列表 | 2165 | 3647 | **¥0.0178** | 小输入；实际输出远小于上界 |
| SUPP（按 KP 按需） | KP-N07-1 + 已定位小节 11858 字摘录 | 8453 | 13279 | **¥0.0656** | 上界按"思考≈输入×1.5+600"；实际输出 JSON 仅数百 token，经验值约 ¥0.01–0.03（用户预期） |
| LOCAL（小节定位） | — | — | — | **¥0** | 纯本地，0 调用 |

口径：`kp_build.PRICE_IN/PRICE_OUT/FX`、`CHARS_PER_TOKEN=1.6`、`REASON_RATIO=1.5`；
`_ai_chat` 实际请求的 `max_tokens` 读 `KP_BUILD_MAX_TOKENS`（默认 65536），**不会因上限大而多花**（按实际生成计费）。

## 6. 测试与验证

### 单测 `tests/test_21_teach_ai.py`（11/11，全部假 `_ai_chat`，0 真实请求）

| 用例 | 断言 |
|---|---|
| `test_map_ai_once_then_build_cached` | MAP AI 仅 1 次；meta.chapter_map/source=ai；entries 有 sections/pages/passages；build 不调 per-KP AI；二次 build `skipped`（0 调用） |
| `test_map_invalid_chapter_retry_then_local_fallback` | 章号 999 → 重试 1 次（2 次调用）→ 本地投票兜底，仍定位到第6章 |
| `test_map_ai_failure_local_fallback` | AI 失败 → local_fallback + ai_map_error，产物照常 |
| `test_ai_disabled_local_only_no_calls` | AI 不可用 → 0 次 `_ai_chat`，仍本地生成 |
| `test_analyze_cache_force_and_pages_filter` | SUPP 1 次；越界页 999 剔除、重复 point 去重、kind 白名单；二次 cached 0 调用；force 重跑；落盘 entry.ai |
| `test_analyze_no_supplement` | verdict=no_supplement → status=no_supplement、points=[] |
| `test_analyze_ai_failure_keeps_local_fallback` | AI 失败 → entry.ai.status=failed + error，本地 passages 不变 |
| `test_analyze_ai_disabled` | 返回 ai_disabled，不发请求 |
| `test_estimates_no_ai_no_write` | estimate_map/estimate_kp_supplement 0 调用、0 写文件、cost>0 |
| `test_legacy_artifact_is_stale_then_upgraded` | **C-0124 旧产物 → status legacy/stale + needs_backfill=True（reason 含"旧版"）**；build 升级为新版、`.bak-*` 备份、`legacy_prev_version` 留痕、升级后 needs=False |
| `test_prompt_contract` | 4 块可加载、MAP 3 个 + SUPP 7 个占位符齐全、关键要求（唯一一章/no_supplement/不重复）在 SYS 里、tracked 副本存在 |

兼容：`tests/test_19_textbook_supplement.py` **8/8 通过**（旧 API/字段兼容；run_all 环境
`VERIFY_REFRESH_FIXTURES` 标记下 `_ai_enabled()` 自动 False → 0 真实 AI，见 §7 风险 3）。

### 真实沙箱（0 AI：`KB_TEACH_AI=0` + `allow_ai=False`，结果 `real-verify.json`）

| 项 | 结果 |
|---|---|
| doc46 build | `done`，22 条 KP → 22 条定位到小节；chapter_map 来源 `local_fallback`（AI 关闭）第6章 p.70–p.82；`sections_total=41`、22 条均有 sections |
| SQL 证据 | 49 条 chunks 查询，**47 条带 `heading_path=?`**，**0 条无 heading_path 的正文查询**（不整本扫描） |
| `supplement_status(46)` | `needs_backfill=false`、legacy=false、ai.enabled=false、ai.n_ai_missing=22（按需未做） |
| doc40 旧版 | before：`needs_backfill=true`、legacy/stale=true、version=C-0124、reason 含"旧版…重新 AI 定位/补做"；build(force) 后：version=C-0125、`legacy_prev_version=C-0124-…`、`.bak-20261022-005359` 存在、needs=false |
| 只读边界 | 真实 DB/真实 KP 产物/真实 `40/41-教材补充.json`/`50-章节细目.json` sha256 前后一致；`model_calls=0` |
| 真实站点旧产物 | 未删除未覆盖：只在沙箱里实验；真实升级由用户点"重新 AI 定位/补做"触发，届时代码会先 `.bak` |

- `run_all.py --only test_21`：**11/11 通过、退出码 0**；`--only test_19`：8/8；`py_compile core/teach.py` 0。

## 7. 全量回归

`tests/run_all.py`：见 `_work/teach-ai/full-suite*.txt`。本任务交付时全量 **168 条 / 166 通过 /
2 失败**，**2 条失败全部在 `test_20_textbook_supplement_ui`（前端智能体正在写的 UI 用例）**：
`test_content_parts`（断言"右侧栏缺少内容块：补充段落"——dashboard HTML 还没改完）与
`test_banner_and_build`（断言导图栏按钮/接口字符串尚未落地）；**与本后端无关**（本任务未改 dashboard）。
本任务相关的 `test_19`（8/8）与 `test_21`（11/11）隔离复跑全绿（`--only` 退出码 0）；
按并行施工约定（各自只跑 `--only`、全量由 Lead 在前端完成后串行收口）——交付前已等 dashboard
mtime 稳定 90s 后复跑一次，仍只剩前端 WIP 的 2 条。

## 8. 建议测试矩阵行（Lead 改 `tests/matrix.py`，建议 F113）

```python
dict(id="F113", feature="教材补充 AI 化（AI 章映射 + 本地小节 + 按需 AI 补充）",
     entry="core/teach.py::map_courseware_chapter/build_supplement/analyze_kp_supplement/"
           "estimate_map/estimate_kp_supplement；提示词 logs/knowledge/提示词-教材补充.md",
     kind="write", priority="P1",
     tests=["test_map_ai_once_then_build_cached",
            "test_map_invalid_chapter_retry_then_local_fallback",
            "test_map_ai_failure_local_fallback",
            "test_ai_disabled_local_only_no_calls",
            "test_analyze_cache_force_and_pages_filter",
            "test_analyze_no_supplement",
            "test_analyze_ai_failure_keeps_local_fallback",
            "test_analyze_ai_disabled",
            "test_estimates_no_ai_no_write",
            "test_legacy_artifact_is_stale_then_upgraded",
            "test_prompt_contract"],
     evidence="json"),
```

## 9. 前端接法（本任务不改 dashboard）

1. 侧栏：`GET /api/textbook-supplement/kp?doc_id=&kp_id=` → `supplement_for_kp`；
   渲染 `entry.sections/pages/passages` 作**本地兜底**；`entry.ai` 有结果时展示 AI 补充（points/gaps/citations）；
   没有 `ai` 时按钮显示 `estimate.cost_cny_max`（如"AI 补充 ≈¥0.02/条，可能 3–20s"）。
2. 补做/升级按钮：`POST /api/textbook-supplement/build {doc_id, force?}` → `build_supplement`；
   返回 `meta.ai_map_source` 可提示"AI 定位/本地兜底"；旧版产物时先显示"重新 AI 定位"（status.legacy）。
3. 单条 AI 分析按钮：`POST /api/textbook-supplement/analyze {doc_id,kp_id,force?}` → `analyze_kp_supplement`；
   返回 `cached` 时提示"已缓存，0 费用"。
4. 提醒：`supplement_status.needs_backfill/reason`（旧版/无产物/教材换本）；AI 开关/模型读 `status.ai`。

## 10. 风险 / 已知限制

1. **AI 章映射仍可能选错章**（尤其课件跨多章）：产物保留 `chapter_map.confidence/reason/source`，
   `local_fallback` 时明确标注；前端应显示来源，不要假装是 AI 结论。
2. **按需 AI 成本上界偏保守**：`cost_cny_max` 按"思考≈输入×1.5+600"估算（SUPP 上界 ¥0.066/条），
   实际输出通常远小于此；真正扣费看 `entry.ai.cost_cny`。若用户对费用敏感，可后续把
   `TEACH_SUPP_MATERIAL_MAX` 从 12000 下调（会略降补充质量）。
3. **测试防真实 AI 的保险**：`_ai_enabled()` 在 `VERIFY_REFRESH_FIXTURES`（`run_all.py` 测试进程标记）
   下恒 False——保证全量回归**绝不发真实请求**；`tests/test_21` 用 monkeypatch `_ai_enabled=True`
   专测 AI 路径。生产（dashboard/CLI）不设该标记，按 key 可用性正常启用；`KB_TEACH_AI=0` 可人工关。
4. **旧数据不自动升级**：C-0124 旧产物只在 status 里提示 stale；用户点"补做"才升级（先 `.bak`）。
5. **缓存失效口径**：KP 产物 sha/mtime、教材、prompt sha、小节页范围任一变化 → MAP/SUPP 重新生成；
   `force=True` 可手工重跑。
6. 未 commit；真实数据/8756 未动；沙箱验证 0 真实写、0 AI。

## 11. 证据文件（`_work/teach-ai/`）

| 文件 | 内容 |
|---|---|
| `real-verify.json` | 真实沙箱 doc46（本地兜底/sections/SQL 只读章级）+ doc40 旧版 stale→升级 + 只读边界 |
| `verify_sandbox.py` | 可重跑的真实副本验证脚本（`KB_TEACH_AI=0`，只写沙箱） |
| `test21-runall.txt` / `test19-compat.txt` | 本任务与旧用例的 run_all 输出（11/11、8/8） |
| `full-suite.txt` / `full-suite2.txt` | 全量回归输出（含前端 WIP 造成的无关失败证据） |
| `ai_block.py` | 追加到 `core/teach.py` 的 C-0125 代码块（便于 review/回溯） |
