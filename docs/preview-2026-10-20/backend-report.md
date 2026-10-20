# 预习后端 v2 重做报告（按章多产物 + 小节细分 + 新提示词）

> 任务：task-23 · 施工智能体 B（artifact-importer）· 2026-10-20
> 用户要求：「**重新实现预习功能**」并明确「**这可能要改一下预习模型的提示词**」。
> 前端页签由前端智能体并行做（本任务**不碰 dashboard**）。

## 1. 结论

- 预习后端已按新目标重做：**每章先按小标题/内容逻辑细分小节，再逐小节写预习**；
  产物改为**按章多文件**，不同章互不覆盖；**每章仍然只做 1 次模型调用**（小节细分在同一次调用里完成）。
- 缺《章节细目》时用 `core/sections.py` 纯本地正则**自动生成**（0 次模型调用）；
  `PREVIEW_ENABLED` **默认改为开启**（用户要求可用；仍可关闭）。
- 新提示词已重写（旧文件备份 `.bak-20261020`；审查副本进 `docs/preview/`）。
- 真实 doc#50（42 章）沙箱验证：自动生成《章节细目》**569 节 / 42 章 / 0.12s / 0 次模型调用**；
  第 2 章 33 节；dry-run 不写任何文件；**真实库/真实 logs 一个字节未动**。
- 单测 `tests/test_17_preview_core.py` 6/6；全量 `tests/run_all.py` 结果见 §6。

## 2. 新目标契约（与 Lead 给的前端契约一致）

| 能力 | 实现 |
|---|---|
| 章节结构/细目 | `ensure_sections_artifact(doc_id, db_path=None, *, dry_run=False)`：缺产物时 `core.sections.load_blocks → detect_sections → dump_sections`（纯本地、0 模型）；已存在复用；**抽不出节如实返回 note、不伪造、不写空产物**；dry-run 只报告不写盘。`list_chapters()` 增 `sections_artifact.would_auto_build`（list 本身零写入）。 |
| 按章产物 | `logs/knowledge/<doc_id>-预习总结-第<N>章.json`（多章并存）；旧单文件 `<doc_id>-预习总结.json` 只读兼容。 |
| 构建 | `build_chapter_preview(doc_id, chapter, *, prompt_path, out_path, db_path, dry_run, force, ignore_switch, log, on_progress)`；dry-run→estimate（0 写入）；开关校验；自动 ensure 细目；读章材料+节级大纲；**1 次调用**；新 schema 解析/校验；原子写（同章重跑先 `.bak-<ts>`）。 |
| 状态/查看 | `preview_status(doc_id)` → `{章号: {exists,status,generated_at,cost_cny,n_subsections,chapter_name,pages,preview_version,is_current_version}}`（+ `_schema`/`_old_single`）；`read_preview_artifact(doc_id, chapter)` → `{ok, artifact:{meta,chapter,summary,subsections,terms,sources}, path, legacy, is_current_version}`。 |
| 后台/CLI 兼容 | `start_background` 内部改调新函数（签名/进度字段不变）；`build_preview` 保留为 wrapper（`tools/preview_build.py` 继续可用）；`estimate_preview` 保留旧字段、新增 `out_path_per_chapter` / `old_out_path` / `sections_would_build`。 |
| 版本 | `PREVIEW_VERSION="C-0110-preview-v2"`；旧产物读取仍可展示、`legacy=true` 标记。 |

## 3. 新产物体积/字段（前端查看器用）

按 Lead 契约实现，顶层四块：`meta` / `chapter` / `summary` / `subsections` / `terms` / `sources`。

- `meta`：`preview_version`、`doc_id`、`chapter_no`、`chapter_name`、`heading_path`、`pages`、`page_label`、
  `status(done|failed)`、`error`、`model/thinking/reasoning_effort/max_tokens`、`prompt_sha256`、
  `usage_this_run{calls,tokens_in,tokens_out}`、`cost_cny`、`n_subsections`、
  `sections_source(artifact|generated|none)`、`warnings`、`created_at`，另兼容旧字段
  （`calls/runs/usage_total/cost_cny_all_calls/params/material/inputs/sections_regex/pipeline/generated_at/elapsed_s`）。
- `subsections[]`：`no/title/pages/page_label/why/core[{name,explain}]/lecture/pitfalls[]/self_check[{q,a}]`，
  另带 `chunk_ids`（**管线按页码自己算，不采信模型**）。
- `summary`：`overview/study_order/difficulties/prerequisites/exam_focus`（+ 旧 `questions` 兼容）。
- `terms[]`：`term/en/def/why`（沿用"必须出自本章材料"的校验）。

