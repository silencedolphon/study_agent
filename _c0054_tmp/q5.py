# C-0054 实测 5：A 问 —— "渲染产物即插即用"验证 + 看板读图链路是否依赖 figure_desc 表
# 手法：import tools/dashboard.py（它不会启动服务，main() 在文件末尾 __main__ 里），
#       把 _figure_desc_root() 用内存 monkeypatch 指到临时目录树，**不碰生产 logs/**。
# 只读数据库（mode=ro）、只读产品代码。
import shutil
import sqlite3
import sys
from pathlib import Path

ROOT = Path(r"G:\study_agent")
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import importlib.util  # noqa: E402
spec = importlib.util.spec_from_file_location("dash_c0054", ROOT / "tools" / "dashboard.py")
dash = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dash)
print("dashboard 模块已加载（未启动服务；main() 只在 __main__ 里调用）")
print("  生产 figure_desc 根 =", dash._figure_desc_root())

# ---- 构造临时"伪 figure_desc 根"：只放 doc#85 的渲染产物 ----
TMPROOT = ROOT / "_c0054_tmp" / "deckroot"
DECK = TMPROOT / "抗心律失常药"
if TMPROOT.exists():
    shutil.rmtree(TMPROOT)
DECK.mkdir(parents=True)
src = ROOT / "_c0054_tmp" / "render85"
n = 0
for f in sorted(src.glob("slide_*.png")):
    shutil.copy2(f, DECK / f.name)
    n += 1
src89 = ROOT / "_c0054_tmp" / "render89"
if src89.is_dir():
    D89 = TMPROOT / "胰岛素和口服降糖药-课件童擎一20241203"
    D89.mkdir(parents=True, exist_ok=True)
    for f in sorted(src89.glob("slide_*.png")):
        shutil.copy2(f, D89 / f.name)
        n += 1
print(f"  临时伪根: {TMPROOT}（复制 {n} 张 png，仅临时目录）")

DB = ROOT / "index" / "study.db"
conn = sqlite3.connect("file:" + str(DB).replace("\\", "/") + "?mode=ro", uri=True)
conn.row_factory = sqlite3.Row

for label, root in (("生产根（现状）", dash._figure_desc_root()),
                    ("临时伪根（模拟补图后）", TMPROOT)):
    dash._figure_desc_root = (lambda r=root: r)
    dash._SLIDE_CACHE.clear()
    print()
    print("=" * 78)
    print(f"## {label}: {root}")
    for did in (85, 89):
        p = conn.execute("SELECT path FROM documents WHERE id=?", (did,)).fetchone()
        if not p:
            continue
        doc_path = p["path"]
        deck = dash._deck_dir_for(doc_path)
        files = dash._slide_files(deck) if deck else {}
        print(f"  doc#{did}  path={doc_path!r}")
        print(f"    _deck_dir_for -> {deck}")
        print(f"    _slide_files  -> {len(files)} 张（前 3 个页号 "
              f"{sorted(files)[:3]}，末页 {sorted(files)[-1] if files else '-'}）")
        info = dash._slide_info(did, [1, 20, 29, 80], conn=conn)
        print(f"    _slide_info(页 1/20/29/80) -> deck={info['deck']!r}, "
              f"slides={info['slides']}")
        png = dash._slide_png_path(did, 20, conn=conn)
        print(f"    _slide_png_path(20) -> {png}")
        if png:
            print(f"      存在={png.is_file()} 大小={png.stat().st_size} B")
        # /img/slide 的白名单判据（路由层只认 slide_<数字>.png）
        import re as _re
        pat = "slide_" + chr(92) + "d+" + chr(92) + ".png"   # 等价于 r"slide_\d+\.png"
        print(f"    路由白名单 re.fullmatch({pat!r}) 通过 = "
              f"{bool(png and _re.fullmatch(pat, png.name))}")

# ---- 反向验证：doc#92（stem 带 _slides，存在两个候选目录）----
dash._figure_desc_root = (lambda: ROOT / "logs" / "figure_desc")
dash._SLIDE_CACHE.clear()
p = conn.execute("SELECT path FROM documents WHERE id=92").fetchone()
print()
print("=" * 78)
print("## 附：doc#92（path 里的 stem 带 _slides）落在哪个目录")
print("  path =", p["path"])
print("  _deck_dir_for ->", dash._deck_dir_for(p["path"]))
for cand in ("2-卢 心绞痛2025", "2-卢 心绞痛2025_slides"):
    d = ROOT / "logs" / "figure_desc" / cand
    print(f"    候选 {cand!r}: 存在={d.is_dir()} png="
          f"{len(list(d.glob('slide_*.png'))) if d.is_dir() else '-'}")
conn.close()
print()
print("完成。生产 logs/ 未被写入（本脚本只往 _c0054_tmp 复制）。")
