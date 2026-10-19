# 两项新特性 · 独立门禁终验报告（task-20 第二段）

> 终验时间：2026-10-19 · 执行者：独立验收（QA，不写产品代码）
> 被测：**`c4ac345 feat: 两项用户指定改进（小窗左侧导图栏 + 课件整页切块，只增不改）`**（HEAD 即最新代码，工作区干净）
> 基线：`0f93be0`（见 [`baseline.json`](baseline.json) / [`PROTOCOL.md`](PROTOCOL.md)）· 工具：`gate_feature.py` + `cdp_a.mjs` + `spot_checks.py` + `spot_browser.mjs`

## 0. 结论

| 验证组 | 合并前基线 | 合并后终验 | 判定 |
|---|---|---|---|
| 特性 A 小窗左侧导图栏 | FAIL（无左栏/无高亮/不能就地切换） | **PASS（12/12 子检查）** | ✅ |
| 特性 B 课件整页切块 | FAIL（配置未实现，新旧均旧结构） | **PASS（6/6 子检查）** | ✅ |
| 原有功能抽检（不回归） | —（回退后基线） | **PASS（7/7）** | ✅ |
| 全量回归 / 健康检查 | — | **95 条·94 通过·0 失败·1 跳过·退出码 0；矩阵 73/73；healthcheck PASS 9/0** | ✅ |

**两项特性均验收通过，原有功能未见回归。**

## 1. 被测代码与指纹

| 项 | 值 |
|---|---|
| git HEAD | `c4ac345`（工作区无产品/测试未提交改动，仅有用户数据的未跟踪 notes 文件） |
| `tools/dashboard.py` sha256 | `e939fcfb4774c81f…`（实例沙箱 / 本地 / `git show HEAD:` 三方一致） |
| `core/search.py` sha256 | `637fae0824770a11…`（= `251886f` 回退基线，未被本次特性改动） |
| `core/ingest.py` sha256 | `194393a5f9291a9a…`（= HEAD；新增 `config.INGEST_PAGE_CHUNK` / 环境变量 **`KB_INGEST_PAGE_CHUNK`**） |
| A 验证环境 | 隔离实例 `qa-feature`（8920，真实 `logs/knowledge`+`logs/outline`+`logs/figure_desc` 只读复制；已 down） |
| B 验证环境 | 沙箱 `_work/feature-gate/ingest/`（真实 DB 快照 + 真实 raw 课件 + `logs/figure_desc` 缓存；密钥剔除，0 AI） |

## 2. 特性 A：小窗左侧思维导图栏 → PASS（12/12）

| 子检查 | 结果 |
|---|---|
| 知识点页思维导图可选 doc46（25 页 / 34 节点） | ✓ |
| 点导图节点打开小窗（`.evpop` 可见，标题「受体选择性与作用部位 KP-N03-1 末级知识点」） | ✓ |
| **小窗左侧存在思维导图栏** | ✓ `.evpop.mmkp-has-side` + `.mmkp-side`，**34 个条目** |
| **当前知识点高亮** | ✓ 恰 1 条 `.on`，文本=「受体选择性与作用部位p.1」 |
| **点左栏另一叶子：右侧标题/要点变化且不关窗** | ✓ 从第 1 条切到「按临床用途分类p.2」，`#mmkp-title`/正文变化、小窗保持 |
| **高亮跟随切换** | ✓ `before=受体选择性与作用部位p.1 → after=按临床用途分类p.2` |
| 原有小窗按钮仍在 | ✓ `mmkp-treebtn / ✕ / mmkp-prev / mmkp-next / mmkp-note / mmkp-notes / mmkp-search / mmkp-deck` |
| 390 窄屏小窗无横向溢出 | ✓ `scrollWidth=390=clientWidth`，左栏默认收起 |
| 390 窄屏左栏可收起/展开 | ✓ 收起态 + 展开按钮（`#mmkp-treebtn`）存在 |
| 截图 | `shots/POST-01-kps.png`、`POST-02-popup.png`、`POST-03-switch.png`、`POST-04-mobile.png` |

## 3. 特性 B：课件整页切块 → PASS（6/6）

沙箱课件：`5抗胆碱酶药和胆碱酶复活药.pptx`（doc48，真实 raw 一份），识图缓存命中。

| 子检查 | 结果 |
|---|---|
| 新模板块结构（`KB_INGEST_PAGE_CHUNK=1`） | ✓ **22 块 = p.1..p.22 连续**，`source_ref` 全 `p.N`，**0 区间、0「p.N 图」独立块** |
| 有图页识图文字并入同一块 | ✓ p.13 块文本 = `【毒性作用机制】…【图片理解】【本页作用】展示机制：…`；p.21 同样并入；无独立图块 |
| `=0` 逐字回退旧结构 | ✓ 24 块，refs 与基线**逐项完全一致**（`p.3-6` 等 5 个区间 + 12 个「p.13/p.21 图」块） |
| 0 AI 成本 | ✓ 两次 ingest `rc=0`、`failed=0`，耗时 13.4s / 15.0s，识图全部命中缓存 |
| 新特性实际生效 | ✓ 新 22 块（纯页块） ≠ 旧 24 块（区间+图块） |
| 不跑 AI 结构拆分 | ✓ `OUTLINE_ANALYSIS=0`；新结构按页、无 AI 调用的 outline 产物 |

