"""比较"推理开/关"两种识图质量与成本。

方法：对两版输出检查同一批**结构类关键点**是否被覆盖，
并对比长度与成本。结构类关键点是从原图人工确认的目标信息。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core import config  # noqa: E402

ON = config.ROOT / "logs" / "figure_desc"
OFF = config.ROOT / "logs" / "figure_desc_nothink"

# 每页的结构类关键点（人工从原图确认，是"复习时真正需要"的东西）
KEYS = {
    4: ["大脑", "中脑", "脑桥", "延髓", "小脑", "脊髓"],
    9: ["间隙连接", "囊泡", "外排", "电突触", "化学突触"],
    11: ["EPSP", "IPSP", "触发区", "总和", "Hillock", "模拟", "数字"],
    12: ["杏仁核", "隔区", "扣带回", "丘脑前部", "乳头体", "下丘脑", "海马", "环路"],
    21: ["中缝核", "前额皮质", "纹状体", "海马", "吲哚", "羟基", "乙胺", "VTA", "黑质"],
}

# 实测成本（来自两次运行输出）
COST_ON, COST_OFF = 0.0198, 0.0048
TIME_ON, TIME_OFF = 25.2, 5.0


def norm(s: str) -> str:
    return re.sub(r"[\s\u3000]+", "", s or "")


def main() -> None:
    print("=" * 84)
    print("推理开 vs 推理关：结构类关键点覆盖 + 成本")
    print("=" * 84)
    print(f"  {'页':>3} {'关键点':>6} {'推理开':>8} {'推理关':>8} "
          f"{'开字数':>7} {'关字数':>7}")
    print("  " + "-" * 76)

    ton = toff = tn = 0
    for pno, keys in sorted(KEYS.items()):
        fon = ON / f"slide_{pno:02d}.md"
        foff = OFF / f"slide_{pno:02d}.md"
        if not fon.exists() or not foff.exists():
            print(f"  {pno:>3}  缺文件")
            continue
        a = norm(fon.read_text(encoding="utf-8", errors="replace"))
        b = norm(foff.read_text(encoding="utf-8", errors="replace"))
        ca = sum(1 for k in keys if k.lower() in a.lower())
        cb = sum(1 for k in keys if k.lower() in b.lower())
        ton += ca
        toff += cb
        tn += len(keys)
        print(f"  {pno:>3} {len(keys):>6} {ca:>8} {cb:>8} "
              f"{len(a):>7} {len(b):>7}")

    print()
    print("=" * 84)
    print("汇总")
    print("=" * 84)
    print(f"  {'':<22}{'推理开':>16}{'推理关':>16}")
    print(f"  {'结构关键点覆盖':<22}{ton:>10}/{tn}{toff:>12}/{tn}")
    print(f"  {'覆盖率':<22}{ton/tn*100:>15.0f}%{toff/tn*100:>15.0f}%")
    print(f"  {'单页成本':<22}{'¥%.4f' % COST_ON:>16}{'¥%.4f' % COST_OFF:>16}")
    print(f"  {'单页耗时':<22}{'%.1fs' % TIME_ON:>16}{'%.1fs' % TIME_OFF:>16}")
    print(f"  {'26 页合计':<22}{'¥%.2f' % (COST_ON*26):>16}"
          f"{'¥%.2f' % (COST_OFF*26):>16}")
    print(f"  {'400 页合计':<22}{'¥%.2f' % (COST_ON*400):>16}"
          f"{'¥%.2f' % (COST_OFF*400):>16}")
    print()
    gain = (ton - toff) / max(1, tn) * 100
    ratio = COST_ON / COST_OFF
    print(f"  推理开带来 {gain:+.0f} 个百分点的覆盖率提升，成本是 {ratio:.1f} 倍。")
    print(f"  即：每多花 ¥{COST_ON-COST_OFF:.4f}/页，多换 {gain:.0f} 个百分点的关键点覆盖。")


if __name__ == "__main__":
    main()