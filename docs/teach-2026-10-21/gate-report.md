# 教材补充功能 · 独立门禁终验报告（task-28 第二段）

> 终验时间：2026-10-21 · 执行者：独立验收（QA，不写产品代码）
> 被测：**`e8ed4be feat(teach): 知识点右侧教材补充栏 + 导图栏补做提醒（按章匹配、零模型、不动原知识点）`**
> （HEAD，工作区干净）· 基线：[`baseline.json`](baseline.json) · 判据：[`PROTOCOL.md`](PROTOCOL.md)
> 工具：`gate_teach.py`（实例编排 + HTTP/源码/产物 sha256/章级范围）+ `cdp_teach.mjs`（CDP：右栏/窄屏/横幅/原有功能）
> **零真实模型调用；所有 build/删产物只发生在隔离实例沙箱；未碰真实 8756 与真实 logs。**

## 0. 结论

| 验证组 | 改版前基线（HEAD a80541a） | 合并后终验（e8ed4be） | 判定 |
|---|---|---|---|
| A 后端（status/build/kp/teach-page/不动产物/章级范围） | FAIL（API 全 404） | **PASS（5/5）** | ✅ |
| B 构建完成回调（on_done + dashboard 接线 + 回退） | FAIL | **PASS（3/3）** | ✅ |
| C 前端（右侧教材栏/窄屏抽屉/横幅补做/原有功能） | FAIL | **PASS（8/8）** | ✅ |
| D 零模型 + 不动原知识点 | FAIL（无产物） | **PASS（2/2）** | ✅ |
| 原有功能抽检 | — | **PASS 4/4**（含在 C 组） | ✅ 无回归 |
| 全量回归 / 健康检查 | — | **145 条 / 144 通过 / 0 失败 / 1 设计内跳过 / 退出码 0；矩阵 77/77；healthcheck PASS 9/0** | ✅ |

**教材补充功能通过：按章匹配/零模型路径可用、右侧栏与导图补做提醒生效、原知识点产物未变、原有功能无回归。**

## 1. 代码指纹与范围

| 项 | 值 |
|---|---|
| git HEAD | `e8ed4be`（工作区无产品/测试未提交改动，仅用户数据未跟踪 notes） |
| `tools/dashboard.py` sha256 | `fcad151a715f7854…`（= HEAD） |
| `core/teach.py` sha256 | `2c682cfa46c2e7d2…`（= HEAD；补充产物/匹配/状态/逐 KP） |
| `core/kp_build.py` sha256 | `b6da4654d81156d7…`（= HEAD；`start_background` on_done 回调） |
| 实例 | `qa-teach-g:8931`（独立命名，避开其它任务 `qa-teach:8927`；已 down、端口释放） |

## 2. A 后端 → PASS（5/5）

| 子检查 | 证据 |
|---|---|
| `GET /api/textbook-supplement/status?doc_id=46` | 200，`has_artifact=true`、`has_textbook=true`（doc50）、`needs_backfill=true` |
| `POST /api/textbook-supplement/build {doc_id:46}`（沙箱） | 200，`status=done`，`n_matched=22`；产物 `logs/knowledge/46-教材补充.json`（沙箱内） |
| `GET /api/textbook-supplement/kp?doc_id=46&kp_id=KP-N03-1` | 200，`available=true`；章「**第6章 作用于胆碱受体药**」（heading_path：药理学 第6版 刘晓东 > 第6章…）、3 段 passages |
| **章级范围** | passages 页码 `[p.70]` ⊂ 该章 doc50 章节切块页集合 `70–82`；**outside=[]（无其它章页）** |
| `GET /api/teach-page?doc_id=50&page=75` | 200，`Content-Type: image/png`，PNG 魔数校验通过，**1,100,277 bytes**（PyMuPDF 渲染，零模型） |
| 不动原知识点 | build 前后既有 `logs/knowledge/*.json` **sha256 全部一致**；仅新增 `46-教材补充.json` |

## 3. B 构建完成回调 → PASS（3/3）

- `core/kp_build.py::start_background` 通过 `kw.pop("on_done", None)` 支持可选完成回调（源码含说明：不传时行为与改造前逐字相同，回调异常被吞掉不影响整理）；
- `tools/dashboard.py` 整理入口传入 `on_done=_teach_after_kp_build`，且带 `except TypeError` 回退（老版 kp_build 不认 on_done 时退回原调用）；
- `core/teach.py` 含 `build_supplement` / `supplement_status` / `supplement_for_kp` / `教材补充.json` 实现；
- 行为不变：无 on_done 的既有整理路径仍工作（由全量回归与 TypeError 回退保证）。

## 4. C 前端 → PASS（8/8）

