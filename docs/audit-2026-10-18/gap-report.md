# P1 源码完整性审计报告（对照灾后恢复源）

> **审计快照**：2026-10-18 16:52 +08:00，工作树 HEAD **d10941f**（= baseline `04835f9` + `935e757` 修 serve_mcp + `d10941f` 加 healthcheck）。
> 审计期间 lead 仍在提交，产品文件 md5 以 `_work/audit/snapshot-manifest.txt` 为准；若文件再变需重跑 `_work/audit/` 下脚本。
> **范围**：`core/*.py`（25）、`tools/*.py`（162）、根目录探针；对照 4 个恢复源树、`_work` 重建产物、v1.1 发布包 PYZ 里的 `.pyc` 真值。
> **产出**：本报告 + `gap-report.json`（机读）+ `tools-inventory.md`（tools 全量分类）+ `serve_mcp-analysis.md`（MCP 重建方案）+ `evidence-snapshot.txt`（命令与原始输出证据）+ `*.json` 明细。

---

## 0. TL;DR（给 Lead 的 10 条）

1. **没有发现"大段源码丢失"**：v1.1 发布版字节码真值共 **22 个模块 / 781 个函数 / 457,896 字符**，当前树函数缺失 **13 个（1.7%）且全部集中在 `core/search.py`**；字符串内容整体保留率 **96.64%**。项目笔记"恢复率约 85%"的说法偏悲观（对 v1.1 文本而言），但 **search.py 一项确实只有 32.7%**。
2. **P1 真 bug（静态可证）**：`core/kp_build.py::_page_stats` 在 L724-738 被拼进了 `render_materials` 的 15 行代码，引用未定义的 `win/lines/pages/pp/cur/fig` ⇒ 调用即 `NameError`（调用点 L1371，`estimate_v1` 路径）。干净参照在 `_work/rebuilt/kp_build/kp_build.rebuilt.py`（42 行版），修法明确。
3. **P1 结构缺口**：`core/search.py` 是 AI 重建版而非原版 —— v1.1 真值 32 个函数缺 13 个（含 `_flag_confidence` 置信度标注、`expand_neighbors` 的 5 个内部 helper）、`expand_neighbors` 当前 49 行 vs 真值 195 行、SQL/融合逻辑与 v1.1 不同。**无任何完整源码可用**（v1.1 只有 pyc）⇒ 要么用 `ai_decompile.py` 从 `core/search.pyc` 重建，要么保留现版并做检索质量回归。
4. **P1 交付缺口**：`tools/run_figure_desc.py` 是**断头文件**（2,228B，从 `def main()` 开始，无 imports、无 `if __name__` 尾部，运行时 `NameError`），而它被 `release_pack.py` 白名单列为 R3 用户工具 ⇒ 打包会带坏工具。
5. **P2 发布白名单缺 4 个工具**：`TOOLS_RELEASE` 声明的 `split_multippt.py` / `make_test_files.py` / `ingest_figures.py` / `restore_figures.py` 在当前树和所有恢复源中**均不存在**（全盘搜索 0 命中）。发布前必须重写或从白名单移除。
6. **已修复项**：`tools/serve_mcp.py` 在基线 `04835f9` 里是断头 JSON 片段（14,575B，第 1 行就是 `"before": {...}`）；lead 已在 `935e757` 重建为 23,631B/570 行、可编译、14 个 MCP 工具 + selftest（**本审计确认该修复成立**，详见 `serve_mcp-analysis.md`）。
7. **7 个语法坏文件全部是开发/验收脚本**（`verify_*`/`check_*`/`smoke_test`/施工临时），**所有恢复源都是同一坏版本**（逐字节 md5 相同），无救回可能；它们被 `release_pack.py` 的排除规则挡在发布包外，对运行时零影响 ⇒ 建议归档，不必修复。
8. **当前版本普遍比恢复源新，不要回退**：11 个文件与 rescue-final 不同（core 8 个 + dashboard/serve_mcp/study.py），均有证据表明是 10-15/16 的修复或 C-0087+ 新功能（如 `kp_build.py` 5670 行 vs final 3890 行、`study.py` 1204 vs 487）；2 个文件（`core/layout.py`、`core/tables.py`）在 rescue-final 中缺席但 v1.1 pyc 中有真值且当前实现保留率 100%。
9. **dashboard.py 完整**：v1.1 真值 169/169 函数齐全、字符串保留 99.05%、内嵌 HTML 按行匹配 99.9%（274,587→280,085 字符，是新增不是丢失）、路由 70 条（真值 67；唯一"缺失"的 `/api/mistake` 已更名为 `/api/mistakes`）。
10. **恢复源自身也残缺**（已如实标注）：`study_agent-final` 的 core+tools 里有 **14 个文件语法不过**（core 4：`kp_batch/kp_build/outline/search`；tools 10：含 `dashboard.py`、`serve_mcp.py`、`study.py` 与 7 个已知坏文件）；`_需要人工补充/` 里 4 个 core 文件与残缺版相同；`serve_mcp.py` 的 4 个候选全部断头且尾部被截；**v1.1 PYZ 内没有 `tools/` 的 pyc**（`serve_mcp.pyc` 不存在），MCP 无字节码真值。

