# tools/run_figure_desc.py 重写报告（task-3）

> 审计快照：2026-10-18 17:1x，工作树含 lead 的新提交（`1e3f604` 修 kp_build、`ec394bb` 修 dashboard、`91782ac` 加开发工作流文档）。
> 唯一产品代码改动：`tools/run_figure_desc.py`（+446 / −38 行，2,228B → 20,277B）。

## 1. 改了什么

**从头重写为完整 CLI**，保留旧断头文件里全部 10 个 argparse 参数（`--src/--slides/--pages/--limit/--no-thinking/--out/--model/--skip-text-heavy/--text-heavy-chars/--only-with-images`，语义不变），并新增 `--dry-run`。

- 模块头：UTF-8 编码声明、模块 docstring（用途/用法/退出码/产物说明）、`from __future__`、`ROOT` 入 `sys.path`、`sys.stdout.reconfigure(utf-8)`，全部对齐 `tools/terms_build.py` 等现有 CLI 的写法。
- 主流程：`extract_pages` → `build_contexts` → 渲染/复用整页图 → 选页与过滤 → 逐页 `figures.describe_page` → 写 `slide_XX.md`（首行 `# pN · 标题`，与 `figures.load_descriptions()` 的解析格式逐字对齐，入库可复用）→ 写 `results.json` → 打印成功/失败/调用次数/费用/耗时。
- 行为对齐 `core/figures.py` 现有管线（不改 core）：
  - 输出目录默认 `config.LOG_DIR/figure_desc/<清理后文件名>`，与 `ingest_document_figures` 的复用目录同口径；
  - 空输出（<30 字）按 `config.FIGURE_RETRY_MAX_TOKENS` 重试一次（与 `_probe_page` 判据相同）；
  - `--skip-text-heavy` 用 `PageContext.body` 长度；`--only-with-images` 用 `figures.page_has_image()`（PDF 返回 None 时明确提示"无可靠判据、不过滤"）；
  - `no_info` 用 `figures.is_no_info()` 统计并在结果里标记（入库时会跳过）。
- 健壮性修复/增强（重写时实测踩到的）：
  1. **相对路径 → 绝对路径**：pptx 渲染走 PowerPoint COM，`Presentations.Open`/`Export` 不吃相对路径；第一次实测报 `0x80070003 The system cannot find the path specified`，转绝对路径后 45 页渲染 10.8s 成功。
  2. **补空语境**：实测该 pptx 有 46 张幻灯片，但文字层只解析出 45 页（第 38 页无文字）→ 给"有整页图、无文字语境"的页补 `PageContext(page, body="")`，不再静默漏页（`--pages 38` 也能跑）。
  3. **单页失败不拖垮整批**：逐页 `try/except` + `FigureResult.error` 记账，最后汇总失败页数与退出码。
  4. **退出码**：0=全部成功/dry-run 完成；1=有页失败；2=源文件/参数/Key/渲染致命错误。
  5. **dry-run 落盘**：写 `dryrun-plan.json`（选中页/跳过原因/语境数/渲染结果/model/thinking/API 调用=0）+ `contexts/pNN.txt`（整章语境 prompt），**不建 DB 连接、不调视觉 API**。

## 2. 验证命令与输出（全部真实执行）

### 2.1 语法

```text
$ .\venv\Scripts\python.exe -X utf8 -m py_compile tools\run_figure_desc.py
py_compile exit=0
```

### 2.2 `--help`（退出码 0，参数齐全）

```text
$ .\venv\Scripts\python.exe -X utf8 tools\run_figure_desc.py --help
usage: run_figure_desc.py [-h] --src SRC [--slides SLIDES] [--pages PAGES]
                          [--limit LIMIT] [--no-thinking] [--out OUT]
                          [--model MODEL] [--skip-text-heavy]
                          [--text-heavy-chars TEXT_HEAVY_CHARS]
                          [--only-with-images] [--dry-run]
整章语境识图驱动：渲染整页图 → 逐页生成图示说明（不写数据库）。
...
help exit=0
```

### 2.3 `--dry-run` 真实样本（任务指定命令）

