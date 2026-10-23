# 化学结构式补全 · 真实 dry-run 报告（task-37）

- 日期：2026-10-24T01:12:10｜施工：search-tuner（施工智能体）｜**本轮 0 次真实视觉模型调用**
- 范围：9 份药理学课件（真实 `index/study.db` 的 `role=courseware` 文档）/ **381 页**
- 交付：`core/structures.py`（扫描/文本漏抽/整页图复用/选择性识图/状态）、
  `tools/structure_build.py`（CLI，默认 dry-run）、提示词真源 + tracked 副本、
  `tests/test_29_structure_scan.py`（25 条用例，全假模型）、本报告
- 结论：**候选 47 页 / 381 页 = 12.3%**（与方案讨论"结构页约占 10–20%"一致）；
  真实识图预算 **47 次调用，费用上界 ¥0.35519（闲时价）/ ¥0.71038（峰值价）**；
  若全量逐页识图（381 页）按同一口径约 ¥2.88/¥5.76。
  选择性识图省掉 **87.7%** 的调用。

## 1. 数据与现场

| 项 | 实测 |
|---|---|
| 课件文档 | 9 份（doc 40/41/43/44/45/46/47/48/49），全部 `role=courseware`、`corpus=main` |
| 总页数 | **381 页**（以 `ppt/slides/slideN.xml` 数量为准） |
| 已缓存整页图 | `logs/figure_desc/<deck>/slide_*.png`：**381 张 + 81 份旧识图 md = 462 个文件、132.2 MB**（与方案文档口径一致） |
| 缺整页图 | **0 页**（9 份课件全覆盖 → 本轮不需要任何渲染，`find_page_images` 默认不渲染） |
| 真实库 | 只读（`file:...?mode=ro`）；真实 `logs/knowledge` 本轮**未写入任何产物**（产物写在 `_work/structure/knowledge/`） |

> **doc#46 特别说明**：任务书假设 doc46 应有候选。实测它的
> `raw/4M胆碱及N胆碱受体阻断药.pptx` 是**纯文本转换件**（25 页、每页 1 个文本框、
> 0 图片/0 矢量对象/0 媒体文件），所以候选=0 是**正确结果**；扫描会给出告警：
> 「源 pptx 疑似纯文本转换件（平均每页 1.0 个形状/图片）：矢量结构式可能在旧版 .ppt 里；请用 PowerPoint 把 .ppt 另存为 .pptx 后重新扫描。」。doc45/47/40 等真实含图形/结构页的课件候选>0。
> 真实结构式若在旧版 `.ppt` 里，需先用 PowerPoint 另存为 `.pptx` 再扫。

## 2. 结构页扫描结果（逐份）

扫描规则（可调阈值，产物 `meta.thresholds` 记录实际值）：**A** 矢量元素多且文本少；
**B** 强化学关键词（结构式/母核/取代基/官能团/手性/构效关系…）；
**C** 弱关键词组合（反应/合成/取代/机理）或弱词+图形证据；**D** 组合形状+图片多；
**E** 组合形状多且含图片；**F** 嵌入 OLE 对象（ChemDraw/公式）。

| doc | 标题 | 页数 | 候选 | 比例 | 有图 | 候选页号 | 预算调用 | 上界(闲时) | 上界(峰值) |
|---|---|---|---|---|---|---|---|---|---|
| 40 | 1中枢神经系统药理学概论 | 26 | **5** | 19.2% | 26 | 3, 10, 2, 7, 22 | 5 | ¥0.0378 | ¥0.0756 |
| 41 | 镇静催眠抗焦虑药 | 46 | **2** | 4.3% | 46 | 6, 46 | 2 | ¥0.0151 | ¥0.0302 |
| 43 | 外周神经药理学概论 | 64 | **12** | 18.8% | 64 | 16, 4, 6, 14, 25, 34, 41, 43, 44, 46, 50, 51 | 12 | ¥0.0907 | ¥0.1814 |
| 44 | 3抗癫痫药和抗惊厥药 | 53 | **2** | 3.8% | 53 | 23, 36 | 2 | ¥0.0151 | ¥0.0302 |
| 45 | 3胆碱受体激动剂 | 23 | **3** | 13.0% | 23 | 8, 7, 12 | 3 | ¥0.0227 | ¥0.0453 |
| 46 | 3. 具有神经节阻断作用和促进组胺释 | 25 | **0** | 0.0% | 25 |  | 0 | ¥0.0000 | ¥0.0000 |
| 47 | 5抗精神失常药物 | 86 | **16** | 18.6% | 86 | 60, 69, 14, 65, 77, 26, 47, 64, 70, 78, 83, 16 … | 16 | ¥0.1209 | ¥0.2418 |
| 48 | 抗胆碱酯酶药和胆碱酶复活药 | 22 | **4** | 18.2% | 22 | 12, 7, 13, 16 | 4 | ¥0.0302 | ¥0.0605 |
| 49 | 1神经系统概论 | 36 | **3** | 8.3% | 36 | 26, 33, 3 | 3 | ¥0.0227 | ¥0.0453 |
| — | **合计** | **381** | **47** | **12.3%** | 381 | — | **47** | **¥0.35519** | **¥0.71038** |