---

## 1. 方法与证据资产

### 1.1 对照源（全部只读）

| 源 | 路径 | 规模 | 本次用法 |
|---|---|---|---|
| rescue-final | `D:\harness\rescue\study_agent-final\project\study_agent` | 311 py（core 23 / tools 160） | 主对照源（逐字节 md5） |
| rescue-recovered2 | `D:\harness\rescue\study_agent-recovered2\project\study_agent` | 311 py | 第二对照源 |
| rescue-recovered | `D:\harness\rescue\study_agent-recovered\project\study_agent` | 76 py | 早期小样本对照 |
| rescue-manual | `D:\harness\rescue\study_agent-final\_需要人工补充\study_agent` | 15 py | 手工补充候选（多数与残缺版相同） |
| rescue-flat | `D:\harness\rescue\recovered` | 少量 | serve_mcp 等扁平候选 |
| _work 重建产物 | `D:\harness\rescue\_work\{built,rebuilt,assembled,decompiled,strings}` | 698 py + 真值 txt | AI 反编译/重建源、修复参照 |
| **v1.1 字节码真值** | `C:\Users\Lenovo\StudyDashboard.exe_extracted\`（PYZ 已提取） | 1,108 pyc（core 21 + dashboard.pyc） | **权威"原本有什么"清单**（函数名/行号/参数/字符串常量） |

> v1.1 真值时间点 ≈ 卡 C-0086（2026-10-12），比当前树旧；因此"当前比真值少"可能是新版有意改写，报告里按模块判断，不一律当丢失。

### 1.2 自写审计脚本（都在 `_work/audit/`，可复跑）

| 脚本 | 作用 | 产物 |
|---|---|---|
| `audit_ast.py` | 全树 AST 顶层 def/class + 编译检查 + 与各恢复源逐文件对比（missing/extra/行数/md5） | `ast-audit.json` |
| `deep_audit.py` | pyc 真值函数差集（含嵌套）、桩函数扫描、未定义全局引用（symtable）、JSON/复算 | `deep-audit.json` |
| `pyc_truth.py` | **直接从 v1.1 pyc** 用 marshal 提取 code 对象：函数名/行号/参数/行跨度/字符串常量 → 计算当前树保留率 | `pyc-truth.json` |
| `badfiles_serve.py` | 7 个坏文件 × 5 恢复源逐字节比对 + serve_mcp 5 候选对比 | `badfiles-serve.json` |
| `build_reports.py` | 汇总生成机读报告与 tools 清单 | `gap-report.json`、`tools-inventory.md` |
| `make_evidence.py` | 汇总全部命令/输出证据 | `evidence-snapshot.txt` |

运行方式（项目 venv，3.13.2，全程不写产品代码、不落 `.pyc`）：

```powershell
cd D:\study_agent
.\venv\Scripts\python.exe -X utf8 _work\audit\audit_ast.py
.\venv\Scripts\python.exe -X utf8 _work\audit\deep_audit.py
.\venv\Scripts\python.exe -X utf8 _work\audit\pyc_truth.py
.\venv\Scripts\python.exe -X utf8 _work\audit\badfiles_serve.py
.\venv\Scripts\python.exe -X utf8 _work\audit\build_reports.py
.\venv\Scripts\python.exe -X utf8 _work\audit\make_evidence.py
```

### 1.3 关键命令与原始输出（节选；完整见 `evidence-snapshot.txt`）

```text
$ git log --oneline -1
d10941f feat(tools): 新增一键健康检查 healthcheck.py
$ git status --short
?? tests/
$ (Get-ChildItem core,tools -File -Filter *.py).Count
core=25 tools=162
$ compile(bytes) 逐个检查（等价 py_compile，但不落 .pyc）
core+tools 共 187 个文件；语法不过 7 个：
  tools\c0089_tmp_launch.py -> invalid syntax (line 1)
  tools\c0098_audit_ui.py -> invalid character '（' (U+FF08) (line 102)
  tools\check_mindmap_kp.py -> unexpected indent (line 1)
  tools\smoke_test.py -> unexpected indent (line 1)
  tools\verify_c0039_precheck.py -> unexpected indent (line 1)
  tools\verify_c0057_browser.py -> closing parenthesis '}' does not match opening parenthesis '(' on line 329 (line 335)
  tools\verify_c0057_offline.py -> '(' was never closed (line 33)
