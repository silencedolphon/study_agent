"""从 ModelScope 下载 ONNX 嵌入模型。

本机 huggingface.co / hf-mirror.com / github.com 均不可达，ModelScope 是唯一可用源。
只用标准库 urllib，不依赖 modelscope 包。

用法:
    python tools/download_model.py                    # 下载 .env 中配置的模型
    python tools/download_model.py --repo Xenova/bge-m3
    python tools/download_model.py --list             # 只列出仓库文件不下载
"""
from __future__ import annotations

import argparse
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core import config  # noqa: E402

# 推理必需文件；model.onnx 与 tokenizer.json 缺一不可
REQUIRED = ["config.json", "tokenizer.json", "tokenizer_config.json",
            "special_tokens_map.json", "vocab.txt"]
OPTIONAL = ["onnx/model_quantized.onnx", "onnx/model.onnx", "onnx/model_fp16.onnx"]

UA = {"User-Agent": "Mozilla/5.0 (study_agent model fetcher)"}


def resolve_url(repo: str, filename: str, revision: str = "master") -> str:
    return f"{config.MODELSCOPE_ENDPOINT}/models/{repo}/resolve/{revision}/{filename}"


def head_ok(url: str, timeout: int = 15) -> int | None:
    req = urllib.request.Request(url, method="HEAD", headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:
        return None


def download(url: str, dest: Path, timeout: int = 120) -> int:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    req = urllib.request.Request(url, headers=UA)
    total = 0
    with urllib.request.urlopen(req, timeout=timeout) as r, open(tmp, "wb") as f:
        while True:
            block = r.read(1 << 20)
            if not block:
                break
            f.write(block)
            total += len(block)
            print(f"\r    {dest.name}: {total/1048576:.1f} MB", end="", flush=True)
    print()
    tmp.replace(dest)
    return total


def main() -> int:
    ap = argparse.ArgumentParser(description="从 ModelScope 获取嵌入模型")
    ap.add_argument("--repo", default=config.EMBED_MODEL_REPO)
    ap.add_argument("--dir", default=str(config.MODEL_DIR))
    ap.add_argument("--onnx", default=config.EMBED_ONNX_FILE,
                    help="要下载的 onnx 文件，如 onnx/model_quantized.onnx")
    ap.add_argument("--revision", default="master")
    ap.add_argument("--list", action="store_true", help="只探测可用文件")
    args = ap.parse_args()

    target = Path(args.dir)
    print(f"仓库   : {args.repo}")
    print(f"目标   : {target}")
    print(f"端点   : {config.MODELSCOPE_ENDPOINT}\n")

    plan: list[str] = []
    for name in REQUIRED + [args.onnx]:
        st = head_ok(resolve_url(args.repo, name, args.revision))
        mark = "OK  " if st == 200 else f"HTTP {st}"
        print(f"  [{mark}] {name}")
        if st == 200:
            plan.append(name)
        elif name == args.onnx:
            print(f"    !! 关键文件 {name} 不可用，请用 --onnx 指定其它精度")
            return 2

    if args.list:
        print("\n(仅探测，未下载)")
        return 0

    print("\n开始下载:")
    for name in plan:
        dest = target / name
        if dest.exists() and dest.stat().st_size > 0:
            print(f"    跳过（已存在）: {name}")
            continue
        download(resolve_url(args.repo, name, args.revision), dest)

    print(f"\n完成。模型目录: {target}")
    print("自检:  python tools/check_env.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())