**解析/校验规则**：`subsections[].pages` 越界（不在本章页范围）→ 该小节 pages **清空** + warning；
`title` 必填；`why/core/lecture/pitfalls/self_check` 全空视为空小节丢弃；同名去重；最多 30 节；
模型若只给旧 `key_points` → 按 `section/页码` 归组兜底生成 subsections（`meta.parser=legacy_key_points` + warning）。

## 4. 提示词重写（用户点名）

- 运行时真源：`logs/knowledge/提示词-预习总结.md`（**已重写**，旧文件备份 `提示词-预习总结.md.bak-20261020`，8626B）。
- 审查副本（tracked）：`docs/preview/提示词-预习总结-v2-2026-10-20.md`（注明真源在 logs，本文件供 review）。
- 保留两个 `<!-- PROMPT-PREVIEW-SYS/USER -->` 代码块（`kp_build._grab` 逐字提取），USER 占位符沿用：
  `{deck} {chapter} {heading_path} {page_label} {n_pages} {sections_outline} {chapter_material}`。
- 新 SYS 要点：角色=带课前预习的大学老师；读者第一次学、目标是**上课能跟上老师任何速度**；
  硬规矩（只用本章材料/页码不猜/数字不编/不整句照抄/只输出 JSON）；**先按【节级大纲】细分 3~10 个小节**
  （无大纲/噪声时按内容逻辑自拟），每小节写 `why/core/lecture/pitfalls/self_check`，章级写
  `overview/study_order/difficulties/prerequisites/exam_focus/key_terms`；JSON schema 写死。
- 新 sha256：`7e3d89e0…`（会写进每章产物 `meta.prompt_sha256`，可回查）。

## 5. 改动文件:行

| 文件 | 改动 |
|---|---|
| `core/preview.py`（+约 580 行，1874 行） | `:74` 版本号 v2；`:154/164` 按章/旧单文件路径；`:127/132` 开关默认开启+文案；`:884` `estimate_preview` 重写（按章路径 + would_build）；`:985` `prev_info_preview` 重写（按章汇总/单章）；`:1046` `build_preview` 改为 wrapper；`:1257` `ensure_sections_artifact`；`:1313` `_clean_subsections`；`:1405` `_subsections_from_key_points`；`:1472` `_build_summary_v2`；`:1490` `_v2_meta`；`:1567` `build_chapter_preview`；`:1737` `preview_status`；`:1792` `read_preview_artifact`；`:1176` `start_background` 改调新函数；`list_chapters` 增 `would_auto_build`。旧 `_clean_points/_preview_meta/_build_summary/_attach_points` 保留（旧路径/对照脚本可能引用）。 |
| `core/config.py:548-563` | `PREVIEW_ENABLED` 默认 0→1 + hint 更新；`_PIPELINES.preview` 描述改「按章 + 小节细分 + 细目缺失本地生成」（只改这段，其余未动）。 |
| `logs/knowledge/提示词-预习总结.md` | 重写（8600→8076B）+ `.bak-20261020` 备份。 |
| `docs/preview/提示词-预习总结-v2-2026-10-20.md` | 新增审查副本（tracked）。 |
| `tests/test_17_preview_core.py` | 新增 6 条纯单测（见 §6）。 |
| `tests/matrix.py` | **未改**（按分工由 Lead 加行，建议见 §8）。 |

## 6. 测试与验证

- `tests/test_17_preview_core.py`（6 条，全程不碰真实库/不调模型）：
  1. 新 schema 写入结构/`pages` 越界清空/空+重名小节丢弃/不采信模型 chunk_ids/1 次调用；
  2. 旧 `key_points` 兼容兜底（`parser=legacy_key_points` + warning）；
  3. 多章产物互不覆盖 + `preview_status`/`read_preview_artifact` + 旧单文件只读兼容（legacy 标记）；
  4. dry-run（含旧名 `build_preview`）不调模型、不写任何文件；
  5. `ensure_sections_artifact`：缺产物 dry-run 不写 / 抽不出节不伪造 / 有节则本地生成且二次复用；
  6. 真实提示词契约：两个块可提取、7 个 USER 占位符齐全、SYS 覆盖小节/跟上/JSON 要求、审查副本存在。