```

```text
$ core+tools 逐字节 md5 vs 恢复源
-- rescue-final:      identical=172 differ=11 absent_in_source=4
   differ: core/extract.py, core/figures.py, core/kp_batch.py, core/kp_build.py,
           core/outline.py, core/pipeline_pool.py, core/search.py, core/study.py,
           tools/dashboard.py, tools/serve_mcp.py, tools/study.py
   absent: core/layout.py, core/tables.py, tools/healthcheck.py, tools/verify_mcp_handshake.py
-- rescue-recovered2: identical=169 differ=14 absent_in_source=4
-- rescue-recovered:  identical=26  differ=47 absent_in_source=114（早期样本，不完整）
```

```text
$ v1.1 pyc 真值（22 模块 / 781 函数）
模块             真值函数 真值末行 当前行数 缺失 截断 字符串保留率
dashboard           169   12428   12822    0    1   0.9905
search               32    1355     856   13    3   0.3269   ← 唯一函数缺失的模块
kp_build            132    4792    5670    0    0   0.8651
kp_batch             17     570     798    0    0   0.8665
study                30     970    1204    0    0   0.8986
其它 17 个模块        —       —       —     0    0   0.9892~1.0
整体（字符加权）：442,522 / 457,896 = 0.9664
```

> 注：`dashboard`、`layout`、`multippt`、`tables` 等模块在当前树更新且保留了 v1.1 全部函数；"缺失=0"表示函数名层面没有丢函数。

---

## 2. 总体结论：完整度量化

### 2.1 函数层面（对照 v1.1 字节码）

- 真值函数 **781** 个（含类方法与嵌套函数），当前树缺失 **13** 个 → **函数保留率 98.3%**。
- 缺失 **全部在 `core/search.py`**（见 5.2）；其余 21 个模块缺失为 **0**。
- 参数个数不一致 **13 处**，绝大多数是**新版加形参**（如 `kp_build.build` 15 args vs 真值 1、`ingest.ingest_file` 7 vs 6、`dashboard._rename_error` 3 vs 1），属正常演进；`search._same_theme`（真值 4→当前 2）与 `study.schedule`（真值 7→当前 3）是重建改写，需行为回归。

### 2.2 内容层面（字符串常量保留率，按字符加权）

| 模块 | v1.1 字符保留率 | 判断 |
|---|---:|---|
| 18 个模块（chat/chunk/config/db/embed/extract/figures/ingest/layout/multippt/outline/pipeline_pool/progress/share/subjects/tables/dashboard/`__init__`） | 0.989~1.0 | 无内容丢失；当前为同版或增强版 |
| `study.py` | 0.8986 | 缺的主要是 SQL 文本与 docstring 的重建改写（如 `INSERT INTO mistakes(...)`/`UPDATE knowledge_points...`），函数与表结构在 |
| `kp_batch.py` | 0.8665 | AI 重建痕迹（文案/重建注记改写），函数齐全（历史缺的 5 个内部函数已补） |
| `kp_build.py` | 0.8651 | 缺的主要是长 docstring（v1/v3 管线说明、覆盖审计口径等），另有 3 处 `TODO(反编译存疑)`；函数齐全 |
| **`search.py`** | **0.3269** | **重建版：13 个函数缺失 + SQL/融合逻辑改写，是真缺口** |

> 结论：**"85% 恢复率"在 2026-10-14 成立，今天已不成立**——除 search.py 外，其余模块对 v1.1 的内容保留率都在 86% 以上（多为文档/文案差异），dashboard 达 99%。真正的风险已从"大面积缺源码"转移到 **search 重建质量 + 1 处拼接损坏 + 交付/发布清单缺口**。

### 2.3 "当前比恢复源新，不要回退"清单

以下 11 个文件与 rescue-final 不同，判断依据：编译通过、对**所有**恢复源 missing=0（当前是超集）、且带 10-15/16 的修复注记或新功能：

`core/extract.py`、`core/figures.py`、`core/kp_batch.py`、`core/kp_build.py`、`core/outline.py`、`core/pipeline_pool.py`、`core/search.py`（重建版，见 P1-1）、`core/study.py`、`tools/dashboard.py`、`tools/serve_mcp.py`（lead 修复）、`tools/study.py`（重建版）。

另：`core/layout.py`、`core/tables.py` 不在 rescue-final 中，但 **v1.1 pyc 有真值**且当前实现函数齐全、保留率 1.0；`tools/healthcheck.py`、`tools/verify_mcp_handshake.py` 是 10-18 新增（非灾后缺口）。
**任何"从恢复源整体覆盖"的操作都会回退这些修复，禁止**（与源码树内 `notes/灾后恢复清单` 的警告一致，本次以实测数据确认）。

---

## 3. 按风险排序的修复清单

### P1（产品/交付会出错，建议本轮修）

| # | 文件 | 问题 | 证据 | 修法 | 估算 | 验证 |
|---|---|---|---|---|---|---|
| P1-1 | `core/kp_build.py` L692-743 | `_page_stats` 被拼入 `render_materials` 的 15 行代码（L724-738），引用未定义 `win/lines/pages/pp/cur/fig`；且原跨页覆盖循环被挤出 `for ref` 循环 | symtable：模块作用域外仅这 6 个未定义引用，全在 `_page_stats`；`_work/rebuilt/.../kp_build.rebuilt.py` 同名函数 42 行且干净；调用点 L1371 | 删 L724-738；把 L739-741 移回 L722（`n_blocks +=` 之后、`for ref` 循环内） | 10 分钟 | `py_compile` + 临时库调 `estimate_v1()` 不再 NameError |
| P1-2 | `core/search.py` | AI 重建版：v1.1 真值 32 函数缺 13、保留率 32.7%、`expand_neighbors` 49 vs 195 行 | `pyc-truth.json`（missing_funcs 13 条）；`_work/strings/search.strings.txt`；调用方 chat.py/dashboard.py | 路线 A：`ai_decompile.py --pyc "...core\search.pyc"` 逐函数重建（工具链已验证过，需 LLM 费用/网络）；路线 B：保留现版，用 8 条口语化查询做 Top-1/引用正确率回归，记录已知差异 | A: ~0.3 元+人工核对；B: 1 小时 | 检索回归 + `expand_neighbors/expand_continuations` 调用点结构断言 |
| P1-3 | `tools/run_figure_desc.py` | 断头：2,228B，首行 `def main() -> None:`，无 imports/无 `if __name__`；运行时 NameError（argparse/Path/figures/config/time） | symtable 未定义全局；final/rec2/rec 同版断头（md5 `0F0B103B...`）；`release_pack.py` TOOLS_RELEASE 列为 R3 用户工具 | 按 `core/figures.py::extract_pages/build_contexts` 接口重写（约 60 行，CLI 参数可从 `dashboard.py` 注释与 `run_figure_desc` 原参数名恢复），或从 TOOLS_RELEASE 移除 | 0.5~1 天 | `--help` 可用 + 一份 pptx 小样本 dry-run（不写真实库） |

### P2（不修也能跑，但发布/质量有缺口）

| # | 文件 | 问题 | 证据 | 修法 |
|---|---|---|---|---|
| P2-1 | `tools/split_multippt.py`、`make_test_files.py`、`ingest_figures.py`、`restore_figures.py` | `release_pack.py::TOOLS_RELEASE` 声明为发布物，但**当前树与所有恢复源均不存在** | 白名单 18 项存在性检查：4 MISS；`D:\harness\rescue` 全盘按名搜索 0 命中 | 按 card/README 功能重写或从白名单摘除（**发布前必须二选一**） |
| P2-2 | `core/kp_build.py` | 3 处 `TODO(反编译存疑)`：L1466（字段推断）、L1926（循环体截断推断）、L2843（`claimed` 键结构） | 源码注释 + 真值保留率 86.5% | 对 v1/v2/v3 三条管线各做一次临时库 dry-run；重点看 L1466/L1926 所在函数输出结构 |
| P2-3 | `core/kp_batch.py` | 重建注记多处（L88/155/212/266/636/688），文本保留 86.7% | 源码注释 | 用临时数据根跑 `plan()`/`start()`（不写真实库） |
| P2-4 | `core/study.py` | SQL/文档文本与 v1.1 有改写（保留 89.9%），SM-2/deck/mistake 为重建实现 | `pyc-truth.json` missing_short | 临时库回归 review/mistake/note（列名以 db.py 为准） |
| P2-5 | `tools/study.py` | 重建版（677 行/788 总行），无 v1.1 tools pyc 可对照 | `ast-audit.json`；与 final 逐字节相同 | CLI 冒烟：check/docs/stats/search（只读命令） |
| P2-6 | 发布白名单口径 | `tools/share.py`、`tools/preview_build.py` 是用户向 CLI，但不在 TOOLS_RELEASE（可能有意排除/白名单未更新） | `tools-inventory.md` O 类 | 产品方确认（不属灾后恢复缺口） |

### P3（清理/归档，无功能影响）

| # | 对象 | 处置 |
|---|---|---|
| P3-1 | 7 个语法坏文件（下表 5.6） | 移入 `tools/_archive/` 或删除；确认无发布白名单引用（已确认 `verify_`/`check_`/`smoke_test` 被排除规则挡住） |
| P3-2 | `tools/find_empty_figures.py`、`tools/verify_c0048_window.py`（均 0 字节） | 删除 |
| P3-3 | 代码注释中引用但不存在的开发工具：`bench_embed.py`、`check_env.py`、`diag_heading_verdict.py`、`find_missing_overviews.py`、`show_table_recovery.py`、`verify_c0041_rootcause.py`、`verify_c0056_browser_guard.py` | 仅注释/历史引用，非运行必需；随源码交付时接受即可 |
| P3-4 | `_tmp_c0074/src/core/chat.py`（1,499B，语法不过） | 临时目录残留，非产品文件，删除 |

### 已完成（本审计确认）

- **`tools/serve_mcp.py`**：基线 `04835f9` 为断头 JSON 片段；`935e757` 重建为可编译的 570 行版本（14 工具 + selftest + stdio/JSON-RPC 协议要素齐全）。建议补跑 `tools/verify_mcp_handshake.py` 做验收（见 `serve_mcp-analysis.md`）。

---

## 4. 逐文件结论（产品必需文件）

> `行数` 为总行数（含空行，`read_bytes().count('\n')+1`）；`真值` 为 v1.1 pyc 函数名比对；"无真值"= v1.1 PYZ 中无对应 pyc。完整字段见 `gap-report.json`，tools 全量见 `tools-inventory.md`。

| 文件 | 行数 | 编译 | v1.1 真值函数 / 缺失 | 字符保留 | 结论 / 风险 |
|---|---:|---|---:|---:|---|
| core/chat.py | 1090 | OK | 34 / 0 | 1.0 | 与 v1.1 完全对齐；无需行动 |
| core/chunk.py | 692 | OK | 25 / 0 | 1.0 | 无需行动 |
| core/config.py | 1052 | OK | 33 / 0 | 1.0 | 增强版（v1.1 后新增），无需行动 |
| core/db.py | 675 | OK | 17 / 0 | 1.0 | 无需行动 |
| core/embed.py | 119 | OK | 10 / 0 | 1.0 | 无需行动 |
| core/extract.py | 1319 | OK | 37 / 0 | 0.994 | 增强版；`convert_legacy.ps1` 在树；无需行动 |
| core/figures.py | 2111 | OK | 57 / 0 | 0.995 | 增强版；`render_pptx_slides.ps1` 在树；无需行动 |
| core/ingest.py | 1173 | OK | 25 / 0 | 0.994 | 参数演进（ingest_file +1），无需行动 |
| core/kp_batch.py | 798 | OK | 17 / 0 | 0.867 | 重建版，历史缺函数已补齐；P2-3 冒烟 |
| core/kp_build.py | 5670 | OK | 132 / 0 | 0.865 | **P1-1 `_page_stats` 拼接损坏 + P2-2 三处存疑 TODO** |
| core/layout.py | 312 | OK | 13 / 0 | 1.0 | 无需行动 |
| core/multippt.py | 237 | OK | 7 / 0 | 1.0 | 无需行动 |
| core/outline.py | 655 | OK | 16 / 0 | 1.0 | 早期断裂已修；无需行动 |
| core/pipeline_pool.py | 759 | OK | 30 / 0 | 0.989 | 无需行动 |
| core/preview.py | 1294 | OK | 无真值 | — | C-0086 后新增模块，与 final/rec2 同版；无需行动 |
| core/progress.py | 122 | OK | 7 / 0 | 1.0 | 无需行动 |
| **core/search.py** | 856 | OK | 32 / **13** | **0.327** | **P1-2 重建缺口** |
| core/sections.py | 983 | OK | 无真值 | — | 新增模块，同 final；无需行动 |
| core/share.py | 1491 | OK | 43 / 0 | 0.996 | 无需行动 |
| core/study.py | 1204 | OK | 30 / 0 | 0.899 | P2-4 行为回归 |
| core/subjects.py | 514 | OK | 27 / 0 | 1.0 | 无需行动 |
| core/tables.py | 644 | OK | 20 / 0 | 1.0 | v1.1 有真值，当前完整；无需行动 |
| core/teach.py | 857 | OK | 无真值 | — | 新增模块，同 final；无需行动 |
| core/textbook.py | 1225 | OK | 无真值 | — | 新增模块，同 final；无需行动 |
| tools/dashboard.py | 12821 | OK | 169 / 0 | 0.991 | 完整（HTML 99.9%、路由 70）；无需行动 |
| tools/study.py | 788 | OK | 无 tools 真值 | — | 重建版；P2-5 CLI 冒烟 |
| tools/serve_mcp.py | 569 | OK | 无真值（v1.1 无 MCP） | — | 已修复；见 `serve_mcp-analysis.md` |
| tools/kp_build.py | 170 | OK | 无真值 | — | 与 final 逐字节相同；无需行动 |
| tools/terms_build.py | 120 | OK | 无真值 | — | 同上 |
| tools/reimport_all.py | 216 | OK | 无真值 | — | 同上 |
| tools/share.py | 274 | OK | 无真值 | — | 同上 |
| tools/preview_build.py | 157 | OK | 无真值 | — | 同上 |
| **tools/run_figure_desc.py** | 50 | OK（语义坏） | 无真值 | — | **P1-3 断头** |

---

## 5. 重点专题与证据

### 5.1 v1.1 字节码真值怎么取的（可复现）

v1.1 的 PyInstaller PYZ 已被前序救援工作提取到 `C:\Users\Lenovo\StudyDashboard.exe_extracted\`（`dashboard.pyc` + `PYZ.pyz_extracted\core\*.pyc` 共 21 个 core 模块）。Python 3.13 可直接 `marshal.loads(raw[16:])` 读 code 对象：

```text
$ python -c "... raw=p.read_bytes(); assert raw[:4]==b'\xf3\x0d\x0d\x0a'; co=marshal.loads(raw[16:])"
dashboard.pyc size 656718 magic f30d0d0a -> <module> nconsts 328  （OK）
core pyc: chat chunk config db embed extract figures ingest kp_batch kp_build layout
          multippt outline pipeline_pool progress search share study subjects tables __init__
