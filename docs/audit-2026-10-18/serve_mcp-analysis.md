# tools/serve_mcp.py 候选对比与重建方案

> 审计快照：2026-10-18 16:52，HEAD **d10941f**（含 `935e757` 对 serve_mcp 的重建）。
> 相关证据：`_work/audit/badfiles-serve.json`、`_work/audit/evidence-snapshot.txt` 第 7/9 节。

## 0. 结论（先看这段）

1. **当前 HEAD 的 serve_mcp.py 可用**：23,631B / 570 行、可编译、7 个顶层函数、**14 个 MCP 工具** + `--selftest`，`initialize` / `tools/list` / `tools/call` / `notifications/initialized` / `isError` 协议要素齐全，尾部有 `if __name__ == "__main__"`。基线 `04835f9` 的断头版本已被 `935e757` 替换（git diff：+299/-30 行 + 新增 `tools/verify_mcp_handshake.py` 189 行）。
2. **4 个恢复源候选全部不可用**：不是"哪份最完整"的问题 —— 它们**同一断点、全部文件头丢失**（第 1 行就是 TOOLS schema 中段），且尾部也被截断（最后一行是 `else:`）。任何"选一份/拼接"的方案都补不出模块头（imports、TOOLS 声明起始、`_hit_brief`/`log` 等函数、`serve` 的 stdio 循环）。
3. **没有字节码真值**：v1.1 的 PYZ 里**不存在** `serve_mcp.pyc`（MCP 是 v1.1 之后的功能），`C:\Users\Lenovo\StudyDashboard.exe_extracted\**\serve_mcp.pyc` = 0 命中。⇒ 无法逐像素复原原版；只能按"工具清单 + 协议约束"重建，并用握手/工具冒烟验收。
4. **当前重建版的工具集是 4 份候选的并集 + 合理外推**：候选里明确出现的工具名 8 个（final/rec2）/ 5 个（rec/manual），当前 14 个 = 8 个 + 6 个与 `core` / `tools/study.py` 命令一一对应的工具（docs/outline/chunk/kp_add/kp_list/mistake_add/review_grade 等）。没有"候选有、当前缺"的工具。
5. **建议**：保留当前版本，**验收走 `tools/verify_mcp_handshake.py` + 工具冒烟**（见 §4）；若将来要逐字复原，最后手段是解压 DSH 会话库（`C:\Users\Lenovo\.dsh\sessions\**\session.v4.jsonl.zstd`）找当时的 write/edit 全文，而不是从这 4 份残缺快照拼。

## 1. 候选版本清单（实测）

| 候选 | 路径 | 字节 | 行数 | 可编译 | 顶层 def | 头 | 尾 | 工具名（可从源码提取到的） |
|---|---|---:|---:|---|---|---|---|---|
| **当前（HEAD）** | `D:\study_agent\tools\serve_mcp.py` | 23,631 | 570 | **是** | `log, _hit_brief, call_tool, handle, serve, selftest, main` | 模块 docstring 完整 | `if __name__ ... raise SystemExit(main())` | **14 个**（见 §2） |
| rescue-final | `...\study_agent-final\project\study_agent\tools\serve_mcp.py` | 14,575 | 330 | 否 | —（SyntaxError） | `"before": {"type": "integer"...}`（schema 中段） | `...ok += 1` / `else:`（selftest 中间） | 8: kb_ingest, kb_note_list/read/save, kb_review_queue, kb_search, kb_stats, selftest |
| `_需要人工补充` | `...\study_agent-final\_需要人工补充\study_agent\tools\serve_mcp.py` | 12,289 | 275 | 否 | — | `"right_answer": {"type": "string"},`（schema 中段） | 同上（截断） | 5: kb_ingest, kb_note_list, kb_search, kb_stats, selftest |
| rescue-recovered2 | `...\study_agent-recovered2\project\study_agent\tools\serve_mcp.py` | 12,651 | 284 | 否 | — | 与 final 同一断点（`"before"...`） | 同上（截断） | 8（同 final） |
| rescue-recovered | `...\study_agent-recovered\project\study_agent\tools\serve_mcp.py` | 12,289 | 275 | 否 | — | 与 manual 逐字节相同（md5 `6108A77DAD9F...`） | 同上 | 5（同 manual） |
| rescue-flat | `D:\harness\rescue\recovered\tools\serve_mcp.py` | 4,158 | 85 | 否 | — | `<type>file</type>`（是一段工具输出/文件片段，不是源码） | `(Showing lines 350-429 of 536...)` | 0（无源码结构） |

