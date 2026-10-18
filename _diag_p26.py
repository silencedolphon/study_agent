"""诊断夹具：复现 smoke_test [25] 的 p.26/p.27 用例，打印真实产物。"""
import sys
sys.path.insert(0, r"G:\study_agent")

from core import chunk as C4
from core import extract as E4
from core import outline as O4

deck = "镇静催眠抗焦虑药"
ol = O4.DeckOutline(doc_id=1, deck=deck, pages={
    2: [deck, "概念与镇静催眠作用"],
    25: [deck, "巴比妥类", "分类"],
    26: [deck, "巴比妥类", "药理作用与临床应用"],
    27: [deck, "巴比妥类", "药理作用与临床应用", "镇静催眠作用"],
    28: [deck, "巴比妥类", "药理作用与临床应用", "抗惊厥抗癫痫与麻醉"],
    30: ["巴比妥类", "体内过程"],
})

blocks = [
    E4.Block(text=deck, page=1, para=0, kind="heading", level=1),
    E4.Block(text="药理作用与临床应用", page=26, para=0, kind="heading", level=2),
    E4.Block(text="普遍性中枢抑制作用，随剂量增加而增强", page=26, para=1),
    E4.Block(text="镇静催眠作用", page=27, para=0, kind="heading", level=2),
    E4.Block(text="小剂量镇静，中剂量催眠，大剂量抗惊厥", page=27, para=1),
]

ck = C4.chunk_blocks(blocks, doc_meta={"slides": 27}, outline=ol)
print("=== 产物 %d 块 ===" % len(ck))
for c in ck:
    print("ref=%-10s len=%-4d head=%s" % (c.source_ref, len(c.text), c.heading_path))
    print("     text=%r" % c.text.replace("\n", " / "))
    print("     meta=%r" % (c.meta,))

by_ref = {c.source_ref: c for c in ck}
print()
print("=== by_ref 键 ===", sorted(by_ref.keys()))
for k in ("p.26", "p.27", "p.1"):
    print("  %-6s in by_ref: %s" % (k, k in by_ref))
