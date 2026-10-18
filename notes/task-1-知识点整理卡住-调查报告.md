# 调查报告：看板「AI 知识点整理」卡住 —— 是正常慢还是真卡死？

- **任务**：`task-1`（共享看板）
- **执行**：teammate `kp-hang`
- **日期**：2026-10-16（本机时钟）
- **结论一句话**：**是「正常慢」**（模型网关单次调用实测最慢 **944 秒**），
  **槽位死锁不存在**；但「慢」期间界面**完全不给信号**（阶段名不动、条数不动、
  没有走秒的心跳），所以看起来就是卡死。已修掉 3 处**真实缺陷**（见 §5）。

---

## 0. 环境事实（与任务书不同之处，先记下来）

| 项目 | 任务书说 | 实际 |
|---|---|---|
| shell 可用性 | 「很可能完全不可用，报 spawn ENOENT」 | **完全可用**。原因是本会话 cwd 被记成不存在的 `G:\study_agent`；**只要给 `pwsh` 显式传 `workdir: D:\study_agent` 就一切正常**（含 python / node / git） |
| `glob` 工具 | — | 仍然失败（`ripgrep provider failure`，它不接 workdir）；grep 工具同样。**改用 `Select-String` 完成全部检索** |
| 知识点产物位置 | `D:\study_agent\logs\knowledge\<doc_id>-末级知识点总结.json` | 实际在**学科目录**下：`D:\study_agent\subjects\<slug>\logs\knowledge\...`。`logs\knowledge\` 根目录只有提示词 md，**没有产物** |

> 这条很重要：如果按任务书给的路径去找产物，会得出「产物根本没写出来」的**错误结论**。

---

## 1. 用户那次到底跑出东西没有？—— **跑出来了，钱没白花**

真实产物（只读，未改动）：

| 文件 | 大小 | mtime | status | 条数 | 用时 |
|---|---|---|---|---|---|
| `subjects\生药药动\logs\knowledge\2-末级知识点总结.json` | 136,155 | 2026-10-15 08:58:50 | `done` | 20/20 | 1197.8 s |
| `subjects\生药药动\logs\knowledge\3-末级知识点总结.json` | 361,778 | 2026-10-15 23:56:51 | `done` | 55/55 | 440.2 s（第 2 轮） |
| `subjects\药物化学\logs\knowledge\1-…json` | 447,737 | 2026-10-15 01:43:19 | — | — | — |
| `subjects\药物化学\logs\knowledge\2-…json` | 269,300 | 2026-10-15 01:38:30 | — | — | — |
| `subjects\药物化学\logs\knowledge\3-…json` | 392,960 | 2026-10-15 01:41:37 | — | — | — |

**⇒ C-0120 担心的「模型调用跑完、产物写不出来」在这个场景下没有发生。**
两份产物都是 `status=done`、条数满额（20/20、55/55），`meta.calls` 逐次可查。
产物里 `meta.runs` 记着：

```
run 1: calls=50  calls_failed=18  cost_cny=0.939630  08:38:53 → 09:43:41
run 2: calls=30  calls_failed=1   cost_cny=1.063993  23:49:31 → 23:56:17
累计 cost_cny_all_calls = 2.003623（doc#3）
```

## 2. 正常慢 vs 真卡死：判据与实测

### 2.1 真值里的耗时分布（doc#3 的 80 条调用记录）

失败（`JSONDecodeError: Expecting value: line 1 column 1 (char 0)`）的调用耗时：

```
924.2  916.6  918.2  909.6  907.2  905.0  900.3   900.2  900.2  900.2
900.2  900.3  900.3   (秒)
```

最慢的**成功**调用 **944.5 s**（`KP-N14-1（p.10）`，`ok=true`，`tokens_out=9580`）。
条目形态：`[attempt 1/2/3] … 900.x s → JSONDecodeError`，随后 `attempt 2` 又跑 119.2 s 才成功。

> 读数：这不是「死锁」，是**服务端把连接吊住很久、最后回了个空/非 JSON 的包**。
> 失败轮 `tokens_in/out` 记 0 —— 说明是模型侧没正常完成，不是管线解析自己的问题。

### 2.2 代码里的三道兜底（C-0123 已落地）

`core/kp_build.py` 的「挂起兜底」小节（源码注释 L165-202）已经把现场记下来了：
2026-10-15 08:42 用户看板那次，4 个 TCP 连接 16 分钟无字节，两份任务停在
`kp_done=14/5`。三道兜底逐条对应：

| 兜底 | 位置 | 默认值 |
|---|---|---|
| ① 抢槽带上限 | `model_slot(..., timeout=slot_wait_timeout())` | 1800 s |
| ② 真调用进陪跑线程 + 硬墙 | `_call_with_hard_timeout(..., timeout=call_hard_timeout())` | 1800 s |
| ③ 在飞全不回来的判停线 | `_cf.wait(..., timeout=stall_timeout())` | 2400 s（= 硬墙 + 600） |

**注释里那句「默认值不能拍脑袋定小」是对的**：实测最慢 944 s，硬墙 1800 s ≈ 1.9 倍余量；
定小了会把「慢但正常、已经付过钱」的输出白扔掉。

---

## 3. 第一嫌疑（槽位死锁）—— **查无此病，13/13 通过**

`core/pipeline_pool.py::_model_slot` 被 C-0108 / C-0111 / C-0120 反复改过，是头号嫌疑。
实测（`_c0124_slot_probe.py`，零花费）：

```
kp_build.model_slot 返回上下文管理器（非裸生成器）        PASS  type=_GeneratorContextManager
pipeline_pool._model_slot 返回上下文管理器                PASS  type=_GeneratorContextManager
pipeline_pool._model_slot 带 @contextlib.contextmanager   PASS
持槽期间 inflight == 1                                    PASS  inflight=1
正常退出后 inflight 归还为 0                              PASS  inflight=0
异常退出后 inflight 归还为 0                              PASS  inflight=0
kp_build.model_slot 异常退出后 inflight == 0              PASS  inflight=0
并发 200 次取槽后 inflight 归零                            PASS  inflight=0
acquired 计数 == 200                                      PASS  acquired=200
peak_inflight 未超闸门值 4                                PASS  peak=4 limit=4
闸门值=1 串行 50 次后 inflight == 0                        PASS
抢槽超时应抛 ModelSlotTimeout                             PASS  ModelSlotTimeout
超时失败后 inflight 归零                                   PASS  inflight=0
==== 汇总：13/13 通过 ====
```

源码核对（`core/kp_build.py:2688-2730`）：
`@contextlib.contextmanager` **确实挂在 `model_slot` 头上**（C-0111 的修复在），
`pipeline_pool.py:616-621` 的协议分派（有 `__enter__` 走 `with`、否则走 `for`）在，
`acquire`/`release` 成对。

**⇒ 「槽位获取/释放不成对」这条排除。** 但见 §5.1：**判停路径**上有一处真的漏还。

---

## 4. 复现与时间序列证据（零花费打桩）

三段探针都**不调真实模型**（`chat.chat_once` 打桩），产物只写 `_c0124_shadow\`，
跑前跑后对真库/真产物做 SHA256 对比。

### 4.1 进度确实在推进（`_c0124_progress_probe.py`）

```
  t=+  0.01s  stage  stage=0  kp_done=0/0  ... 读取库里的文本（15 页）
  t=+  0.01s  stage  stage=1  kp_done=0/0  ... 生成结构
  t=+  0.01s  stage  stage=1  kp_done=0/15 ... 写知识点（15 条待生成）
  t=+  2.02s  tick   stage=2  kp_done=1/15 inflight=1/2 abandoned=1
  t=+  2.02s  tick   stage=2  kp_done=2/15 inflight=0/2 abandoned=0
  t=+  4.03s  tick   stage=2  kp_done=3/15 inflight=1/2 abandoned=1
  t=+  4.04s  tick   stage=2  kp_done=4/15 inflight=0/2 abandoned=0
  t=+  4.06s  stage  stage=3  kp_done=4/15 ... 写入产物
  t=+  4.07s  end    stage=3  kp_done=4/15 running=False
  [时间序列] (t秒, kp_done) = [(2.02, 1), (2.02, 2), (4.03, 3), (4.04, 4)]
  [产物] 已写出   meta.status=stopped  kp_actual=4/15  knowledge_points 条数=4
  [隔离断言] 用户真库/真产物/真学科表**逐字节未变**：是