```text
$ .\venv\Scripts\python.exe -X utf8 tools\run_figure_desc.py --src "raw\2镇静催眠药.pptx" \
    --dry-run --pages 1,2,3 --out "D:\study_agent\_work\audit\runfig_dry"
源文件 : D:\study_agent\raw\2镇静催眠药.pptx
格式   : pptx（PowerPoint COM 渲染）
输出   : D:\study_agent\_work\audit\runfig_dry
模式   : dry-run（不调用视觉 API）

课件   : 镇静催眠抗焦虑药
页数   : 45（建立了 45 个页面语境）
渲染整页图 → D:\study_agent\_work\audit\runfig_dry ...
  渲染完成 46 页，耗时 10.8s
⚠ 补空语境：1 页没有文字层（[38]），按空正文处理

选中页 : 3 / 3 页（跳过 0 页）

dry-run 计划已落盘：D:\study_agent\_work\audit\runfig_dry\dryrun-plan.json
  页面语境：D:\study_agent\_work\audit\runfig_dry\contexts（3 份）
  视觉 API 调用：0 次；费用：¥0.0000（未调用模型、未写数据库）
exit=0
```

落盘产物（已实测存在）：`runfig_dry/` 下 46 张 `slide_XX.png`（11s 渲染）、`dryrun-plan.json`、`contexts/p01~p03.txt`（整章语境 prompt，例如 p1：课件名 + 章节脉络 + 第 1 页）。`dryrun-plan.json` 关键字段：`"render_ok": true, "slides_found": 46, "visual_api_calls": 0, "cost_cny": 0.0`。
完整日志：`_work/audit/runfig_dry_validation.log`。

### 2.4 边界场景（均已执行）

```text
# 源文件不存在 → 友好错误 + 退出码 2
$ ... run_figure_desc.py --src "raw\不存在的课件.pptx"
错误：源文件不存在: raw\不存在的课件.pptx
      请检查路径（支持 pptx / pdf）。
exit=2

# --slides 复用 + --only-with-images 过滤（dry-run，不重渲染）
$ ... --slides "_work\audit\runfig_dry" --dry-run --pages 1,2,3 --only-with-images
复用整页图：_work\audit\runfig_dry（46 张）
选中页 : 1 / 3 页（跳过 2 页）
  - p2 跳过：无图片对象
  - p3 跳过：无图片对象
exit=0

# 空白页 38（文字层缺页）→ 补空语境后可选中
$ ... --slides "_work\audit\runfig_dry" --dry-run --pages 38
⚠ 补空语境：1 页没有文字层（[38]），按空正文处理
选中页 : 1 / 1 页
exit=0

# --pages + --limit 组合
$ ... --pages 4,5,6,7 --limit 2 ...   → "选中页 : 2 / 2 页"（即 4,5）
# --skip-text-heavy 阈值过滤
$ ... --pages 2,4,6 --skip-text-heavy --text-heavy-chars 150 ...
选中页 : 2 / 3 页（跳过 1 页）
  - p4 跳过：正文 163 字 > 阈值 150
```

## 3. 未验证项（如实标注）

1. **真实视觉调用未跑**（按任务约束：需要网络与费用）：`describe_page` 调用、`slide_XX.md` 实际写入内容、`results.json`、费用/调用次数统计只做了**静态路径核对**（代码直接复用 `figures.describe_page`/`is_no_info`/`_safe_dirname`，格式按 `load_descriptions()` 规范对齐）。真要验：在有 key 的机器上跑 `--pages 2 --limit 1`（约 ¥0.01 量级，看板设置里可查）。
2. **PDF 分支未实测**（raw/ 下当前 3 份都是 pptx；PDF 走 pymupdf 内置渲染，不依赖 Office，属 `figures.render_pages` 既有已验证路径）。
3. pptx 渲染依赖本机 PowerPoint（COM 已注册、实测可用）；没装 Office 的机器会得到 `render_backend_problem()` 的中文可操作提示（已实测渲染失败文案路径）。
4. 未写数据库（无 `db.connect`/`ensure_schema` 调用；dry-run 与实跑均不落库），但 `figures.render_pages` 会调用 `core.progress.tick`（进程内状态，无副作用）。

## 4. release_pack 白名单建议

**无需调整**：`TOOLS_RELEASE["run_figure_desc.py"]` 的判定（"R3 整章语境识图驱动：pptx 走 COM、pdf 走 pymupdf"）与实际实现一致，文件现已可编译、可 `--help`、可离线 dry-run，进包不会带坏工具。
可选（非必须）：
1. 若《使用说明.md》有该工具的用法段，可补一行 `--dry-run` 的离线自检用法；
2. `release_pack.py --self-test` 若对 tools 逐个 py_compile，本文件会通过（已验证）。
