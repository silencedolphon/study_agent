# Copyright (c) 2026 潜声海豚 <2227663973@qq.com>. All rights reserved.
# 本源码为著作权人私有作品，适用项目根目录 LICENSE（专有许可）。
# 允许个人安装与使用；禁止反向工程、修改、再分发、商业使用及声称原创。
# 本软件不含源码，按「现状」提供；详见 NOTICE.txt。     
# File-ID: SA-06-3787ae1a   (project study_agent; keep this line)
"""本地 ONNX 嵌入引擎。

选型说明：
- 模型 bge-small-zh-v1.5（Xenova 的 ONNX 导出，从 ModelScope 下载）。
  本机 huggingface.co 与 hf-mirror.com 均不可达，ModelScope 可达。
- 用 onnxruntime + tokenizers 而非 sentence-transformers + torch：
  省掉约 2.5GB 的 torch 下载，且无需 CUDA 依赖。对本机（RTX 4060 Laptop）
  CPU 推理 bge-small 已足够快。
- BGE 的池化方式是 **CLS token（句首）+ L2 归一化**，不是 mean pooling。
  用错池化方式会显著降低检索质量。
- 查询侧需加指令前缀，文档侧不加。
"""
from __future__ import annotations

import numpy as np
import onnxruntime as ort
from tokenizers import Tokenizer

from . import config


class Embedder:
    def __init__(self, model_dir=None, onnx_file=None, max_tokens=None) -> None:
        model_dir = model_dir or config.MODEL_DIR
        onnx_file = onnx_file or config.EMBED_ONNX_FILE
        self.max_tokens = max_tokens or config.EMBED_MAX_TOKENS

        onnx_path = model_dir / onnx_file
        tok_path = model_dir / "tokenizer.json"
        if not onnx_path.exists():
            raise FileNotFoundError(
                f"缺少模型文件 {onnx_path}\n请先运行: python tools/download_model.py"
            )
        if not tok_path.exists():
            raise FileNotFoundError(f"缺少 tokenizer.json: {tok_path}")

        self.tokenizer = Tokenizer.from_file(str(tok_path))
        self.tokenizer.enable_truncation(max_length=self.max_tokens)
        self.tokenizer.enable_padding(pad_id=0, pad_token="[PAD]")

        opts = ort.SessionOptions()
        opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        opts.intra_op_num_threads = 0  # 交给 onnxruntime 决定
        self.session = ort.InferenceSession(
            str(onnx_path), sess_options=opts, providers=["CPUExecutionProvider"]
        )
        self._input_names = {i.name for i in self.session.get_inputs()}
        self.dim = config.EMBED_DIM

    # ---- 内部 ----
    def _forward(self, texts: list[str]) -> np.ndarray:
        encs = self.tokenizer.encode_batch(texts)
        input_ids = np.array([e.ids for e in encs], dtype=np.int64)
        attention_mask = np.array([e.attention_mask for e in encs], dtype=np.int64)
        token_type_ids = np.zeros_like(input_ids)

        feed = {"input_ids": input_ids, "attention_mask": attention_mask}
        if "token_type_ids" in self._input_names:
            feed["token_type_ids"] = token_type_ids

        (last_hidden,) = self.session.run(None, feed)
        # BGE: CLS pooling -> 取每个序列第一个 token
        cls = last_hidden[:, 0, :]
        return cls.astype(np.float32)

    @staticmethod
    def _normalize(mat: np.ndarray) -> np.ndarray:
        norms = np.linalg.norm(mat, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return mat / norms

    # ---- 对外 ----
    def embed_documents(self, texts: list[str], batch_size: int = 16,
                        progress: bool = False) -> np.ndarray:
        """文档侧嵌入：不加指令前缀。"""
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        out: list[np.ndarray] = []
        for i in range(0, len(texts), batch_size):
            out.append(self._forward(texts[i : i + batch_size]))
            if progress:
                done = min(i + batch_size, len(texts))
                print(f"  embedded {done}/{len(texts)}", flush=True)
        return self._normalize(np.vstack(out))

    def embed_query(self, text: str) -> np.ndarray:
        """查询侧嵌入：加 BGE 指令前缀。"""
        vec = self._forward([config.QUERY_PREFIX + text])
        return self._normalize(vec)[0]

    def embed_queries(self, texts: list[str]) -> np.ndarray:
        prefixed = [config.QUERY_PREFIX + t for t in texts]
        return self._normalize(self._forward(prefixed))


_engine: Embedder | None = None


def get_embedder() -> Embedder:
    """进程内单例，避免重复加载模型。"""
    global _engine
    if _engine is None:
        _engine = Embedder()
    return _engine


def to_blob(vec: np.ndarray) -> bytes:
    """float32 向量 -> BLOB，便于直接存进 SQLite。"""
    return np.asarray(vec, dtype=np.float32).tobytes()


def from_blob(blob: bytes, dim: int | None = None) -> np.ndarray:
    # 注意：`dim` 参数被忽略，这里不做维度校验（历史遗留占位参数，保留签名用）。
    return np.frombuffer(blob, dtype=np.float32).copy()