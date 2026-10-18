# core/kp_build.py::_page_stats 修复独立复核（commit 1e3f604）

> 只读复核，不重跑全量审计；结论：**修复成立，P1-1 可关闭**。

1. `venv\Scripts\python.exe -X utf8 -m py_compile core\kp_build.py` → exit 0。
2. AST：`_page_stats` 现为 **L692-728（37 行）**；`for ref, n, c in rows:` 循环体内层有且仅有 1 个 `for p in range(a, b+1)`（跨页覆盖累加已移回循环内）；`for p in win:` 拼接片段 0 处（"宿主没能进入窗口"全文 0 命中）。
3. `symtable` 全模块「被引用但未定义」的全局名 = **{}**（修复前是 `win/lines/pages/pp/cur/fig` 共 6 个）。
4. 只读实跑（连接串强制 `?mode=ro`，未写任何库）：药物化学库 doc1-3 → `n_blocks=230/217/447, bad=[]`；默认库 doc31-33 → `107/118/54`；生药药动 doc2-4 → `52/138/945`。**与 lead 的 230/217/447 逐数一致**。
5. 结论：拼接污染已清除、语义恢复为"按 ref 分组 + 跨页累加覆盖"；无新增未定义引用，P1-1 关闭。
