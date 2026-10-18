# M0-6b（产物导入看板入口）+ M0-8（移动端基础）· 施工报告

- 任务：`task-15` · 执行人：dash-fixer · 时间：2026-10-18 23:33–00:06（waveB）
- 改动文件：`tools/dashboard.py`（+136 / −2；CRLF 保持、无 BOM）+ 新建 `tests/test_12_m0_import_ui_mobile.py`
- 证据：`_work/m0/waveB/`（截图 20 张、`shots/result.json`、`api-evidence.json`、移动端探针脚本、测试日志）
- 未动：`core/`（`core/kp_import.py` 只 import 调用）、`tests/matrix.py`/`run_all.py`/其它 tests、真实数据、8756/3080、无 AI 调用、未 git commit

## 1. 结果一句话

M0-6b：知识点页「批量整理」旁新增常驻「导入已整理好的知识点（0 费用）」——preview（只读、零写入）→ 确认弹窗（本次实测「将新建 256 条、补 725 条出处」）→ apply 写库 → 列表刷新；二次 apply/预览新增 **0**（幂等），非法 `doc_id` 400、外部 `artifacts_dir` 被忽略（防注入）。M0-8：390×844 下 8 个视图 `scrollWidth == clientWidth == 390`（资料页 788→390）、首屏 `scrollY` 全 0（问答页 396/401→0）、0 JS 异常；资料页表格在 ≤560px 改卡片式。新增 7 条用例 `--only test_12` **7/7 PASS（退出码 0）**；按指示未跑全量。

## 2. M0-6b 改动（tools/dashboard.py，行号为改后）

### 后端
| 位置 | 内容 |
|---|---|
| `89` | `from core import kp_import as kp_import_mod`（只 import，不改 core） |
| `7501` | `api_kp_import_preview(conn, doc_id="")`：`kp_import.run(conn=只读连接, doc_id=...)`；返回 `{ok, **stats}`；`KpImportError → 400 JSON`（`_code` 约定） |
| `7510` | `api_kp_import_apply(conn, payload)`：`kp_import.run(conn=写连接, apply=True, doc_id=payload.doc_id)`；写锁内串行；`KpImportError→400`、`sqlite3.Error→500` |
| do_GET | 只读 `_lock` 块内新增 `GET /api/kp-import/preview`（`?doc_id=` 可选） |
| do_POST | 写锁块内新增 `POST /api/kp-import/apply`（`{"doc_id": 可选}`） |

**路径注入防护**：两个接口都**不接收** `artifacts_dir`，产物目录固定 `core.kp_import` 默认的 `config.LOG_DIR/knowledge`（env 覆盖仍走 `KB_LOG`）；实测传 `?artifacts_dir=C:\Windows` 或 POST 同字段均被忽略（见 `api-evidence.json`）。

### 前端
| 位置 | 内容 |
|---|---|
| `3342` | `kpBuildCard()` 按钮行：`#kp-build-open`（批量整理）旁新增 `#kp-import-open`「导入已整理好的知识点（0 费用）」（空态/列表两分支都渲染这张卡） |
| `3362` | `kpImportOpen()`：调 preview → 弹确认（扫描/匹配份数、将建 N 条、补 M 条出处、跳过说明 + 「不调用大模型、不联网、不花钱」）；无新增时弹「没有需要新建的知识点」（无导入按钮） |
| `3403` | `kpImportApply(btn)`：POST apply → `toast`（新建 N 条 / 已是最新）→ `go('kps')` 刷新；失败 toast + 可重试 |
| `bindKpBuild()` | 绑定 `#kp-import-open` |

## 3. M0-8 改动（同文件）

| 位置 | 内容 |
|---|---|
| `689` | 新增 `@media (max-width:560px)`：`#view table/thead/tbody/tr/td` 改卡片式（行块、单元格纵向堆叠、隐藏表头）、`code/kbd/pre` 允许断行、`overflow-wrap:anywhere` 给 `.dz-hint/.sub/.hint` 等长文本、`input/select/textarea{max-width:100%}`、`main/header` 收窄内边距 |
| `775` | 新增 `safeFocus(el)`：`focus({preventScroll:true})`，老浏览器退回普通 `focus()` |
| 问答页 | 两处自动聚焦（首屏渲染、发送完成后）改用 `safeFocus(ta)`；**首屏不再被 autofocus 顶下去** |

## 4. 验收证据（`_work/m0/waveB/`）

### 4.1 M0-6b 浏览器实测（qa-ui2 @8916，全新隔离实例；真实主库只读快照，`logs/knowledge` 9 份产物只读复制进沙箱）
| 证据 | 内容 |
|---|---|
| `shots/M0B-01-kps-import-button.png` | 知识点页「批量整理」旁两个按钮都在（`#kp-build-open` + `#kp-import-open`，`result.json::M0B-01-import-button` 抓到文案/可点） |
| `shots/M0B-02-import-confirm.png` | 确认弹窗：「扫描到 9 份整理结果，匹配库里 9 份课件。将新建 **256** 条知识点、补 **725** 条原文出处。不调用大模型、不联网、不花钱；重复导入不会重复建卡。」+「开始导入 256 条」 |
| `shots/M0B-03-import-done.png` | 点「开始导入」后回知识点页：列表从 0 → **256 条**（`result.json::M0B-03-after-apply`：`nKps=256`、modal 已关、`CURRENT='kps'`） |
| `shots/M0B-04-import-again.png` | 再点导入：弹「没有需要新建的知识点…（或知识库已是最新）」且**没有**导入按钮 |
| `shots/M0B-05-idempotent-toast.png` | 幂等 apply 的 toast：「已是最新：没有新建知识点（补出处 0 条）」（DOM 文本抓到） |
| `api-evidence.json` | 二次 `POST apply` → `kps_created=0, kps_existing=256`，`/api/kps` 数量不变 256→256；`preview?doc_id=999` → `kps_found=0`；`preview?doc_id=abc` → **400** + 中文 error；`preview/apply` 传外部 `artifacts_dir` → 被忽略，响应里 `artifacts_dir` 仍在沙箱 `logs/knowledge` 下 |

