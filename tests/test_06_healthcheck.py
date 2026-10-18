"""一键健康检查工具验收（Lead 新增 tools/healthcheck.py）。

healthcheck.py 会检查：解释器 / core 全模块导入 / 产品脚本语法 / 数据库 integrity /
`study.py check` / MCP selftest + stdio 握手 / 看板首页与 /api/stats。

测试方式：给它套上**隔离 env（KB_* 指向副本）**、并把「看板」指向本套件自己起的
隔离实例端口，这样它既跑完整链路，又不会读真实库/真实看板。
"""

import json
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import env  # noqa: E402

_SRV = None


def setUpModule():
    global _SRV
    env.prepare(clean_data=True)
    _SRV = env.Server(tag="healthcheck")
    _SRV.start()


def tearDownModule():
    if _SRV is not None:
        _SRV.stop()


class TestHealthcheck(unittest.TestCase):
    def test_healthcheck_all_green(self):
        """tools/healthcheck.py：9 项检查全 PASS、0 FAIL（看板指向隔离实例）。"""
        healthcheck = env.PROJECT / "tools" / "healthcheck.py"
        self.assertTrue(healthcheck.exists(), "tools/healthcheck.py 不存在（Lead 新增工具）")
        cmd = [str(env.PYTHON), "-X", "utf8", str(healthcheck),
               "--json", "--no-git", "--port", str(_SRV.port)]
        cp = subprocess.run(
            cmd, cwd=str(env.PROJECT), env=env.base_env(_SRV.db_path),
            capture_output=True, timeout=600,
        )
        out = cp.stdout.decode("utf-8", "replace")
        err = cp.stderr.decode("utf-8", "replace")
        self.assertEqual(cp.returncode, 0, f"healthcheck rc={cp.returncode}\n{out[-3000:]}\n{err[-1500:]}")
        start = out.find("{")
        self.assertGreaterEqual(start, 0, f"healthcheck 无 JSON 输出：{out[-2000:]}")
        data = json.loads(out[start:])
        # 留证据
        (env.ARTIFACT_DIR / "healthcheck-result.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        self.assertEqual(data["fail"], 0, f"healthcheck 有 FAIL：{data['results']}")
        self.assertGreaterEqual(data["pass"], 7, f"PASS 项过少：{data}")
        by_name = {r["name"]: r for r in data["results"]}
        required = ["解释器", "core 模块导入", "产品脚本语法", "数据库",
                    "study.py check", "MCP 协议自测", "MCP stdio 握手",
                    "看板首页", "看板 /api/stats"]
        for name in required:
            match = next((r for k, r in by_name.items() if name in k), None)
            self.assertIsNotNone(match, f"healthcheck 缺检查项 {name}: {list(by_name)}")
            self.assertEqual(match["status"], "PASS",
                             f"{name} 未通过：{match['detail']}")
        # 数据库检查必须落在隔离副本（fixture 签名：3 文档 / 1135 切块；真实默认库当前是 227 切块）
        db_item = next(r for k, r in by_name.items() if "数据库" in k)
        self.assertIn("documents=3", db_item["detail"])
        self.assertIn("chunks=1135", db_item["detail"],
                      f"healthcheck 没在读隔离 fixture：{db_item['detail']}")

    def test_backup_data_isolated(self):
        """tools/backup_data.py：库备份到临时目录、integrity=ok、失败 0、--keep 清理生效。"""
        tool = env.PROJECT / "tools" / "backup_data.py"
        self.assertTrue(tool.exists(), "tools/backup_data.py 不存在（Lead 新增工具）")
        out_root = env.DATA_DIR / "backup_smoke"
        base = [str(env.PYTHON), "-X", "utf8", str(tool), "--json", "--out", str(out_root)]

        def run_backup(keep: int | None = None):
            cmd = list(base)
            if keep is not None:
                cmd += ["--keep", str(keep)]
            cp = subprocess.run(cmd, cwd=str(env.PROJECT),
                                env=env.base_env(env.DATA_DIR / "main.db"),
                                capture_output=True, timeout=300)
            out = cp.stdout.decode("utf-8", "replace")
            self.assertEqual(cp.returncode, 0,
                             f"backup_data rc={cp.returncode}\n{out[-2000:]}\n"
                             f"{cp.stderr.decode('utf-8', 'replace')[-1000:]}")
            start = out.find("{")
            self.assertGreaterEqual(start, 0, f"backup_data 无 JSON 输出：{out[-1500:]}")
            # JSON 之后还会打印一行 "RESULT: PASS"，用 raw_decode 只取第一个 JSON 对象
            obj, _end = json.JSONDecoder().raw_decode(out[start:])
            return obj

        data = run_backup()
        (env.ARTIFACT_DIR / "backup-data-result.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        self.assertEqual(data["failed"], [], f"备份失败项：{data['failed']}")
        self.assertGreaterEqual(len(data["databases"]), 1)
        first = data["databases"][0]
        self.assertEqual(first["label"], "index__study.db")
        self.assertIn(str(env.DATA_DIR), first["src"], "备份主库不是隔离副本")
        for item in data["databases"]:
            self.assertEqual(item["integrity"], "ok", f"{item['label']} integrity 非 ok")
        dest = Path(data["dest"])
        self.assertTrue(dest.exists() and dest.is_dir())
        self.assertIn(str(out_root.resolve()), str(dest.resolve()))
        # 备份出的主库必须能打开且文档数与 fixture 一致
        conn = __import__("sqlite3").connect(str(dest / "index__study.db"))
        try:
            n = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(n, 3)

        # --keep 1：第二次备份后应清掉旧的，只剩 1 份
        import time as _time
        _time.sleep(1.1)  # 备份目录名精确到秒
        data2 = run_backup(keep=1)
        self.assertEqual(len(data2["removed"]), 1, f"--keep 1 未清理旧备份：{data2['removed']}")
        remaining = [p for p in out_root.glob("data-backup-*") if p.is_dir()]
        self.assertEqual(len(remaining), 1, f"备份目录应为 1 份，实际 {[p.name for p in remaining]}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