可复现命令：

```powershell
cd D:\study_agent
.\venv\Scripts\python.exe -X utf8 _work\audit\badfiles_serve.py
# 输出（节选）：
#   current 23631B 570L ok=True  defs=['log','_hit_brief','call_tool','handle','serve','selftest','main']
#   final   14575B 330L ok=False err=unexpected indent (line 1)
#   rec2    12651B 284L ok=False err=unexpected indent (line 1)
#   rec     12289B 275L ok=False err=unexpected indent (line 1)
#   manual  12289B 275L ok=False err=unexpected indent (line 1)
#   flat     4158B  85L ok=False err=unmatched '}' (line 6)
# v1.1 PYZ 内 serve_mcp.pyc：[]   （不存在）
```

### 1.1 为什么"拼接"不可行

- **头部**：4 份候选的第 1 行都是某个工具的 JSON schema 中段（`before` / `right_answer`），前面缺的是：版权头、模块 docstring、`from __future__`、6-7 个 import、`log()`、`_hit_brief()`、`TOOLS = [` 的起点与前 1-2 个工具定义。这些内容无一份候选提供。
- **尾部**：4 份候选的末行都是 `else:`（`selftest()` 的循环分支中间）——缺 `call_tool` 里未知工具的兜底、`handle` 的 `tools/call` 返回、`serve()` 的 stdin/stdout 主循环、`main()`、`__main__` 入口。
- **中间**：`call_tool` 的实际分支随版本演进（final/rec2 8 个工具 vs 当前 14 个），拼接会把旧版 `TOOLS` 声明和新版 `call_tool` 分支错配。
- 结论：**拼接无法产出可编译版本**；当前 HEAD 的重建版是"按协议重新实现"，不是"拼回原文"。

## 2. 当前版本工具集（14 个）与映射

| 工具 | 说明 | 对应实现 |
|---|---|---|
| `kb_search` | 知识库检索 | `core.search.search()`（RRF 融合） |
| `kb_docs` | 资料列表/统计 | `core.db` + 文档表查询 |
| `kb_outline` | 文档大纲 | `core.search.doc_outline()` |
| `kb_chunk` | 单块 + 上下文邻块 | `core.search.get_chunk()/neighbors()` |
| `kb_note_save` | 写/更新笔记 | `core.study.save_note()`（markdown + front-matter） |
| `kb_note_list` | 列笔记 | `core.study.list_notes()` |
| `kb_note_read` | 读笔记 | `core.study.read_note()` |
| `kb_review_queue` | 复习队列 | `core.study.due_queue()` |
| `kb_review_grade` | 记一次复习（SM-2） | `core.study.schedule()/record_review()` |
| `kb_kp_add` | 写知识点 | `core.study.upsert_kp()` |
| `kb_kp_list` | 列知识点 | knowledge_points 查询 |
| `kb_mistake_add` | 记错题（自动降级/提前复习） | `core.study.add_mistake()` |
| `kb_stats` | 库统计 | documents/chunks/kp 计数 |
| `kb_ingest` | 入库（默认 raw/） | `core.ingest.ingest_paths()` |
| `--selftest` | 进程内协议自测 6 步（initialize/tools/list/3×tools/call/未知工具） | 不是 MCP 工具，CLI 参数 |

- 协议层：`handle()` 覆盖 `initialize`（返回 serverInfo/capabilities）、`notifications/initialized`（无回复）、`tools/list`、`tools/call`（未知工具返回 JSON-RPC error；工具异常放 `result.content` + `isError: true`）、`ping`；`serve()` 逐行读 stdin、每次写一行 JSON 到 stdout、日志强制 stderr。
- 与历史候选对照：候选只暴露 8 个工具名的"可见片段"，当前 14 个工具覆盖了 `tools/study.py` CLI 的全部只读/写入命令面，是合理超集；**没有发现候选里有、当前缺失的工具名**。