### 4.2 M0-8 移动端实测（390×844，CDP `Emulation.setDeviceMetricsOverride`；探针 `mobile_probe.mjs`）
| 指标 | 改前（`shots/baseline390/result.json`） | 改后（`shots/fixed390/result.json`） |
|---|---|---|
| docs `scrollWidth/clientWidth` | **788 / 390**（表格 747px 顶宽） | **390 / 390** |
| assistant 首屏 `scrollY` | **401**（autofocus 顶下去） | **0** |
| overview/review/kps/mistakes/graph/notes | 已是 390/390、scrollY=0 | 390/390、scrollY=0 |
| 8 视图溢出元素清单 | docs 12+ 个（table/tr/td 416~747px） | **全部空列表** |
| JS 异常 | 0 | **0** |

截图：`shots/fixed390/fixed-01…08-<view>.png`（`fixed-02-docs.png` 可见资料页全部纵向卡片、无横滑；`fixed-03-assistant.png` 首屏在顶部）。
说明：`_work/eval/shoot.mjs --width 390` 受 Chromium 最小窗口宽度（实测变 504）限制，故本任务写了 `mobile_probe.mjs` 用 CDP 真 390 视口测量/截图（与 gate v2 判据一致）。

### 4.3 自动化测试（新文件 `tests/test_12_m0_import_ui_mobile.py`，7 条）
```text
venv\Scripts\python.exe -X utf8 tests\run_all.py --only test_12
→ 7 条 / 通过 7 / 失败 0 / 退出码 0   （log：_work/m0/waveB/run_all_only_test12.log）
```
覆盖：preview `dry_run` 且零写入（只建 2 条 fixture）；apply 后 `/api/kps` +2；**二次 apply 新增 0**；`doc_id` 过滤/非法 400；`artifacts_dir` 注入被忽略；前端按钮+preview/apply/toast 接线；≤560px 表格卡片化 CSS；`safeFocus` + `preventScroll`（旧 `ta.focus()` 已不存在）。
静态检查：`py_compile` OK；内嵌 JS `node --check` OK；`git diff --check`（CRLF 按 `cr-at-eol` 计）0 告警；无 BOM、13334 CRLF=LFs。

## 5. 矩阵行建议（matrix.py 由 Lead 改）
```python
dict(id="F99", feature="M0-6b 整理产物导入看板入口（preview/apply/幂等）", entry="GET /api/kp-import/preview + POST /api/kp-import/apply", kind="write", priority="P0",
     tests=["test_12_m0_import_ui_mobile.TestKpImportApi"]),
dict(id="F100", feature="M0-6b 导入按钮与确认流程 + M0-8 移动端基础", entry="知识点页 #kp-import-open / ≤560px 媒体查询", kind="read", priority="P0",
     tests=["test_12_m0_import_ui_mobile.TestImportUiAndMobile"]),
```

## 6. 风险 / 遗留
1. **移动端是"基础"不是"完整移动版"**：≤560px 表格改卡片后表头隐藏，单元格纵向堆叠（无每格标签），信息能找到但没有列语义；更细的移动端信息架构留给后续里程碑。
2. 问答页首屏不再自动聚焦输入框（防滚动），手机用户需自己点一下输入框——这是任务书"首屏 scrollY=0"的取舍。
3. 导入 apply 会写 `meta` 导入清单（`kp_import.artifacts.v1`）且**不提供看板撤销**；幂等语义下重复执行安全，"删掉导入的知识点"需走清理/手工，建议 M1 再评估撤销入口。
4. 产物目录固定 `config.LOG_DIR/knowledge`（防路径注入）：若用户把整理产物放别处，看板不会导入（设计如此）。
5. 真库上首次导入 256 条耗时约数秒（沙箱实测 <8s）；真实库更大时是长一点的一次性操作（写锁内，期间其它写接口排队）。
6. qa-ui2 实例里 `logs/knowledge` 的 9 份产物是从真实 `logs/` **只读复制**进沙箱的（真实目录未写）；实例已 down 并删除。
7. 工作区还有本次之外的东西（`docs/长期改进计划`、`tests/matrix.py` 的改动和 `notes/` 下用户笔记）——不是我写的，提交请按路径 add。

## 7. 回滚
只涉及一个产品文件：`git checkout -- tools/dashboard.py` 即回 HEAD（waveA）；`tests/test_12_m0_import_ui_mobile.py` 可一并删。无数据迁移、无配置改动。
