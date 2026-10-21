# 知识点小窗右侧「教材补充」栏 + 导图栏「补做教材补充」提醒（task-27）

- 任务：`task-27` · 执行人：dash-fixer · 时间：2026-10-21 19:42–20:10
- 改动：`tools/dashboard.py`（**+470 / −7 行**；CRLF 保持、无 BOM）+ 新建 `tests/test_20_textbook_supplement_ui.py`（12 条）
- 未动：`core/`（`core/teach.py`、`core/kp_build.py` 的新函数由后端智能体并行新增，本任务调用未改）、其它测试/matrix、真实数据/8756/3080；未 commit；不调 AI/网络（全部只读产物 + 本地 PDF 渲染）

## 1. 结论

知识点小窗从「左树 + 正文」升级为**三段式**：左导图树 224 + 中间原有正文 + 右侧「📖 教材补充」340（≥1280px 常驻；<1280px 头部「📖 教材」按钮开覆盖式抽屉，390 无横向溢出）。右侧栏显示：教材名、命中章名/页码、≤3 张教材页图（`/api/teach-page` 懒加载、点击新标签开原尺寸、加载失败灰块）、补充段落（页码 + ≤350 字 + 命中词 chips）、「教材补充点」chips、底部"按教材章节切块匹配，仅供参考"，以及**无教材 / 教材已入库但需补做 / 无匹配**三种人话状态。课件思维导图栏在 `needs_backfill` 时显示横幅 +「补做教材补充」按钮（POST 零模型 build → toast → 刷新为"已就绪：N 条已匹配"）；已有产物显示一行小字、无教材/无产物不打扰。整理知识点成功后通过 `kp_build.start_background(on_done=...)` 自动补做教材补充（异常吞掉，不影响整理）。**原知识点产物与其它页面行为零改动**。

## 2. 改动块（tools/dashboard.py，行号为改后）

| 位置 | 内容 |
|---|---|
| `439-511` | CSS：`.mmkp-teachbtn`、`.mmkp-teach`（340px、独立滚动）、`.mteach-*`（页图/段落/命中词/补充点/底部注释）；`@media (min-width:1280px)` 三段式常驻；`@media (max-width:1279px)` 抽屉（`right:0`/`width:min(340px,86vw)`/`mm-teach-open`、头部 `z-index:3` 保证关闭按钮可点、`padding-top:54px`）；导图栏 `.mmteach-banner/.mmteach-ready` 样式 |
| `5008-5013` | `mmRenderKp` 的 `.mmkp-shell` 加右侧 `<aside class="mmkp-teach" id="mmkp-teach">`（占位 loading） |
| `~4990` | 小窗头部加「📖 教材」按钮（`#mmkp-teachbtn`，宽屏隐藏） |
| `~5030` | 渲染前加 `pop0.classList.add('mmkp-has-teach')`，保留 `mm-teach-open` 状态；末尾 `mmTeachPaint(d, el)` + 绑定按钮 |
| `5173-5272` | `MMTEACH` 状态 + `mmTeachPaint`（并行取 `/api/textbook-supplement/kp` + `/api/textbook-supplement/status`，带过期请求保护）+ `mmTeachRender`（三状态分支 + 页图 + 段落 + chips + footer） |
| `5274-5299` | `mmTeachToggle`/`mmTeachClose`/`mmTeachGotoBackfill` |
| `5301-5360` | 导图栏 `mmTeachBackfill`（`#mm-tree` 顶部横幅/就绪行，带 doc 切换作废）与 `mmTeachBuild`（按钮置忙 → POST build → toast → 重新取状态；`MMBACK.busy` 防重） |
| `5029 附近 / 1260 附近 / 1414 附近 / 4770 附近` | 关闭/出处小窗/切知识点时清理 `mmkp-has-teach`、`mm-teach-open`、`MMTEACH.open`（不影响原有左树/正文） |
| `4304 附近` | `loadMindmapTree()` 成功后调 `mmTeachBackfill(MINDMAP.docId)`（仅课件思维导图栏） |
| `10532-10660` | Python 胶水：`api_textbook_supplement_status/kp/build`、`api_teach_page`（返回 PNG bytes + 页数/错误码）、`_teach_after_kp_build`；均防御式（缺函数/异常 → 200/4xx JSON） |
| `14347 附近` | Handler `_teach_page`：`self._send(200, png, "image/png")`；`TeachError` 转其 `.code`，其余 500 JSON |
| 路由 | GET `/api/textbook-supplement/status`、`/api/textbook-supplement/kp`（只读 `_lock` 内）；GET `/api/teach-page`（图片路由，**不进全局 `_lock`**，core/teach 自带渲染锁）；POST `/api/textbook-supplement/build`（同步零模型、不持全局锁） |
| `10710` | `api_kp_build_start`：`start_background(..., on_done=_teach_after_kp_build)`；老版不认 on_done 时 `except TypeError` 退回原调用 |

## 3. 与后端实际契约的对接

- `core.teach.supplement_status(doc_id, conn=?)`：返回 `{ok,has_artifact,has_textbook,textbooks,supplement,needs_backfill,reason}`；
- `supplement_for_kp(doc_id,kp_id)`：`{ok,available,textbook:{doc_id,title},entry:{chapter:{no,name,heading_path},pages,page_label,passages:[{chunk_id,page_start,page_end,page_label,text,score,hit_terms}],added_terms,status,note},note}`；
- `build_supplement(doc_id, force=?)`：写 `logs/knowledge/<doc>-教材补充.json`（本次实测 doc46：22 条 KP 全匹配、零模型）；
- `page_png(conn, doc_id, page, dpi)`：返回 PNG bytes + 总页数，五道安全闸（role=textbook + PDF + 路径必须在 KB 根内 + 页码范围）；`TeachError(code)` 转 4xx；
- `kp_build.start_background(..., on_done=callable)`：整理产物落盘后回调一次（回调异常被吞）。

