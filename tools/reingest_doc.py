r"""重新入库指定文档，让提取/切块逻辑的改动生效。

## 为什么需要它

`ingest` 用文件内容的 sha256 判重，源文件没改动就被判为"内容未变"跳过——
而改的是**提取与切块逻辑**，文件哈希当然没变。所以必须 `force=True`。

## ⚠ 必须先把识图块存下来

`ingest_one` 里那句 `DELETE FROM chunks WHERE doc_id=?` 是按**文档**删的，
会把该文档后来手工插入的识图块一起删掉。实测第一次跑完，
doc#58 从 179 块掉到 21 块——156 个识图块被静默清掉，
而 `figure_desc` 的页级记录还在，从"页数/块数"看完全正常。

识图是**花过钱**的（doc#58 那批 ¥0.4612、doc#89 那批 ¥1.3345），
所以这里先快照、后写回，不重跑。

用法:
    python tools/reingest_doc.py --doc-id 89 --dry-run
    python tools/reingest_doc.py --doc-id 89
    python tools/reingest_doc.py --all-pdf        # 所有 pdf（表格还原生效）
"""
from __future__ import annotations

import argparse
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from core import config, db, ingest  # noqa: E402
from reingest_pptx import restore_figure_chunks, snapshot_figure_chunks  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--doc-id", type=int, default=0)