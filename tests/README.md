# tests/ · P2 隔离验收套件

给 `D:\study_agent`（学习看板）用的一键回归网。**不依赖第三方库**（标准库 unittest /
urllib / sqlite3 / subprocess），也不会读写真实数据。

## 一键运行

```powershell
cd D:\study_agent
venv\Scripts\python.exe -X utf8 tests\run_all.py
```

- 退出码 `0`：全部通过（允许 `expectedFailure`，即已登记的产品缺陷）
- 退出码 `1`：有失败/错误，或已登记缺陷被修好（`UnexpectedSuccess`，提示更新登记表）
- 报告：`_work/verify/report.md`（人读）+ `_work/verify/report.json`（机读）

常用参数：

```powershell
# 只跑某条/某组用例（报告只含被跑到的行，matrix 其余行标记「未运行」）
venv\Scripts\python.exe -X utf8 tests\run_all.py --only test_search

# 查看功能矩阵
venv\Scripts\python.exe -X utf8 tests\run_all.py --list-matrix

# 按当前真实库重拍 fixture（默认 fixture 冻结在第一次运行时的快照）
$env:VERIFY_REFRESH_FIXTURES = "1"
venv\Scripts\python.exe -X utf8 tests\run_all.py
```

单模块调试也可以直接 `python -m unittest discover -s tests -t tests -v`，
但正式基线请用 `run_all.py`（它负责隔离核对、清理和报告）。

## 隔离是怎么做的

1. **代码沙箱**：`core/*.py` + `tools/dashboard.py` + `tools/study.py` 复制到
   `_work/verify/sandbox/`。`core/config.py` 用 `Path(__file__).parent.parent` 算 ROOT，
   所以被测进程的 `ROOT`/学科注册表/`share/`/`config.local.json` 全部落在沙箱里。
2. **数据 fixture**：真实库用 sqlite3 backup API 快照到 `_work/verify/fixtures/*.db`
   （main ← `subjects/生药药动`，deck ← `subjects/药物化学`），每次运行再复制到
   `_work/verify/data/`；通过 `KB_DB/KB_RAW/KB_NOTES/KB_INDEX/KB_LOG/KB_SETTINGS_FILE`
   环境变量把被测进程指过去。
3. **端口**：优先 8897/8898/8899，占用则自动找空闲端口；每模块起停，跑完释放。
4. **真实数据零写入**：`env.real_data_snapshot()` 记录真实 DB 的 sha256 与 raw/notes
   文件清单，`run_all.py` 运行前后对比；`test_01_health` 里也有断言。
   （若真实看板 8756 在跑并被人操作，报告会把差异归因为外部改动，而不会误判成本套件写入。）

## 文件

| 文件 | 作用 |
|---|---|
| `env.py` | 隔离环境管理：代码同步 / fixture / 数据副本 / 起停服务 / HTTP / CLI / Edge |
| `matrix.py` | **功能矩阵**（功能 → 接口 → 用例 → 优先级）+ 未覆盖清单 + 已知缺陷登记 |
| `test_01_health.py` | 启动健康、页面骨架、404、隔离路径、真实数据只读 |
| `test_02_api_read.py` | 只读 API（文档/检索/知识点/复习/图谱/预导/分享/学科/设置/进度） |
| `test_03_api_write.py` | 写 API（全在临时副本上）：知识点/复习/笔记/设置/学科/预览类接口 |
| `test_04_cli.py` | `study.py check/stats/docs/search/show/review/kp/mistake/note/ingest` |
| `test_05_browser.py` | Edge headless 渲染首页 + 截图证据 |
| `test_06_healthcheck.py` | `tools/healthcheck.py` 一键自检（隔离 env + 隔离看板端口）、`tools/backup_data.py` 数据备份 |
| `test_07_legacy_ingest.py` | 旧版 Office（.ppt/.doc/.xls）入库链路判定层：raw 列表 / 导入预览 / 批处理计划 / 产物已入库去重（不触发真实转换） |
| `run_all.py` | 一键运行、汇总、隔离核对、生成报告、退出码 |

## 加用例的规矩

1. 在对应 `test_0N_*.py` 里加 `def test_xxx(self)`，断言要能定位到**用户路径**。
2. 在 `matrix.py::MATRIX` 里加一行，`tests` 填测试方法名片段（报告靠它做功能矩阵映射）。
3. 写操作只能在 `env.DATA_DIR` / `env.SANDBOX_DIR` 下的副本上做；不要在 `setUpModule`
   之外碰真实路径。
4. 如果发现的是**产品缺陷**：先报告（不要改 product 代码），在用例上加
   `@unittest.expectedFailure` 并写进 `matrix.py::KNOWN_BUGS`；缺陷修好后用例会变成
   UnexpectedSuccess（退出码 1），届时去掉装饰器并把缺陷移到「已修复」清单。