```

### 4.2 挂起能自愈（`_c0124_hang_probe.py`，桩「永不返回」）

| 场景 | 参数 | 用时 | 产物 | `inflight` | 结果 |
|---|---|---|---|---|---|
| A 硬墙 | `KP_CALL_HARD_TIMEOUT=3` | **3.09 s** | 已写出 | **0/2** | `status=failed`，记账 `ModelCallHang` |
| B 判停线 | `KP_STALL_TIMEOUT=4`，硬墙 90 s | **4.08 s** | 已写出 | **0/2** | `status=failed`，「已回收 1 个滞留模型槽位」 |
| C 对照 | 正常桩 0.2 s/条 | 0.30 s | 已写出 | 0/2 | `status=done`（兜底不误伤） |

**a `with ThreadPoolExecutor` 不会再钉死**：主线程能带着已完成的条数收尾落盘。

---

## 5. 找到并修掉的 3 处真实缺陷

改动前已备份 + SHA256（`_c0124_backup_20261016-002007\`）。

### 5.1 【真缺陷】判停时槽位永久滞留（`core/kp_build.py`）

**现象（场景 B 修前实测）**：`build_v2` 已经返回、产物已落盘，
`model_gate_stats()["inflight"] == 1/2` —— **一格槽没还**。

**根因**：默认值下 `stall(2400) > hard(1800)`，所以判停线**必然先触发**：

```
t=0      主线程 _cf.wait(timeout=2400) 等着
t=2400   判停线到 → 主线程收尾落盘、不再等 worker
t=1800   worker 自己的硬墙才到 —— 但已经没人等它了
```

判停那一刻，在飞 worker 还**阻塞在 `th.join(1800)` 里**（`_call_with_hard_timeout`），
根本走不到 `with model_slot` 的退出路径 ⇒ 槽要等 1800 秒才被一个「早已无人等待」的
worker 释放。用户紧接着再点一次整理，起手就少一格并发。

**修法**：判停时由**主线程按账回收**这些「已经没人等」的槽位。

- 新增模块级账本 `_ABANDONED` + 线程本地 `_ABANDONED_LOCAL`；
- `_v2_call` 在**抢到槽之后**记账（`_abandoned_enter()`），返回时销账（`_abandoned_done()`）
  ——记账点在 `acquire()` 成功之后，`cancel_futures` 里没跑起来的份不会虚增；
- `reclaim_stalled_slots()` **逐格** `release()` 并同步 `inflight`（不重建闸门：
  `_gate()` 是进程级共享的，`core/pipeline_pool.py` 的入库任务也在用，重建会把在飞额度翻倍）；
- 被回收的 worker 之后走完自己的退出路径时**跳过归还**，否则 `BoundedSemaphore`
  会 `ValueError: released too many times`，把收尾路径也炸掉。

**验证**：场景 B `inflight` 由 **1/2 → 0/2**，`error` 文案加「已回收 1 个滞留模型槽位」。

### 5.2 【真缺陷】并发写知识点时阶段名不动（`core/kp_build.py`）

C-0123 给并发路径补了 `_emit(on_event, …)`，但那只喂**批量**回调；
**单份**进度条读的是 `_STATE`（`progress_snapshot()`），而 `_emit` 不写它。
实测后果：`stage` 一直显示「生成结构」、`stage_index` 停在 1，
用户看到的是「阶段没变 + 条数在涨」这种自相矛盾的状态。

**修法**：并发路径补一次 `_stage(2, note="写知识点（N 条待生成）", on=progress_on)`。
`on=progress_on` 保证批量模式照旧不碰模块级 `_STATE`。

**验证（修后采样）**：

```
t=+0.31s … t=+1.85s   stage 文案 = 写知识点   (kp_done=0)
t=+2.16s … t=+3.98s   stage 文案 = 写知识点   (kp_done=2)
写知识点期间的 stage 文案（按采样去重）= ['写知识点']
```

### 5.3 【真缺陷】慢的时候界面完全没有信号（`core/kp_build.py` + `tools/dashboard.py`）

单次调用实测最慢 944 s（15 分钟），期间**阶段名与条数都不变**——界面看着就是卡死。

**修法**：加**心跳**。

- 后端：`observe_call_seconds()` / `observed_call_seconds()` / `seconds_since_last_call()`，
  只留最近 40 次、取**中位数**（抗离群）；`progress_snapshot()` 新增
  `since_last_call_s` / `eta_call_s` / `eta_note` 三个字段；
- 前端：`renderProgress()` 里渲染 `⏳ 当前一条等待 12 分 34 秒（同类中位 46 秒）`，
  并在两次 1.5 秒轮询之间**本地走秒**（否则每 15 分钟才动一次，等于没动）；
- `_fmt_secs()`：秒 → `15 分 14 秒` / `1 小时 2 分` 可读形式。

**验证（修后采样，每 0.3 秒一次）**：`since_last_call_s` 在长调用期间持续推进
`0.1 → 0.4 → 0.7 → 1.1 → 1.4 → 1.7 → 2.0`，同时 `eta_call_s = 2.0`（同类中位）。

---

## 6. 改动清单

| 文件 | 新增/删除行 | 原 SHA256 | 新 SHA256 |
|---|---|---|---|
| `core\kp_build.py` | +206 / -16 | `D615B9CB…5EDC` | `7F1BEDF6…426B` |
| `tools\dashboard.py` | +26 / -2 | `815CF9D1…ECDC` | `5847849E…00A0` |
| `core\kp_batch.py` | **未改动** | `87B6F8ED…74C0` | 同 |
| `core\pipeline_pool.py` | **未改动** | `9719F098…7B26` | 同 |
| `core\progress.py` | **未改动** | `C231897D…E790` | 同 |

未触碰其他工作正在改的 `core\figures.py` / `outline.py` / `tables.py` / `extract.py` /
`layout.py` / `multippt.py`。

**备份**：`D:\study_agent\_c0124_backup_20261016-002007\`

---

## 7. 验证命令与实际输出（可复跑，全部零花费）

```powershell
$env:PYTHONUTF8='1'; $env:PYTHONIOENCODING='utf-8'