校准过程（为什么不是"全量"）：最初规则把 `a:ln`（文本框描边）也算作矢量元素，
候选 143/381=37.5%；剔除 `a:ln`、并把"反应/合成/取代/机理"降为**弱关键词**（"不良反应"
含"反应"，否则会捞进 44 页普通文字页）后，候选 **47/381=12.3%**。阈值全部集中在
`DEFAULT_SCAN_THRESHOLDS`，CLI/调用方可逐项覆盖。

## 3. 费用上界与假设（写清、可复核）

- 价格源：`core/figures.py::_PRICE`（DeepSeek USD/1M tokens：峰值 miss 0.30 / out 1.20；
  闲时 miss 0.15 / out 0.60；汇率 7.1），与入库识图**同一套记账口径**；
- 输入上界：**3000 tokens/页**（取真实 `figure_desc` 表 81 页历史调用的 `prompt_tokens`
  峰值 2673 的上界）；输出上界：`max_tokens=1024`（结构 JSON 短输出，按写满算）；
- 单页上界：闲时 **¥0.00756**、峰值 **¥0.01511**；
  47 页 → **¥0.35519 / ¥0.71038**；
- 历史参照：`figure_desc` 81 页真实整页识图（长描述+开推理）平均
  **¥0.0140/页**、`tokens_in` 均值 2219、`tokens_out` 均值 3207、
  单页最贵 ¥0.0561。本管线输出是短 JSON 且默认关推理，理论上应低于历史均值；
- 真实记账：跑 `--run` 时按接口 `usage` 用 `figures.price_of()` 逐页记账，产物
  `meta.calls/cost_cny/tokens` 可逐页核对（不会用预估值冒充真实值）。

> 方案文档估算 ¥0.2–0.9（10–20% 页数）；本轮实测 12.3% → **¥0.35519–0.71038**，落在区间内。

## 4. 文本漏抽对比（python-pptx 顶层 shape 文本 vs XML 全部 `<a:t>`）

**仅报告，不改入库**。判据：XML 里某段文本 run（去空白后）在 python-pptx 顶层文本中
找不到 → 记为漏抽；`coverage_ratio = 1 - 漏抽字符/XML 总字符`。

| 汇总 | 值 |
|---|---|
| XML 文本总量 | 39332 字 |
| 漏抽字符 | **2864 字（92.72% 被顶层 shape 覆盖）** |
| 有漏抽的页 | **31 / 381 页（8.1%）** |

| doc | XML 字数 | 漏抽字数 | 有漏抽页 | 覆盖率 | 漏抽最多的页（样例） |
|---|---|---|---|---|---|
| 40 | 2285 | 352 | 3 | 0.8460 | p26 漏 324 字；p25 漏 16 字；p10 漏 12 字 |
| 41 | 5496 | 0 | 0 | 1.0000 | — |
| 43 | 5842 | 789 | 5 | 0.8649 | p56 漏 272 字；p2 漏 195 字；p57 漏 161 字 |
| 44 | 5774 | 400 | 2 | 0.9307 | p44 漏 262 字；p6 漏 138 字 |
| 45 | 2051 | 318 | 2 | 0.8450 | p5 漏 221 字；p4 漏 97 字 |
| 46 | 2979 | 0 | 0 | 1.0000 | — |
| 47 | 9380 | 380 | 15 | 0.9595 | p55 漏 79 字；p60 漏 72 字；p3 漏 50 字 |
| 48 | 2233 | 56 | 1 | 0.9749 | p13 漏 56 字 |
| 49 | 3292 | 569 | 3 | 0.8272 | p4 漏 306 字；p10 漏 195 字；p3 漏 68 字 |

结论：**约 7.28% 的文字不在 python-pptx 顶层 shape 里**（组合形状/表格/更深层文本），
印证方案文档"入库只抽到一部分文字"；是否重入库由后续任务决定（本卡只报告）。
纯线条画的结构式即使把文字抽全也无法还原——那正是下面"选择性识图"要补的部分。

## 5. 本轮"没有花钱"的证据

1. **默认 dry-run**：`build_structures(dry_run=True)` 只读扫描+算预算，0 调用、0 落盘；
   CLI 不带 `--run` 永远走扫描/预算路径。
2. **真实调用必须显式**：`--run` 前先打印预算并要求交互确认；**非交互环境必须 `--yes`**；
   实测无 `--yes` 时：`[exit code] 3` + `已取消：未发生任何模型调用。`（证据
   `_work/structure/evidence/cli-run-cancel.txt`，且未生成 `47-结构式.json`）。
3. **测试红线**：`tests/test_29_structure_scan.py` 在 `setUpModule` 把
   `core.structures._vision_call` 换成"一调就 AssertionError"的哨兵，任何测试误入真实
   调用路径都会失败；所有"真实构建"用例注入假视觉函数（固定 JSON + 固定 usage）。
