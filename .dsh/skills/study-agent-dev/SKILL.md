---
name: study-agent-dev
description: 继续开发 / 改进本机「学习看板」study_agent（D:\study_agent）时使用。新会话先用 tools/session_brief.py 读交接卡与长期计划，按测试门禁逐个完成任务，避免上下文丢失；适合"按计划继续做 M0/M1 任务"、"继续修学习看板功能/排版"这类请求。
whenToUse: 用户说"继续改进/开发学习看板"、"按长期计划继续"、"做 M0-x / M1-x 任务"，或要求修改 D:\study_agent 的看板功能、排版、复习/知识点/检索等模块时。
---

# study_agent 长期开发 · 会话接棒规程

> 这是一个持续数周的项目。**不要依赖对话记忆**：信文件、信测试、信 git。
> 你的职责是"接着做"，不是"重新摸底"。

## 0. 新会话开场（固定 4 步，不跳）

```powershell
cd D:\study_agent
venv\Scripts\python.exe -X utf8 tools\session_brief.py    # 简报：HEAD/计划进度/最新交接/服务/验收
venv\Scripts\python.exe -X utf8 tools\healthcheck.py      # 9 项环境检查（必须 PASS）
git status --short                                        # 不该有别人的半成品
```
然后读：`docs/交接/` 最新卡片（重点第 3、4、5 节）→ `docs/长期改进计划-*.md` 的状态跟踪表。

## 1. 选任务（一次会话只做 1–2 个）

- 从计划表的待办里挑编号最小的未完成项（M0 优先级最高）；
- 任务说明在计划 §3；每项都有"改法要点 / 验收 / 工作量"——**验收就是完成标准**；
- 动手前先看 `docs/开发工作流.md` 的约束与 `tests/README.md` 的加用例规矩。

## 2. 开发纪律

- 只改产品代码 + 该任务对应文件；不顺手重构、不扩大范围；
- 数据安全：真实 `index/ subjects/ raw/ notes/ logs/` 只读；实验一律用 `tests/` 沙箱或 `_work/eval/instances` 隔离实例；
- 不新增付费 AI 调用（入库/整理/识图会花钱；问答每轮约 ¥0.01，需明确说明）；
- 每完成一个可验证的改动就 commit（小步提交，message 写清"问题/改法/验证"）。

## 3. 完成门禁（缺一不可）

1. `venv\Scripts\python.exe -X utf8 tests\run_all.py` 全部通过（新增功能要同步加矩阵行与用例）；
2. `venv\Scripts\python.exe -X utf8 tools\healthcheck.py` PASS；
3. 该任务的"验收"逐条拿到证据（截图 / API JSON / 命令输出）；
4. 更新 `docs/长期改进计划` 状态表（状态→已完成 + 证据 + 日期）；
5. 写交接卡：`... tools\session_brief.py --checkpoint` 生成后补第 2–6 节，和代码同批 commit。

## 4. 上下文管理

- 感觉"快记不住前面了"→ 立刻停，写交接卡 + 提交，换新会话从 §0 继续；
- 大任务拆给子智能体，产出必须落文件（`_work/` 或 `docs/`），主会话只做决策与验收；
- 决策写"为什么"进交接卡第 4 节；踩坑写复现命令进第 5 节。

## 5. 禁止

- 不要整体回退到灾后恢复源（会丢新功能）；不要动 `.env`/`config.local.json` 的密钥；
- 不要删用户数据或真实 `raw/` 文件；不要在 8756 上做破坏性测试；
- 不要凭记忆声称"已完成"——以测试通过 + 证据文件为准。