- `run_all.py --only test_17`：**6/6 通过、退出码 0**（`_work/preview-core/test17-runall.txt`）。
- **全量 `tests/run_all.py`**（`_work/preview-core/full-suite.txt`）：**113 条 / 通过 107 / 失败 6**，
  其中 `test_17_preview_core` **6/6 全 PASS**。6 条失败全部来自**并行的前端智能体正在改
  `tools/dashboard.py`**（工作区 diff +989/-178，接口 `/api/graph` 暂时 404，导致
  test_02/test_03/test_08/test_11/test_05 依赖 dashboard 的用例失败），**与本任务无关**
  （本任务只改 core/preview.py + core/config.py 的预习字段，未碰 dashboard）。前端改完后由 Lead
  串行复跑全量即可；本任务 `--only test_17` 独立运行 6/6 通过。
  交付前 2 次复查（`--only test_graph`）仍 404 → 前端改动尚未完成，本任务**不碰 dashboard**、
  不代改他人文件，按"并行施工只跑 --only、全量由 Lead 串行跑"的既定约定交给 Lead 收口。
- `py_compile core/preview.py core/config.py tests/test_17_preview_core.py` exit 0。
- **真实 doc#50（0 模型调用，只读）**：`_work/preview-core/verify_real.py`（沙箱副本，结果 `real-verify.json`）：
  - `list_chapters`：42 章、`sections_artifact.read=false / would_auto_build=true`（缺细目，只读不写）；
  - `ensure_sections_artifact(dry_run=True)`：不写文件；真跑：**569 节 / 42 章 / 0.12s / 0 次模型调用**，二次调用复用；
  - 生成后 `list_chapters`：第 2 章 **33 节**（全库 569 节）；
  - `estimate_preview(doc50, 2)`：21 页 / 33 节 / 材料 28083 字 / **est_calls=1** / 成本上界 ¥0.1428；
  - `build_chapter_preview(dry_run=True)`：不写预习产物；**model_calls=0**；真实库 sha256 前后一致、真实 `logs/` 无 50-* 文件。
  - CLI 实测：`tools/preview_build.py --doc 50 --list-chapters`、`--chapter 2 --dry-run` 均正常（0 调用）。
- 未真实调模型（费用 0）；未写真实 `index/`、`logs/knowledge` 产物（只改了提示词真源与备份，见 §5）。

## 7. 风险 / 已知限制

1. **默认开启是行为变更**：`PREVIEW_ENABLED` 默认 1（用户要求可用）。老用户若不想用，去设置中心关掉即可；关着时入口拒绝并给可操作提示。
2. **老单文件产物**：只在读取时做只读转换（`legacy=true` + warning）；不会自动迁移成按章文件。已生成的旧产物不占新按章路径。
3. **小节细分质量依赖模型**：提示词要求 3~10 节，但 parser 只做硬校验（pages/空节/重名/≤30），
   数量不在范围内只记 warning、不阻断；若模型仍给旧 `key_points`，按 section/页归组兜底（why/lecture 为空）。
4. **token 上限**：新 schema 输出更长，`PREVIEW_MAX_TOKENS=16384` 可能被打满（思考链与正文共用）；
   `_preview_call` 既有"输出打满→下一次加倍"兜底仍在；若频繁触发，可在设置中心调大。
5. **前端未对接前**：当前 dashboard 还没有预习页签（前端智能体在并行做）；本任务只保证 core API 与 CLI 可用。
   `tools/preview_build.py` 的文件头 docstring 仍描述旧路径（按约束未改该工具），但其输出/产物已按新路径。
6. `list_chapters` 仍**不自动写**细目（保持零写入语义），`would_auto_build` 只是提示；真正生成发生在 build 路径。

## 8. 建议测试矩阵行（Lead 改 `tests/matrix.py`）

当前矩阵最大编号 F108 → 建议:

```python
dict(id="F109", feature="预习后端 v2（按章多产物 + 小节细分 + 提示词重写）",
     entry="core/preview.py::build_chapter_preview / preview_status / "
           "read_preview_artifact / ensure_sections_artifact；CLI tools/preview_build.py",
     kind="write", priority="P1",
     tests=["test_new_schema_validation_and_artifact", "test_legacy_key_points_fallback",
            "test_multi_chapter_paths_status_and_read", "test_dry_run_writes_nothing",
            "test_ensure_sections_missing_no_fake", "test_prompt_contract"],
     evidence="json"),
```

## 9. 证据文件（`_work/preview-core/`）

| 文件 | 内容 |
|---|---|
| `real-verify.json` | doc#50 沙箱验证全步骤（42 章 / 569 节 / dry-run / 只读边界 / model_calls=0） |
| `verify_real.py` | 可重跑的真实副本验证脚本 |
| `test17-runall.txt` | `run_all.py --only test_17`（6/6、退出码 0） |
| `full-suite.txt` | 全量回归输出 |
| `sandbox/` | 沙箱副本 DB + 生成的 `50-章节细目.json`（真实库只读快照） |
| `v2_block.py` | 本次追加到 `core/preview.py` 的 v2 代码块（便于 review/回溯） |
