---
name: study-agent
description: 本地学习助手知识库。当用户要求把资料/教材/课件入库、就自己的资料提问、查资料出处、复习知识点、记录错题、整理笔记、或问"我之前存过什么/学到哪了"时使用。知识全部来自本机 D:\study_agent 下的离线向量库，不依赖外部服务。
whenToUse: 用户提到"入库/存资料/我的资料/我的笔记/复习/错题/知识点/学到哪了"，或需要基于本地已存资料作答并给出处时。
---

# 本地学习助手（study_agent）

一套离线优先的个人知识库 + 复习系统。资料存在本机，检索走本地 ONNX 模型，
不需要联网、不上传资料、不花钱。

**核心原则：结论必须有出处。** 凡是基于资料作答，都要给出 `文档 › 章节 › 页码`。
没有检索到就说没有，不要凭记忆编造。

## 环境

解释器固定用这个（系统 `python` 是 Python 2.7，不能用）：

```
D:\study_agent\venv\Scripts\python.exe
```

命令入口（下列所有 `study.py` 均指此文件）：

```
D:\study_agent\venv\Scripts\python.exe D:\study_agent\tools\study.py <命令>
```

加 `--json` 可拿到结构化输出，便于程序化处理。加 `-X utf8` 可避免中文输出编码错误，
建议统一带上：`...python.exe -X utf8 D:\study_agent\tools\study.py ...`

## 资料放在哪

`D:\study_agent\raw\` —— 用户把 PDF / Word / PPT / 图片 / Markdown 丢进来，
然后运行 `ingest`。**raw 只增不改**，助手不要删改里面的文件。

支持：`.pdf` `.docx` `.pptx` `.md` `.txt` `.png` `.jpg` 等
旧版 `.doc` `.ppt` `.xls`：可直接入库——管线会调用本机 Microsoft Office
自动转成 `.docx/.pptx/.xlsx` 再解析（转换要几秒到几十秒；源文件保留，
转换产物与源文件同目录）。机器上没装 Office 时转换会失败并说明原因。
不支持：看板不认识的扩展名（unknown 类）。

## 三个检索层（重要）

不要一上来就用向量搜索。按代价从低到高选：

**1. 先看有什么（最省）**
```powershell
study.py docs                      # 列出所有文档
study.py docs --outline <doc_id>   # 看某文档大纲（章节+页范围+字数）
```
大纲只有几百字，却能让你判断该深入读哪一节。**多文档场景先看大纲，不要直接向量搜。**

**2. 模糊回忆（语义）**
```powershell
study.py search "为什么深层网络训练不动" -k 5
```
适合：用户用口语提问、不知道资料里怎么表述、跨文档找相关概念。

**3. 精确查找（关键词）**
```powershell
study.py search "梯度消失" --mode keyword
```
适合：术语、公式名、人名等精确匹配。

默认 `--mode hybrid` 会同时跑两路并融合，多数情况直接用默认即可。

**看完整内容**：检索结果只给切块，要看上下文用
```powershell
study.py show <chunk_id> --before 1 --after 1
```
`show` 会连相邻块一起给出，并能回溯到相邻章节。

## 抗上下文刷新：回写笔记

**这是本系统最重要的机制。** 上下文会被压缩或清空，向量检索是有损的，
但写进 `notes\` 的笔记是无损的、人可读的、下次直接读就行。

因此：**每当讨论得出一段有价值的结论，就落成笔记**，不要只留在对话里。

```powershell
study.py note save --title "第三章 优化与训练 要点" `
  --body "## 梯度消失`n- 成因：激活函数导数<1 连乘衰减`n- 解法：残差连接/批归一化/门控" `
  --refs "深度学习基础-第三章.md › 3.1 › para 3-4" --tags "深度学习,优化" --kind summary
```

读取（新会话开始时先看这里）：
```powershell
study.py note list                 # 有哪些笔记
study.py note read "第三章"         # 读某篇原文（支持模糊匹配）
```

`--kind` 取值：`note` `summary` `mistake` `cheatsheet`

## 复习与错题

```powershell
study.py kp add "梯度消失" --summary "深层网络梯度指数衰减" --subject 深度学习 --chunks 12,13
study.py kp link "链式法则" "梯度消失"        # 前置依赖
study.py review                              # 今日到期队列
study.py review 1 --grade 4                  # 打评分 0..5（>=3 算通过）
study.py kp list                             # 全部知识点与掌握度
study.py mistake add --question "..." --my "..." --right "..." --reason 概念混淆 --kp-id 1
study.py mistake stats
study.py stats                               # 看板：文档/知识点/错题/薄弱项
```

评分语义（SM-2）：`0-2` 完全不会/答错 → 重置为 1 天；`3` 勉强；`4` 正确；
`5` 轻松。间隔依次拉长为 1 天 → 6 天 → 按 ease 倍数增长。

错因标签固定用这几个：`概念混淆` `计算失误` `前置缺失` `审题错误` `记忆遗忘` `方法不会`。
记录错题时若带了 `--kp-id`，该知识点会自动降级并提前复习。

## 典型工作流

**用户丢进新资料**
1. 确认文件已在 `raw\`
2. `study.py ingest`（幂等，重复跑不会重复入库）
3. 报告：入库了几个文档、几个切块、有没有失败或需要 OCR 的
4. 若提示某 PDF 是扫描件，告诉用户需要装 OCR：`pip install rapidocr-onnxruntime`

**用户就资料提问**
1. `study.py search "<问题>" -k 5`
2. 命中不足就换说法、或切 `--mode keyword`、或先 `docs --outline` 看结构
3. 用 `show` 补上下文
4. 作答，**每条结论后附出处**
5. 若这段结论有长期价值 → `note save` 落盘

**用户要复习**
1. `study.py review` 取到期队列
2. 基于 `kp_sources` 关联的切块出题（用原文出题，别自己编）
3. 用户作答后给出反馈并指出对应原文
4. `study.py review <kp_id> --grade <0-5>` 记录，错题再 `mistake add`

**新会话开始（我什么都不记得时）**
1. `study.py stats` —— 看整体进度
2. `study.py note list` —— 看已有结论
3. `study.py note read "<相关的>"` —— 把关键结论读回上下文
4. 然后才开始检索资料

## 注意事项

- 检索不到的，直说"资料里没有"，**不要编造**。这是这套系统存在的意义。
- 引用格式统一：`文档名 › 章节路径 › 页码/段号`。`search` 输出里的 `cite` 字段已生成好，直接用。
- 检索结果里的 `why` 字段说明命中原因（语义相似度/关键词/标题命中）。
  如果召回明显不相关，把这个字段给用户看，便于判断是资料缺失还是检索问题。
- 向量模型是 bge-small-zh（512 维，int8 量化）。实测 8 条口语化查询 Top-1 命中 7 条。
  **已知弱点**：语义跨度大的提问（如问"怎么让很深的网络训起来"而资料里写的是"残差连接"）
  可能排不进前列。遇到这种情况换关键词再试，或先看大纲定位。
- `ingest` 失败或某文档 `status` 不是 `ok` 时，看 `docs` 里的状态标记并如实告知用户。