4. **本轮报告全部由 dry-run 生成**：`_work/structure/summary.json` 里每份课件的
   `est_calls` 就是"如果真跑要花几次"的预算，不是已发生费用。

## 6. 测试与验证

| 验证 | 命令 | 结果 |
|---|---|---|
| 语法 | `py_compile core/structures.py tools/structure_build.py tests/test_29_structure_scan.py` | OK |
| 新用例（独立） | `python -m unittest discover -s tests -t tests -p "test_29*.py"` | **25/25 OK** |
| 新用例（门禁） | `tests\run_all.py --only test_29` | **25 条用例 25 通过 / 0 失败，退出码 0** |
| 全量回归 | `tests\run_all.py` | 第 1 次：225 条 → **220 通过、5 条 `test_24_review_ui` 失败**；第 2 次：因另一个 run_all 并发占用 `_work/verify/data` 出现 setUpModule 错误。**两次失败都与本次改动无关**：工作区里 `tools/dashboard.py` / `core/review.py` / `test_23` / `test_24` 正被其它智能体并行修改（`git status` 可见），单独跑 `--only test_24` 已 **12/12 通过**；本卡新增文件只有 `core/structures.py` / `tools/structure_build.py` / 提示词 / `test_29`，不改 dashboard/review。**最终全量以 Lead 串行为准。** | |
| CLI 证据 | `_work/structure/evidence/` | dry-run 扫描 / 持久化扫描 / 状态 / `--run` 无 `--yes` 取消（退出码 3） |

用例覆盖：扫描字段/候选>0（doc40/47）/纯文本件 0 候选+告警/阈值可调+非法阈值/
dry-run 0 写 0 调用/落盘 schema/文本漏抽/整页图复用+缺图可读/提示词真源与 tracked
副本逐字一致/占位符替换/构建 dry-run 预算/假视觉落盘 schema/缓存复用/force/limit/
失败页记录不崩/无 key 可读错误/页号越界/状态摘要/CLI 三种模式。

## 7. 产物与复现

```
core/structures.py                              # 内核（扫描/漏抽/图复用/构建/状态）
tools/structure_build.py                        # CLI（默认 dry-run；--run 才花钱）
logs/knowledge/提示词-结构式识图.md             # 提示词运行期真源
docs/prompts/提示词-结构式识图-v1-2026-10-24.md # tracked 副本（与真源逐字一致）
tests/test_29_structure_scan.py                 # 25 条用例（全假模型）
_work/structure/report.md                       # 本报告
_work/structure/summary.json                    # 9 份课件逐份预算（机器可读）
_work/structure/knowledge/<doc>-结构页.json     # 本轮扫描产物（9 份，零 AI）
_work/structure/evidence/                       # CLI 证据（扫描/状态/取消）
```

```powershell
# 只看：扫描结构页 + 预算（零 AI、不落盘）
venv\Scripts\python.exe -X utf8 tools\structure_build.py --doc 47 --dry-run
# 落结构页 JSON（零 AI）
venv\Scripts\python.exe -X utf8 tools\structure_build.py --doc 47 --scan
# 看产物状态
venv\Scripts\python.exe -X utf8 tools\structure_build.py --doc 47 --status --json
# 真实识图（花钱，先预算再确认；非交互必须 --yes）
venv\Scripts\python.exe -X utf8 tools\structure_build.py --doc 47 --run --limit 2
```

## 8. 风险与后续（未做 / 需人拍板）

1. **真实 AI 效果未验证**：本轮 0 调用，提示词 v1 的"结构描述/SMILES/uncertainty"质量
   需挑 1 份课件人工抽检（建议 doc47 的 16 个候选页，预算 ¥0.12/¥0.24）；
2. **扫描召回/精度未人工标注**：12.3% 是启发式结果，建议按方案文档第 5 步
   "拿 1 份课件人工标注 10 页"再决定全库跑；阈值可逐项调；
3. **纯文本转换件（doc46）**：pptx 里没有矢量对象，需用户用 PowerPoint 重新另存为
   `.pptx` 后重扫；本轮已给可读告警，不做自动转换（不碰真实数据/不起 Office）；
4. **结构式产物尚未接入 UI/知识点管线**：本卡只做后端+CLI（按任务边界），
   UI 入口/「结构原图核对」由 Lead/前端后续接；
5. **SMILES 红线**：提示词要求"不确定就空串"，产物又带 `uncertainty/notes` 与原图路径，
   下游消费时应把结构描述当**旁证**而不是正文事实。

## 9. 矩阵建议（未改 matrix.py，按任务边界）

建议新增一行（tests 会命中 `test_29_structure_scan` 的 25 条）：

```python
dict(id="F121", feature="化学结构式补全（PPTX 结构页扫描 + 选择性识图，默认 dry-run）",
     entry="core.structures / tools/structure_build.py", kind="unit",
     priority="P1", tests=["test_structure", "test_scan_text_gaps", "test_build_structures"],
     evidence="json"),
```
