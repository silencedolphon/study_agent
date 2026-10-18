# -*- coding: utf-8 -*-
"""C-0100 施工补丁 2（微调，只在**本卡新增的** `pvFinish` 里加一行）。

为什么单独一个脚本：`pvFinish` 跑完会 `await pvLoad()` 重取章列表，重建 DOM 时把
预估区清空了——那一栏会显示成"这一章暂时算不出预估"。加一行把预估刷回来即可。
**只动卡 C-0100 新增的函数**，别的一行不碰。

行尾符：读进来是 CRLF，写回去仍是 CRLF（末尾自检 bare LF == 0）。
用法：
    python tools\\c0100_apply_patch2.py            # dry-run
    python tools\\c0100_apply_patch2.py --apply    # 落盘
"""
import argparse
import io
import sys

sys.stdout.reconfigure(encoding="utf-8")

TARGET = r"G:\study_agent\tools\dashboard.py"

OLD = "    await pvLoad(PVP.docId);        // 重取章列表：产物标记与「最近一章」跟着更新"
NEW = ("    await pvLoad(PVP.docId);        // 重取章列表：产物标记与「最近一章」跟着更新\r\n"
       "    if (PVP.chNo) pvPickCh(PVP.chNo);   // 顺手把预估区刷回来（别让它空成\"算不出预估\"）")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    with io.open(TARGET, "r", encoding="utf-8", newline="") as f:
        raw = f.read()
    if raw.count("\n") - raw.count("\r\n") != 0:
        raise SystemExit("停手：目标文件里本来就有裸 LF")
    n = raw.count(OLD)
    if n != 1:
        raise SystemExit("定位失败：锚行命中 %d 次（应为 1）" % n)
    if "if (PVP.chNo) pvPickCh(PVP.chNo);" in raw:
        raise SystemExit("停手：这一行已经在文件里了（补丁打过）")
    print("命中锚行 1 次：%s" % OLD.strip()[:60])
    if not a.apply:
        print("（dry-run，未落盘。加 --apply 才写。）")
        return 0
    out = raw.replace(OLD, NEW)
    if out.count("\n") - out.count("\r\n") != 0:
        raise SystemExit("自检失败：写回文本里出现了裸 LF，拒绝落盘")
    with io.open(TARGET, "w", encoding="utf-8", newline="") as f:
        f.write(out)
    print("已落盘：%s（行数 %d → %d，CRLF 保持，裸 LF = %d）"
          % (TARGET, raw.count("\r\n"), out.count("\r\n"), out.count("\n") - out.count("\r\n")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