| 子检查 | 证据 |
|---|---|
| 小窗右侧教材栏 | `#mmkp-teach` 可见；`img[src="/api/teach-page?doc_id=50&page=70&dpi=110"]`；页码 p.70；**3 段补充段落**；章行「第6章 作用于胆碱受体药 · p.70」；含「教材补充」标题 |
| 390 窄屏 | `#mmkp-teachbtn`（📖 教材）存在；点击后 `mm-teach-open` 展开右侧抽屉、页图加载；`scrollWidth=390=clientWidth` 无横向溢出 |
| 导图提醒横幅 | 删除沙箱 `46-教材补充.json` 后，`#mm-tree` 顶部出现「检测到教材《药理学 第6版 刘晓东》已入库，可为这份课件的知识点补做教材补充（不改动原知识点）」+「补做教材补充」按钮 |
| 点「补做」→就绪 | 点击后横幅消失，出现 **「教材补充已就绪：22 条知识点已匹配」** |
| 原有功能 4/4 | 小窗左侧导图（`.mmkp-side` + 高亮）、笔记计数（doc46「作用位点及药理作用」=1）、设置中心（5 管线/分组/搜索）、预习页签均正常 |
| JS 异常 | 主/横幅两轮探针 `errors=[]`（无 `Runtime.exceptionThrown`） |

截图：[`shots/POST-01-kp-teach-side.png`](shots/POST-01-kp-teach-side.png)、[`POST-02-mobile-teach.png`](shots/POST-02-mobile-teach.png)、[`POST-03-banner.png`](shots/POST-03-banner.png)、[`POST-04-banner-after.png`](shots/POST-04-banner-after.png)。

## 5. D 零模型 + 不动原知识点 → PASS（2/2）

- 补充产物 `46-教材补充.json` 内**无任何 AI 计费/调用字段**（cost/calls/usage/tokens 均不存在或为 0）；
- build 全程 0.63s（纯本地按章匹配 + 原子写 JSON），无模型调用；
- 原知识点产物 sha256 前后一致（见 §2）；全程未触碰真实数据/8756。

## 6. 全量回归与健康检查

| 门禁 | 命令 | 结果 |
|---|---|---|
| 全量回归 | `venv\Scripts\python.exe -X utf8 tests\run_all.py` | **Ran 145 tests · OK (skipped=1) · 退出码 0**；145 条 / 144 通过 / **0 失败** / 1 跳过；功能矩阵 **77/77** |
| 跳过说明 | `test_01_health.TestHealth.test_real_data_untouched` | 真实 8756 正在使用、运行期真实数据指纹变化 → 设计内跳过（非产品问题） |
| 健康检查 | `venv\Scripts\python.exe -X utf8 tools\healthcheck.py` | **PASS 9 / FAIL 0 / SKIP 0** |

原始输出：[`evidence-post-console.txt`](evidence-post-console.txt)、[`evidence-run_all-post.txt`](evidence-run_all-post.txt)、[`evidence-healthcheck-post.txt`](evidence-healthcheck-post.txt)。

## 7. 基线与终验对比

- 基线（HEAD `a80541a`，工作区施工中代码不参与，`--code-ref HEAD`）：A/B/C/D **全 FAIL**——API 全 404、无右侧栏/横幅、源码无 on_done/补充实现；
- 终验（HEAD `e8ed4be`）：A/B/C/D **全 PASS**；`needs_backfill true → build done(22 matched) → 右侧栏 3 段/页图 → 删除产物后横幅出现 → 补做后就绪` 全链路闭合。

## 8. 残留风险与边界（如实说明）

1. **真实看板未由 QA 操作**：8756 由 Lead 统一重启并做线上只读验证；QA 仅在隔离实例验证，未在真实 `logs/` 生成任何补充产物。
2. **沙箱保真处理**：真实 DB 的 `documents.abs_path` 指向真实 `raw/教材/…`（在沙箱 KB 根之外，会被教材页安全校验 403 拒绝）；门禁在实例沙箱内把 doc50 的 `abs_path` 指到沙箱内同一份教材 PDF（只改沙箱 DB），以验证真实场景行为。真实 8756 的路径天然在 KB 根内，不存在该问题。
3. **覆盖范围**：只覆盖 doc46（课件）↔ doc50（教材）一对；其它课件/教材、无 KP 产物、教材替换后 needs_backfill 等分支未逐一覆盖；
4. **页图渲染成本**：首次渲染 1 页约 0.1–0.2s 并写 `logs/teach_pages/` 缓存（纯本地 PyMuPDF，零模型），后续命中缓存；大教材批量浏览会产生缓存写盘与磁盘占用（已有缓存上限/清理逻辑）。
5. 全量 1 条跳过为真实数据在跑所致（设计内）；矩阵未覆盖 10 项为既有状态。

## 9. 证据索引

| 证据 | 路径 |
|---|---|
| 门禁结果（A–D 逐项 + 证据 + 截图清单） | [`gate-teach-result.json`](gate-teach-result.json) |
| 改版前基线（A–D 全 FAIL） | [`baseline.json`](baseline.json) |
| 判据/必须保留清单 | [`PROTOCOL.md`](PROTOCOL.md) |
| 截图（右侧栏/窄屏/横幅前/后 + result；基线同款 BASE-*） | [`shots/POST-*`](shots/)、[`shots/BASE-*`](shots/) |
| 工具 | [`gate_teach.py`](gate_teach.py)、[`cdp_teach.mjs`](cdp_teach.mjs) |