```

从 code 对象可拿到：`co_qualname`（函数/类/嵌套）、`co_firstlineno`（原始行号）、`co_argcount`、`co_lines()`（行跨度）、`co_consts`（全部字符串常量，含 INDEX_HTML 与 docstring）。`pyc_truth.py` 用它算"当前树相对 v1.1 的函数缺失/行跨度异常/字符串保留率"。

> 局限：v1.1 是**旧版**；它没有 `tools/` 的 pyc（`serve_mcp.pyc` 不存在），也没有 4 个新 core 模块（preview/sections/teach/textbook）。这些文件只能做恢复源对照。

### 5.2 `core/search.py` 缺口细节（P1-2）

```text
真值函数 32，当前缺 13：
  _is_figure_hit, _base_of, _path_parts, _is_prefix, _flag_confidence,
  expand_continuations.fetch / .topic_candidates,
  expand_neighbors.base_of / .group_of / .same_theme / .rows_near / .wrap / .expand_one
行跨度异常：expand_neighbors 当前 49 行 vs 真值 195（0.25x）；
            expand_continuations 64 vs 182（0.35x）；_same_theme 15 vs 38（0.39x）
字符串保留率：0.3269（去重后 75 条字符串常量中大量缺失，含 SQL、置信度说明、主题链判据）
```

当前文件里这些名字**完全没有定义**（全仓 grep `same_theme`/`base_of`/`group_of`/`rows_near`/`expand_one`/`topic_candidates`/`_is_figure_hit`/`_path_parts`/`_is_prefix`/`_base_of` = 0 命中），说明不是"搬到别处"，而是重建时被简化/内联了；`_flag_confidence` 只在 L95 注释里出现。**无完整源码**（`_work/search_rebuilt.py` 就是当前版前身，798 行；rescue-final 471 行是早期残缺版）。

### 5.3 `core/kp_build.py::_page_stats` 拼接损坏（P1-1）

证据链（`evidence-snapshot.txt` 第 4 节）：

1. `symtable` 全文件扫描：被引用、非局部、非自由、且模块级未定义的名字**只有 6 个**：`win, lines, pages, pp, cur, fig`，且全部出现在 `_page_stats`（L692-743）。
2. 当前 `_page_stats` 52 行；其中 L724-738 是一整段 `for p in win: lines.append('===== 第 %d 页 =====' ...)`，明显属于 `render_materials()`（后者 L875 起，签名参数正是 `win, pages, ch, cur, parent_pages, carry_pages`）。插进去的这段还引用了 `_parse_ref()` 的局部量 `a/b/n`（在 `for ref` 循环外只剩最后一行值）。
3. 干净参照：`_work/rebuilt/kp_build/kp_build.rebuilt.py::_page_stats` 为 42 行，跨页覆盖循环 `for p in range(a, b+1): cover...` 明确在 `for ref, n, c in rows:` 循环体内。
4. 调用点：`core/kp_build.py:1371  st = _page_stats(doc_id, db_path)`（`estimate_v1` 旧管线）⇒ 一旦走 v1 预估即 `NameError`。

修复（无需外部源）：删除 L724-738，并把 L739-741 三行移回 L722 之后（仍在 `for ref` 循环内）。修复后 `_page_stats` 约 41-42 行，与 AI 重建版一致。

### 5.4 `tools/dashboard.py` 完整性（无缺口）

- 行数 12,821（真值末行 12,428；更大是新增内容；`Get-Content|Measure-Object -Line` 的 11,786 是非空行，不是总行数——旧报告的坑）。
- 函数：v1.1 真值 169/169 全在；唯一结构变化 `Handler.do_POST` 19 行 vs 真值 268 行，是重构为委托 `Handler._do_post`（当前新增方法），非截断。
- 内嵌 `INDEX_HTML`：当前 280,085 字符 vs v1.1 274,587；按行匹配 **99.9%**（新增了 C-0087+ 的界面改动，符合"恢复版 HTML 比 v1.1 新"的历史记录，**不能整块替换回 v1.1**）。
- 路由：当前 70 条；v1.1 67 条。真值独有 `/api/mistake` 在当前为 `/api/mistakes`（前端 L5292 调用 `/api/mistakes`，一致）；当前新增 4 条（如 `/api/firstrun`、`/api/ingest/textbook` 等）。

### 5.5 `tools/serve_mcp.py`（专文见 `serve_mcp-analysis.md`）

- 基线 `04835f9`：`14575B/301 非空行`，第 1 行即 `"before": {"type": "integer", ...}`（JSON schema 中段），**文件头丢失**，`py_compile` 报 `unexpected indent`。
- 4 个恢复源候选（final 14575B / `_需要人工补充` 12289B / rec2 12651B / `recovered/tools` 4158B）**全部语法不过**，且尾部同样被截（最后一行是 `else:` / `(Showing lines 350-429 of 536...)`）⇒ 无一份可直接采用，也**不能靠拼接尾部修复**。
- 当前 HEAD `d10941f` 版本：`23631B/570 行`，可编译，7 个顶层函数，14 个 MCP 工具 + `selftest`，`initialize/tools/list/call_tool/notifications/initialized/isError` 协议要素齐全。
- **v1.1 PYZ 内无 `serve_mcp.pyc`**（MCP 是 v1.1 之后的功能）⇒ 无字节码真值，只能靠握手/结构验收。

### 5.6 7 个语法坏文件：逐文件结论（均"无可用恢复源"）

比对方法：badfiles_serve.py 对 final/rec2/rec/manual/flat 五个源逐字节 md5 比对。

| 文件 | 错误 | 恢复源情况 | 结论 / 处置 |
|---|---|---|---|
| `tools/c0089_tmp_launch.py` | invalid syntax (line 1) | 72B；final/rec2/manual 与当前 **md5 完全相同**；内容是一段工具报错文本（`Error: cannot read "G:\study_agent\tools\c0089_tmp_launch.py": not found`） | 纯垃圾残留 → **删除**（P3） |
| `tools/c0098_audit_ui.py` | invalid character '（'（L102，中文全角括号裸出现在代码里） | 12,303B；final/rec2/manual 与当前 md5 相同 | 历史 UI 量化抓取工具（C-0096/C-0098），无可用版本 → 归档（P3）；若必须修仅一处标点（可尝试，但无验收脚本配套） |
| `tools/check_mindmap_kp.py` | unexpected indent (line 1，头部丢失) | 当前/ final / rec2 = 12,803B 同版坏；rec/manual = 9,330B（也坏，断点更早）；flat 5,710B 坏 | 历史检查脚本（报告已注"断言的是 C-0036 时代格式，本身过时"）；dashboard 仅在注释中引用 → **归档**（P3） |
| `tools/smoke_test.py` | unexpected indent (line 1，头部丢失) | 当前/final/rec2/rec/manual = 12,689B 同版坏；flat 4,302B 坏 | 端到端验收脚本（被 release 规则显式排除）；无恢复源 → 归档（P3）。若要恢复端到端验收，需按 `verify_*` 现有脚本另建 |
| `tools/verify_c0039_precheck.py` | unexpected indent (line 1) | 当前/final/rec2/rec/manual = 2,448B 同版坏 | C-0039 历史预检脚本 → 归档（P3） |
| `tools/verify_c0057_browser.py` | 括号不匹配（L335；L329 `(` 未闭合） | 当前/final/rec/manual = 15,642B 同版坏；rec2 = 1,530B 坏；flat 8,538B 坏 | C-0057 真浏览器验收脚本 → 归档（P3）；该卡功能可用 `verify_c0057_offline`（也坏）之外的现有脚本替代验证 |
| `tools/verify_c0057_offline.py` | '(' never closed (L33) | 当前/final/rec2/manual = 1,700B 同版坏；rec 1,587B 坏 | 同上 → 归档（P3） |

> 共同结论：**这 7 个文件的损坏早于所有恢复快照**（各源与当前逐字节相同），不是本次树的问题，也不存在"从 G 盘/恢复源找回"的路径。它们全是 dev/verify 类，被 `release_pack.py::TOOLS_EXCLUDE_RULES`（`verify_`/`check_`/`smoke_test` 前缀）排除在发布包外，对产品运行零影响。**不要为它们花修复成本**。

### 5.7 发布白名单缺口 + tools 分类

- `tools/` 下 162 个 py：R（运行/发布/安装必需）11、D（开发/验收/探针）83、T（施工/临时）55、X（缺陷）10、O（其它）3；另有 `.ps1` 3、`.mjs` 18、`dashboard.py.bak*` 11。
- **P2-1**：`TOOLS_RELEASE` 18 项中 4 项文件不存在（`split_multippt.py`/`make_test_files.py`/`ingest_figures.py`/`restore_figures.py`），全恢复源搜索 0 命中。
- 运行时必需链（均已验证存在且可编译）：`tools/dashboard.py`、`tools/study.py`、`tools/serve_mcp.py`、`tools/convert_legacy.ps1`（core/extract.py L1113 直接调用）、`tools/render_pptx_slides.ps1`（core/figures.py L508 直接调用）、`tools/download_model.py`（install.cmd）。
- 详细逐文件分类/依据见 `tools-inventory.md`。

### 5.8 `_work` 既有报告的复核结论

| 旧报告断言 | 本次实测 | 结论 |
|---|---|---|
| `core/study.py` 487 行缺 SM-2/遗忘曲线/错题（缺口清单 B1） | 当前 1,204 行，真值 30 函数齐全，保留 89.9% | **已关闭** |
| `core/kp_batch.py` 缺 5 个内部函数（`_blank_task` 等） | 当前 798 行，17/17 真值函数在，5 个函数全在 | **已关闭** |
| `core/outline.py` 207 行、缺缓存层（B3） | 当前 655 行，16/16 真值函数、保留 1.0 | **已关闭** |
| `core/kp_build.py` 缺 ~900 行、12 处片段（B4） | 当前 5,670 行，132/132 真值函数；但发现 `_page_stats` 拼接损坏 + 3 处存疑 TODO | **大部分关闭，残留 P1-1/P2-2** |
| `tools/study.py` 301 行、缺 8 命令（B2） | 当前 788 行、17 个顶层函数（重建版） | **已关闭（待冒烟）** |
| `tools/dashboard.py` 缺 ~2000 行、"唯硬卡点" | 当前 12,821 行、可编译、169/169 真值函数 | **已关闭** |
| "恢复率约 85%" | 对 v1.1 文本整体保留 **96.64%**；函数 98.3%；但 search.py 单模块 32.7% | 需按模块表述，不能用一个百分比概括 |

> 旧报告（10-14）是早期状态，**不要按其中 B1-B5 直接施工**；它们的大部分缺口在 10-15/16 的修复轮次里已关闭。

---

## 6. 已知限制 / 不可核实项（如实标注）

1. **v1.1 真值是旧基线**：当前树在 C-0087~C-0124 期间持续演进，"保留率 <100%" 部分是新功能/改写导致；本报告只在函数缺失（13 个）与明确损坏（`_page_stats`、`run_figure_desc`）上判"缺口"。
2. **无真值区域**：`core/preview.py`、`core/sections.py`、`core/teach.py`、`core/textbook.py`、全部 `tools/`（除 dashboard）在 v1.1 PYZ 中无 pyc；只能与恢复源对照，无法证明"与历史版本逐字一致"。
3. **恢复源自身残缺**：rescue-final 的 core+tools 有 14 个文件语法不过（含 `dashboard.py` 655KB / `serve_mcp.py` / `study.py`）；`_需要人工补充/` 的 4 个 core 文件与残缺版相同；serve_mcp 全部候选断头且尾部截断；recovered 源只有 76 py。**本报告已对每条结论标注所依据的可用源**。
4. **动态行为未验证**：启动看板、检索质量、批量整理、AI 管线实际调用需 verifier 在隔离环境（临时数据根）实测；本审计只覆盖静态完整性 + 调用图风险（symtable NameError 扫描）。
5. **原始 DSH 会话库未解压**：`C:\Users\Lenovo\.dsh\sessions\**\session.v4.jsonl.zstd` 可能含更完整的 write/edit 内容（是逐字复原 search.py/serve_mcp.py 的最后备用路径），本次未深入（超出本任务范围/时间）。
6. **审计期间产品树仍在变动**（lead 16:44 修 serve_mcp、16:52 加 healthcheck）；以 `snapshot-manifest.txt`（187 个文件 md5）为界。

---

## 7. 交付物清单

| 文件 | 内容 |
|---|---|
| `_work/audit/gap-report.md` | 本报告（人读） |
| `_work/audit/gap-report.json` | 机读：33 个产品文件 + 7 坏文件 + 4 缺失白名单 + 2 空文件，每项含 `file/current_lines/best_source/missing_defs/extra_defs/recommendation/confidence` + v1.1 真值字段 |
| `_work/audit/tools-inventory.md` | tools/ 162 py 全量分类（R/D/T/X/O + 被引用证据 + 非 py 资产） |
| `_work/audit/serve_mcp-analysis.md` | serve_mcp 5 候选对比、重建方案、验收步骤 |
| `_work/audit/evidence-snapshot.txt` | 11 节命令与原始输出证据（本文所有数字的出处） |
| `_work/audit/{ast-audit,deep-audit,pyc-truth,badfiles-serve}.json` | 明细数据 |
| `_work/audit/{audit_ast,deep_audit,pyc_truth,badfiles_serve,build_reports,make_evidence}.py` | 可复跑脚本 |
| `_work/audit/snapshot-manifest.txt` | 审计快照的 187 个产品 py 的 md5/大小清单 |
