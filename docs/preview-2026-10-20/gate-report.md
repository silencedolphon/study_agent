# 预习重做 · 独立门禁终验报告（task-25 第二段）

> 终验时间：2026-10-20 · 执行者：独立验收（QA，不写产品代码）
> 被测：**`0633d50 feat(preview): 图谱页签下线，重做为教材按章预习（后端 v2 + 新提示词 + 左树右文查看器）`**
> （HEAD，工作区干净）· 基线：[`baseline.json`](baseline.json) · 判据：[`PROTOCOL.md`](PROTOCOL.md)
> 工具：`gate_preview.py`（实例编排 + HTTP 门禁 + 零写盘快照）+ `cdp_preview.mjs`（CDP）+ `spot_check.py`/`cdp_spot2.mjs`（原有功能抽检）
> **零成本保证：全程 0 次真实大模型调用**（只读 estimate/progress、假产物、不点费用确认、start 只用不存在章节 999）。

## 0. 结论

| 验证组 | 合并前基线 | 合并后终验 | 判定 |
|---|---|---|---|
| A 图谱移除 + 预习页签 | FAIL | **PASS（4/4）** | ✅ |
| B 预习页（42 章/折叠/按钮/费用确认/未开启横幅） | FAIL | **PASS（9/9）** | ✅ |
| C 查看器（假产物 → API/左树右文/切小节/答案 details） | FAIL | **PASS（5/5）** | ✅ |
| D 零成本 API（estimate/progress/start 非法章节） | FAIL | **PASS（7/7）** | ✅ |
| E 390×844 无横向溢出 | FAIL | **PASS（1/1）** | ✅ |
| 原有功能抽检（小窗导图/笔记计数/设置中心/移动端） | — | **PASS（4/4）** | ✅ 无回归 |
| 全量回归 / 健康检查 | — | **125/125 · 矩阵 75/75 · 退出码 0；healthcheck PASS 9/0** | ✅ |

**预习重做通过：图谱彻底下线、按章预习端到端可用、零成本路径安全、原有功能无回归。**

## 1. 代码指纹与改动范围

| 项 | 值 |
|---|---|
| git HEAD | `0633d50`（工作区无产品/测试未提交改动，仅有用户数据未跟踪 notes） |
| `tools/dashboard.py` sha256 | `65b64bb675f45ef8…`（= HEAD；图谱页签下线 + 预习页/查看器前端） |
| `core/preview.py` sha256 | `1d454aee624e6869…`（= HEAD；v2 按章产物 + 新提示词/CSV schema 校验） |
| `core/config.py` sha256 | `bb0c2935ed3cf552…`（= HEAD；新增 `PREVIEW_ENABLED`/模型/强度等配置） |
| 实例 | `qa-preview`（8924）/ `qa-spot2`（8925），数据全部沙箱；已 down、端口释放 |

## 2. A 图谱移除 + 预习页签 → PASS（4/4）

- `GET /api/graph` → **404**（`{"error":"未知路径"}`）✓
- 导航项不再有「图谱」；页面无「图谱」按钮/链接/`go('graph')` 入口 ✓
- 导航出现「预习」页签且可点击打开 ✓

## 3. B 预习页 → PASS（9/9）

| 子检查 | 证据 |
|---|---|
| `GET /api/preview/options` | 200（教材列表） |
| `GET /api/preview/chapters?doc_id=50` | 200，**42 章**（章来源 chunks.heading_path，与基线 CLI 一致） |
| 第 2 章识别到假产物 | `preview.exists=true / status=done / n_subsections=3` |
| 未开启横幅 | 关闭状态下显示「🔒 预习功能未开启…请到设置中心 → AI 预习总结 打开（写入 PREVIEW_ENABLED=1）…关着时入口会直接拒绝，不会静默花钱」，含「去设置开启」 |
| 教材 doc50 | 📚「药理学 第6版 刘晓东」；42 章目录完整 |
| 42 章可折叠/展开 | 章头点击后 `.pv-chap-body` 数量变化（展开/收起各一次） |
| 每章按钮 | 无产物章「预习本章」；有产物章「查看预习 / 重新预习」 |
| 费用确认框 | 点「预习本章」→ `confirmBox`：「现在预习这一章？预计 **1 次调用**｜成本上界 **≤¥0.0350**｜章 4 页｜模型 deepseek-flash｜**确认后才开始调用外接大模型**」——**探针只读 DOM，点「取消」退出，未产生调用** |

截图：[`shots/POST-03-preview-on.png`](shots/POST-03-preview-on.png)、[`POST-04-cost-modal.png`](shots/POST-04-cost-modal.png)。

## 4. C 查看器 → PASS（5/5）

- 假产物 `logs/knowledge/50-预习总结-第2章.json`（C-0110-preview-v2 schema：meta/chapter/summary/subsections，
  3 小节含 why/core/lecture/pitfalls/self_check）经 `GET /api/preview/doc?doc_id=50&chapter=2` 返回
  `artifact.subsections` **3 条** ✓
- UI 点「查看预习」→ 左章节树 + 右文；渲染「章概览 · 3 小节」+ 3 个小节条目 ✓
- 小节内容：这一小节要解决什么 / 核心概念 / 老师会怎么讲 / 易错点 / 自测 全部可见 ✓
- 点左树第 2 小节 → 右侧内容切换（`pv-sec-head` 变化）✓
- 答案 `details.pv-qa` 点击可展开 ✓