## 4. 验收证据

### 4.1 宽屏 1440（qa-teach@8927；`result.json`、`shots/TS-*.png`）
| 检查项 | 结果 |
|---|---|
| 导图栏（已有产物） | doc46 选中、34 节点；顶部小字「教材补充已就绪：22 条知识点已匹配」，**无横幅** |
| 小窗右侧栏 | 小窗宽 **1280**、右侧 asides **340px 常驻**（`display:block`）；左树/正文/原有底部控件都在；宽屏「📖 教材」按钮隐藏 |
| 页图 | `entry.pages=[70]` → 1 张页图 `/api/teach-page?doc_id=50&page=70&dpi=110`，`naturalWidth>0` 加载成功、无灰块 |
| 内容 | 标题「📚 药理学 第6版 刘晓东」；章「第6章 作用于胆碱受体药 · p.70」；补充段落 3 段（首段 p.70、匹配分 220.1）；命中词 chips 15 个；教材补充点 8 个；底部注「按教材章节切块匹配，仅供参考；不改动原知识点」 |
| 滚动 | 右栏独立滚动（scrollH 1376 / clientH 497），正文与左树不受影响；Esc 关闭后右侧栏状态清理 |
| JS 异常 | **0** |

### 4.2 模拟「教材后入库」→ 补做（先删沙箱 `46-教材补充.json`；`shots/TS-04~07.png`）
| 检查项 | 结果 |
|---|---|
| 横幅 | 导图栏顶部出现「检测到教材《药理学 第6版 刘晓东》 已入库，可为这份课件的知识点补做教材补充（**不改动原知识点**）」+「还没有教材补充产物 → 建议补做教材补充」+ 按钮「补做教材补充」 |
| 点补做 | POST build 零模型生成 22/22 匹配 → 横幅消失、变「教材补充已就绪：22 条知识点已匹配」；随后打开小窗右侧栏 3 段落 + 1 页图正常 |
| 无教材/无产物 | 代码分支：无教材显示"本学科尚未入库教材…"；无 KP 产物不显示横幅（静态断言覆盖） |

### 4.3 窄屏 390×844（CDP 真视口，`shots/narrow/`、`narrow-result.json`）
| 检查项 | 结果 |
|---|---|
| 默认收起 | 右侧栏 `display:none`；头部「📖 教材」按钮可见；小窗 366×523 在视口内；关闭按钮点击命中 `pclose` |
| 抽屉展开 | 右侧栏 `display:block`、宽 **335**（left42→right377，在小窗内）；`documentElement.scrollWidth=390=clientWidth`；溢出元素 **0** |
| 抽屉滚动/关闭 | 可滚到底（top 927）；右上 ✕ 关闭抽屉；Esc 关小窗；全程 **0 JS 异常** |

### 4.4 测试
```text
tests\run_all.py --only test_20 → 12 条 / 通过 12 / 失败 0 / 退出码 0
tests\run_all.py（全量）        → 145 条 / 通过 145 / 失败 0 / 退出码 0（121.8s；矩阵 75 行全覆盖）
```
- `tests/test_20_textbook_supplement_ui.py`（12 条，自包含 `tests/env.py` 沙箱）：三段式容器/按钮/原有控件仍在；页图/段落/chips/底部注；三种状态文案；1280/1279 响应式与关闭按钮层级；导图栏横幅/按钮/就绪行/触发条件；Python 胶水源码断言（4 路由、`_teach_page`、`on_done`、回调函数）；错误降级运行时（status/kp/build 错参 200 JSON，teach-page 400/403/404 JSON，不 500）；其它页签与原功能仍在。
- `py_compile` OK；内嵌 JS `node --check` OK；无 BOM、14828 CRLF。

## 5. 环境与风险说明
1. **qa-teach 沙箱**：复制真实 `logs/knowledge`/`logs/figure_desc`/`logs/outline` + 真实 index 快照；用后端 `build_supplement` 在沙箱生成 `46-教材补充.json`（零模型）。为验证页图，在沙箱内造了一本 **452 页假教材 PDF**（420KB，`sandbox/raw/教材/药理学 第6版 刘晓东.pdf`）并把沙箱 DB `documents.abs_path` 指向它（真实 DB/真实 PDF **未动**）；真实部署时真实教材 PDF 本就在 KB 根内，无需此步。
2. 后端 `core/teach.py`/`core/kp_build.py` 与任务并行修改；本任务按其最终落地函数对接（联调已实际通过：build/status/kp/page 全链路）。若后端后续再改字段，`supplement_for_kp` 契约不变即可。
3. 右侧栏在所有状态（含无教材）都占 340px 宽屏空间以展示状态文案；若产品上希望"无教材时收掉"，后续一行 class 判断即可（当前按需求"无教材 → 一句人话"处理）。
4. `POST /api/textbook-supplement/build` 为同步零模型（doc46 实测秒级），不持全局 `_lock`；不会阻塞其它请求。
5. `teach-page` 每次渲染约 0.1-0.2 秒，core/teach 自带渲染锁 + 磁盘缓存；路由不进全局 `_lock`。
6. 沙箱曾出现服务被外部结束（日志无 traceback、无崩溃堆栈），重启后全链路正常；与本改动无关，报告如实记录。另有其它智能体的 `qa-teach-g` 实例在跑，未触碰。

## 6. 回滚
`git checkout -- tools/dashboard.py` + 删 `tests/test_20_textbook_supplement_ui.py` 即恢复（后端 core/teach 新函数保留但无调用方）；无数据/配置改动。