## 4. 原有功能抽检（不回归）→ PASS（7/7）

| 抽检项 | 结果 |
|---|---|
| 小窗笔记计数（BUG-9 场景）：doc46「作用位点及药理作用」 | ✓ 显示「**已绑定 1 条笔记**」（kp_id=KP-N07-1，切换节点后仍正确） |
| 笔记保留：`/api/notes` | ✓ 20 篇 = 真实 notes 目录 20 篇（≥ 回退恢复的 19 篇，未丢失） |
| 回退后排版：8 视图开发者术语 | ✓ 0 命中（`logs/`、`tools/`、`卡 C-`、`doc_id`、`.json`） |
| 回退后移动端：390×844 | ✓ 八视图无横向滚动、首屏 `scrollY=0` |
| 掌握度四处一致 | ✓ list/detail/graph/stats 均 0.13，diff=0.0 |
| 搜索基线行为（回退后 M0 规则已撤） | ✓ 十题回归接口全部 200，命中 4/10（=回退基线水平，符合预期；原召回问题仍在） |

## 5. 全量回归与健康检查

| 门禁 | 命令 | 结果 |
|---|---|---|
| 全量回归 | `venv\Scripts\python.exe -X utf8 tests\run_all.py` | **Ran 95 tests · OK (skipped=1) · 退出码 0**；用例 95 条 / 94 通过 / 0 失败 / 1 跳过；功能矩阵 **73/73**；历史缺陷 7 个全转正（含 BUG-9） |
| 跳过说明 | `test_01_health.test_real_data_untouched` | 真实 8756 正在使用、真实数据指纹运行期变化 → 按设计跳过（非产品问题） |
| 健康检查 | `venv\Scripts\python.exe -X utf8 tools\healthcheck.py` | **PASS 9 / FAIL 0 / SKIP 0** |

原始输出：[`evidence-post-console.txt`](evidence-post-console.txt)（特性门禁）、[`evidence-run_all-post.txt`](evidence-run_all-post.txt)、[`evidence-healthcheck-post.txt`](evidence-healthcheck-post.txt)、[`evidence-spot-console.txt`](evidence-spot-console.txt)。

## 6. 残留风险与边界（如实说明）

1. **旧数据未重导**：真实库现有课件仍为**旧块结构**（如 doc48 仍 24 块含区间/`p.N 图` 块）；整页切块只对**新入库/`--force` 重导**的数据生效。用户旧资料的检索/引用行为不变，需要时再另做一次全量重导（会产生嵌入计算与识图缓存依赖）。
2. **识图缓存的依赖**：新模式把识图文字并入页块依赖 `logs/figure_desc` + DB 快照缓存；换机/清缓存后重导会重新产生识图费用（本次验证为 0 AI、0 费用）。
3. **特性 A 依赖整理产物**：左栏数据来自课件整理产物；无产物的课件不显示左栏（原有行为，非回归）。
4. **搜索能力仍为回退后基线**：M0 检索规则已撤，十题回归 4/10 为基线水平；本次两特性未触碰 `core/search.py`（sha256 与 `251886f` 一致）。
5. **验证边界**：特性 A/B 均在隔离实例/沙箱验证；真实 8756 未由 QA 做浏览器实操（Lead 随后统一重启并做线上只读验证）；全量测试跳过 1 条为真实数据在跑所致。
6. 本次 0 次 AI 调用；真实数据/8756/3080 未触碰；实例 `qa-feature`、`qa-spot` 均已 down，端口 8920/8921 已释放。

## 7. 证据索引

| 证据 | 路径 |
|---|---|
| 特性门禁结果（A/B 逐项 + 指纹 + 截图清单） | [`gate-feature-result.json`](gate-feature-result.json) |
| 原有功能抽检结果 | [`spot-result.json`](spot-result.json) |
| 合并前基线（A/B FAIL 判别力证明） | [`baseline.json`](baseline.json) |
| 判据文档 | [`PROTOCOL.md`](PROTOCOL.md) |
| 截图（A：4 张 + result；抽检：2 张 + result） | [`shots/POST-*`](shots/) |
| 工具 | [`gate_feature.py`](gate_feature.py)、[`cdp_a.mjs`](cdp_a.mjs)、[`spot_checks.py`](spot_checks.py)、[`spot_browser.mjs`](spot_browser.mjs) |