## 3. 重建方案（当前已采用，供复核）

```text
方案 A（当前 HEAD 已采用）：
  1) 以 4 份候选残片为"字段级参考"（JSON schema 的字段名/中文描述可直接复用）；
  2) 协议骨架按 MCP 规范重写：initialize / notifications/initialized / tools/list /
     tools/call / ping；stdout 只走 JSON-RPC，日志全部 stderr；
  3) 工具实现全部落回 core 现有 API（不新写业务逻辑）；
  4) 附 --selftest + tools/verify_mcp_handshake.py（真 subprocess + 逐行 JSON 断言）。
  优点：可编译、可运行、工具面完整、不依赖残缺快照；缺点：无法证明与原版逐字一致
  （v1.1 无 pyc，原版 MCP 为 v1.1 后功能）。

方案 B（仅当需要逐字复原）：解压 DSH 会话库
  C:\Users\Lenovo\.dsh\sessions\**\session.v4.jsonl.zstd
  在其中找 serve_mcp.py 的 write/edit 调用（含完整文件内容），按变更序列回放。
  成本：需 zstd 解压 + 会话检索；本次未做（超出 P1 审计范围）。

方案 C（不推荐）：从 4 份快照拼接 —— 见 §1.1，不可行。
```

## 4. 验收步骤（交给 verifier / lead）

```powershell
cd D:\study_agent
# 1) 语法与导入
.\venv\Scripts\python.exe -X utf8 -c "import py_compile;py_compile.compile(r'tools\serve_mcp.py',doraise=True);print('compile OK')"
.\venv\Scripts\python.exe -X utf8 -c "import ast;ast.parse(open(r'tools\serve_mcp.py',encoding='utf-8').read());print('ast OK')"

# 2) 进程内协议自测（不写库；只读查询：kb_search/kb_note_list；无 API 调用）
.\venv\Scripts\python.exe -X utf8 tools\serve_mcp.py --selftest
# 期望最后一行：结果: 6/6；若有失败按输出定位

# 3) 真 subprocess 握手（lead 新建的验收脚本）
.\venv\Scripts\python.exe -X utf8 tools\verify_mcp_handshake.py
# 期望：RESULT: PASS（会真的起 stdio 子进程、逐行断言 JSON-RPC）

# 4)（可选）模拟 MCP 客户端逐个工具冒烟
#    注意：kb_ingest 会写真实库，验收时不要调用；其余只读工具可在临时数据根上跑。
```

> 注意：
> - `kb_ingest` 默认 `core.config.RAW_DIR` 入库（会写库/索引），**验收时避免直接调用**，或按 verifier 的隔离环境（临时数据根）运行。
> - `--selftest` 的 `kb_search` 查询固定为 `"三次握手"`（即使库里没有资料也应正常返回空结果而不是崩溃）；`kb_note_list` 只读 notes 目录。

## 5. 未核实 / 风险

| 风险 | 说明 | 建议 |
|---|---|---|
| 语义偏差 | 当前实现是按工具名 + core API 外推，无法与"原版"逐字对齐（无 pyc） | 以握手 + 每个工具一次真实调用结果为验收标准 |
| 工具集边界 | 原版是否有第 15+ 个工具未知（候选残片只覆盖 8 个名字） | 产品方确认；以 `tools/study.py` 命令面为界目前是 14 个 |
| stdout 污染 | MCP 要求 stdout 只走 JSON-RPC；core 层的 `print` 若未重定向会破坏协议 | 已用 `contextlib.redirect_stdout(stderr)` 包裹工具调用；握手脚本可验证 |
| 日志/错误通道 | 未知工具返回 JSON-RPC error，工具异常返回 `isError:true` 的 content（模型可见错误） | `--selftest` 第 6 步已覆盖未知工具分支 |
