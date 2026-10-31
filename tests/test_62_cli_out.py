"""CLI 回归：tools/kp_build.py --out 必须真的传给 build（GPT 真实验收发现）。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import tools.kp_build as cli  # noqa: E402


class TestKpCliOut(unittest.TestCase):
    def test_out_is_forwarded_to_build(self):
        out = str(Path("_work") / "isolated" / "45.json")
        fake = {"ok": True, "status": "done", "out_path": out, "meta": {}}
        with mock.patch.object(sys, "argv", [
                "kp_build.py", "--doc", "45", "--out", out, "--force"]), \
             mock.patch.object(cli.kp_build, "build", return_value=fake) as b:
            self.assertEqual(cli.main(), 0)
        self.assertTrue(b.called)
        self.assertEqual(b.call_args.kwargs.get("out_path"), out)

    def test_dry_run_does_not_call_build(self):
        with mock.patch.object(sys, "argv", ["kp_build.py", "--doc", "45", "--dry-run"]), \
             mock.patch.object(cli.kp_build, "estimate", return_value={"ok": True, "out_path": "x"}), \
             mock.patch.object(cli, "_print_est"), \
             mock.patch.object(cli.kp_build, "build") as b:
            self.assertEqual(cli.main(), 0)
        self.assertFalse(b.called)


if __name__ == "__main__":
    unittest.main()