截图：[`shots/POST-05-viewer.png`](shots/POST-05-viewer.png)。

## 5. D 零成本 API → PASS（7/7）

| 子检查 | 证据 |
|---|---|
| `GET /api/preview/estimate?doc_id=50&chapter=2` | **200**：`calls=1`、`cost_cny_max=0.137473`、`n_pages=21`、`model=deepseek-flash`、`est_rule=整章 1 次调用`；dry-run |
| estimate 零写盘 | 前后 `logs/knowledge` 文件清单/size/mtime 与 `logs/outline` 完全一致 |
| `GET /api/preview/progress` | **200**（active=false 初始态） |
| `POST /api/preview/start` 章 999 | 接口异步受理；后台线程 0.5s 内结束：`status=failed`、**`calls=0`、`cost_cny=0.0`**、`error=这份资料里找不到这一章（999）。可用的章：第1章 绪论…` |
| 非法章节无模型调用 | progress `calls=0` / cost 0（校验在调模型之前完成） |
| 非法章节不产生预习产物 | 无 `50-预习总结-第999章.json`；仅写本地 `50-章节细目.json`（纯正则缓存，0 模型成本，见风险 §8-2） |
| 无 999 产物 | ✓ |

## 6. E 390×844 → PASS（1/1）

- 预习页 `document.documentElement.scrollWidth = clientWidth = 390`，无横向溢出；章节列表/内容正常渲染 ✓
- 截图：[`shots/POST-06-mobile.png`](shots/POST-06-mobile.png)。

## 7. 原有功能抽检（不回归）→ PASS（4/4）+ 全量

| 抽检项 | 结果 |
|---|---|
| 小窗左侧导图栏 | ✓ doc46 知识点小窗 `.mmkp-side` 可见、条目加载、当前项 `.on` 高亮 |
| 笔记计数（BUG-9） | ✓ doc46「作用位点及药理作用」显示「已绑定 1 条笔记」 |
| 设置中心 | ✓ 5 管线 tab、导航分组、设置搜索框、保存按钮均在 |
| 移动端 390 | ✓ 7 视图无横向滚动、首屏 scrollY=0 |
| 全量回归 | **Ran 125 tests · OK · 退出码 0**（125 条 / 125 通过 / 0 失败 / 0 跳过；矩阵 **75/75**） |
| 健康检查 | **PASS 9 / FAIL 0 / SKIP 0** |

原始输出：[`evidence-post-console.txt`](evidence-post-console.txt)、[`evidence-run_all-post.txt`](evidence-run_all-post.txt)、
[`evidence-healthcheck-post.txt`](evidence-healthcheck-post.txt)、[`evidence-spot-console.txt`](evidence-spot-console.txt)。
浏览器全程 `Runtime.exceptionThrown = 0`（`js_errors=[]`）。

## 8. 残留风险与边界（如实说明）

1. **真实生成未执行**：门禁只走 estimate（估价）+ 假产物 + 不存在的章 999；费用确认框只读不确认，
   因此未验证真实模型输出/真实写入的端到端（Lead 已在线上 8756 做只读复核：42 章、费用确认 ≤¥0.0337、
   Esc 取消无调用、`/api/graph` 404）。
2. **「零写盘」的本地缓存例外**：`POST /api/preview/start` 后台线程在章节校验前会调用
   `ensure_sections_artifact` 生成/复用 `logs/knowledge/50-章节细目.json`（纯正则、本地、0 模型成本）；
   门禁已确认无预习产物、无模型调用，但严格意义上发生了这一个缓存文件写入。
3. **覆盖范围**：只测 doc50（42 章）+ 第 2 章假产物；其它教材/章、旧版 v1 产物兼容路径、真实章节细目
   缺失时的降级未逐一覆盖。
4. **真实看板未由 QA 重启/实操**：8756 由 Lead 重启并线上只读验证；QA 只读核对未触碰。
5. 设置中心开关在隔离实例内被门禁改写（沙箱 settings，用于验证「未开启横幅」→再开启），未触碰真实
   `config.local.json`；全程 0 次 AI 调用。

## 9. 证据索引

| 证据 | 路径 |
|---|---|
| 门禁结果（A–E 逐项 + 证据 + 截图清单） | [`gate-preview-result.json`](gate-preview-result.json) |
| 改版前基线（A–E 全 FAIL） | [`baseline.json`](baseline.json) |
| 原有功能抽检 | [`spot-result.json`](spot-result.json) |
| 判据/必须保留清单 | [`PROTOCOL.md`](PROTOCOL.md) |
| 截图（POST 6 张：主界面/预习页/开启后/费用框/查看器/390 + result；抽检 3 张 + result） | [`shots/POST-*`](shots/) |
| 基线截图（5 张 + result） | [`shots/BASE-*`](shots/) |
| 工具 | [`gate_preview.py`](gate_preview.py)、[`cdp_preview.mjs`](cdp_preview.mjs)、[`spot_check.py`](spot_check.py)、[`cdp_spot2.mjs`](cdp_spot2.mjs) |