# ① 槽位成对性 13 项
D:\study_agent\venv\Scripts\python.exe D:\study_agent\_c0124_slot_probe.py
#   → 汇总：13/13 通过

# ② 进度时间序列 + 心跳（桩 2.0s/条）
$env:C0124_KP_SLEEP='2.0'; $env:C0124_LIMIT='3'
D:\study_agent\venv\Scripts\python.exe D:\study_agent\_c0124_progress_probe.py
#   → kp_done 1→2→3→4；since_last_call_s 0.1→2.0；stage 文案=写知识点；真库 SHA 未变

# ③ 挂起自愈三场景
D:\study_agent\venv\Scripts\python.exe D:\study_agent\_c0124_hang_probe.py
#   → A 3.09s / B 4.08s（inflight 0/2）/ C status=done；真库 SHA 未变

# ④ 前端 JS 语法（dashboard.py 内联 JS 抽出来给 node --check）
D:\study_agent\venv\Scripts\python.exe D:\study_agent\tools\check_dashboard_js.py
#   → node --check: 语法通过

# ⑤ 看板接口（另起 8799 验证后退掉，未动用户的 8756）
#   GET /api/kp-build/progress → HTTP 200，含 since_last_call_s / eta_call_s / eta_note
#   GET /api/kp-build/batch/progress → HTTP 200
#   GET /api/kp-build/options → HTTP 200
#   GET / → HTTP 200，HTML 内含 KP_HEART / 当前一条等待
```

**隔离断言**：跑前跑后 `subjects\生药药动\index\study.db`、
`index\study.db`、`logs\subjects.json`、两份真实产物 **SHA256 完全一致**；
`subjects\*\logs\knowledge\99*-…json` 无污染（影子产物只在 `_c0124_shadow\`）。
全程**真实模型调用 0 次，花费 CNY 0.0000**。

---

## 8. 未做项 / 不确定项（请你判断）

1. **没有真调一次模型**（按硬要求打桩）。所以「944 s 是不是当前网关的真实上限」
   只是**历史真值**（2026-10-15 的产物），不是今天的实测。
2. **三个兜底默认值我没动**（1800/1800/2400 s）。理由：注释里「默认值不能拍脑袋定小」
   是对的，实测最慢 944 s，把硬墙压到 944 以下会**白扔已付费的输出**；
   而我没有今天的实测数据支撑一个新值。**这是有意的保守选择，不是遗漏。**
   如果你确认网关现在不会再有 15 分钟级调用，可以把 `KP_CALL_HARD_TIMEOUT`
   调到 1200（≈1.27 倍余量），`KP_STALL_TIMEOUT` 会自动变成 1800。
3. **`core/chat.py` 的 `TIMEOUT=300` 没动**（不在本卡可改范围）。它是
   **socket 单次操作**超时，对「连接活着、服务端不回包」不生效 —— 这正是 900 s 级
   挂起的根子。真正的根治点在 `chat.py`，不在 `kp_build.py`。
   **另注**：产物里 13 条失败调用的耗时密集落在 **900.2~924.2 s**，而 `TIMEOUT=300`。
   这个 3 倍关系（300×3）**对不上**任何一处我读到的常量，我没有找到确证，
   **不猜**：可能是网关侧自己的 900 s 断连，也可能是 urllib 的
   `TimeoutError: The read operation timed out`（doc#3 有一条 334.7 s 的正是这个错）
   在不同路径上的表现。**这条列为不确定项。**
4. **用户那个 8756 端口的看板进程（pid 16268，12:48 启动）还跑着旧代码**，
   必须重启才会带上本次修复。
5. **外部进程正在动真实数据**（不是我）：
   - `logs\subjects.json` 在 **00:12:15** 被外部改成 `current=药物化学`（SHA 变了）；
   - `subjects\药物化学\index\study.db` 在 **00:26:10** 被外部重写（7.8 MB），
     我当时正在验证，我的脚本对它只读且从不引用它，`dash.log` 也证明我只发了
     只读 GET。**推测是用户或另一流程在跑入库/切库。**
   - ⇒ 如果你的验收在我之后跑，注意 `current` 学科已经不是 `生药药动` 了。
6. **`eta_call_s` 是量级参考不是承诺**：它只统计**本进程内**已观测的调用，
   冷启动（第一次调用前）为 0，界面此时不显示中位数。
7. **`kp_batch.py::plan()` 的 `out_path` 闸**只做了纯函数级核对
   （`out_file()` 对 `None`/`''`/`'   '`/`'.'`/`'..'` 全部回退到默认产物；
   `_save(Path('.'))` 仍按预期抛 `ValueError: WindowsPath('.') has an empty name`），
   **没有**用影子库跑一遍完整批量 `plan()`。C-0120 的修复在源码里，字段齐全。

---

## 9. 用户下一步该做什么

1. **重启看板**（8756 那个进程还是旧代码）：
   关掉当前 `看板.cmd` 窗口，重新双击 `D:\study_agent\看板.cmd`。
2. 重开后再点「AI 知识点整理」，这次你会看到：
   - 阶段条会正确走到 **「写知识点 N/M 条」**（以前一直停在「生成结构」）；
   - 右边出现 **`⏳ 当前一条等待 X 分 Y 秒（同类中位 Z 秒）`**，**秒数在走** ⇒ 进程是活的；
   - 「已出 n/N 条 · CNY…」持续增长。
3. **慢是正常的**：doc#3 实测 55 条、单次调用中位几十秒、最慢 944 s，
   一轮十几分钟到几十分钟都算正常。**只要秒数在走就别重启**——
   重启会丢掉这一轮，重跑还得重新花钱（`续跑`只省已完成的那几条）。
4. 如果秒数**长时间不动**（> `KP_STALL_TIMEOUT`，默认 40 分钟）才会判停滞，
   届时产物照旧落盘、已完成条数一条不丢，`error` 里会写明原因，
   直接再点一次整理就是**续跑**（不重复花钱）。
5. 想更激进地缩短「卡住」的容忍时间（可选，要改环境变量）：
   ```
   set KP_CALL_HARD_TIMEOUT=1200
   set KP_STALL_TIMEOUT=1800
   ```
   注意：定得比实测最慢值（944 s）低，会把「慢但正常」的调用误判成挂起，
   **白扔已经付过钱的输出**。

---

## 附：本次新增的探针/证据文件

| 文件 | 用途 |
|---|---|
| `_c0124_slot_probe.py` | 槽位获取/释放成对性 13 项断言 |
| `_c0124_progress_probe.py` | 打桩跑 build_v2：阶段与条数时间序列 + 心跳采样 + 真库 SHA 对比 |
| `_c0124_hang_probe.py` | 挂起自愈三场景（硬墙 / 判停线 / 正常对照） |
| `_c0124_shadow\*.log` | 上述脚本的完整输出留档 |
| `_c0124_backup_20261016-002007\` | 改动前的 5 个文件 + SHA256 |
