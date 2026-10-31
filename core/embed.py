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

# ---------------- 确定性向量复用（P1-A / S07） ----------------
import hashlib as _hashlib          # noqa: E402
import threading as _threading      # noqa: E402
from collections import OrderedDict as _OrderedDict  # noqa: E402

_EMBED_CACHE: "_OrderedDict[str, np.ndarray]" = _OrderedDict()
_EMBED_CACHE_LOCK = _threading.RLock()
_EMBED_CACHE_MAX = 4096
_EMBED_CACHE_STATS = {"hit": 0, "miss": 0, "forward": 0, "saved": 0}


def embed_cache_signature() -> str:
    """模型/维度/归一化/切词口径指纹：不同模型/维度绝不混用。"""
    return "|".join([
        "bge", str(config.EMBED_MODEL_REPO), str(config.EMBED_ONNX_FILE),
        "dim=%d" % int(config.EMBED_DIM), "max_tokens=%d" % int(config.EMBED_MAX_TOKENS),
        "pool=cls", "norm=l2", "query_prefix",
    ])


def embed_cache_key(text: str) -> str:
    raw = embed_cache_signature() + "\n" + str(text)
    return _hashlib.sha256(raw.encode("utf-8")).hexdigest()


def clear_embed_cache() -> None:
    with _EMBED_CACHE_LOCK:
        _EMBED_CACHE.clear()
        for k in _EMBED_CACHE_STATS:
            _EMBED_CACHE_STATS[k] = 0


def embed_cache_stats() -> dict:
    with _EMBED_CACHE_LOCK:
        return dict(_EMBED_CACHE_STATS, size=len(_EMBED_CACHE))


def embed_documents_cached(texts: list[str], embedder=None, batch_size: int = 16,
                           progress: bool = False):
    """带进程内缓存的 `embed_documents`：同文本/同模型/同维度只前向一次。

    只缓存向量（不含正文）；miss 才调用 ONNX。旧 embeddings 表/读取路径完全不改。
    """
    emb = embedder or get_embedder()
    texts = list(texts)
    n = len(texts)
    dim = int(getattr(emb, "dim", config.EMBED_DIM))
    out = [None] * n
    missing: list[tuple[int, str, str]] = []
    with _EMBED_CACHE_LOCK:
        for i, text in enumerate(texts):
            key = embed_cache_key(text)
            vec = _EMBED_CACHE.get(key)
            if vec is not None and getattr(vec, "shape", (0, 0))[-1] == dim:
                _EMBED_CACHE.move_to_end(key)
                out[i] = vec
                _EMBED_CACHE_STATS["hit"] += 1
                _EMBED_CACHE_STATS["saved"] += 1
            else:
                _EMBED_CACHE_STATS["miss"] += 1
                missing.append((i, key, text))
    if missing:
        miss_texts = [m[2] for m in missing]
        try:
            vecs = emb.embed_documents(miss_texts, batch_size=batch_size,
                                       progress=progress)
        except TypeError:
            # 兼容旧/测试 embedder 的签名（只接受 texts）
            vecs = emb.embed_documents(miss_texts)
        vecs = np.asarray(vecs)
        if vecs.ndim != 2 or vecs.shape[0] != len(missing):
            raise RuntimeError("嵌入器返回的向量形状与输入不匹配")
        with _EMBED_CACHE_LOCK:
            _EMBED_CACHE_STATS["forward"] += 1
            for row, (i, key, _text) in enumerate(missing):
                vec = np.array(vecs[row], dtype=vecs.dtype, copy=True)
                out[i] = vec
                if vec.shape[-1] == dim:
                    _EMBED_CACHE[key] = vec
                    _EMBED_CACHE.move_to_end(key)
                    while len(_EMBED_CACHE) > _EMBED_CACHE_MAX:
                        _EMBED_CACHE.popitem(last=False)
    if n == 0:
        return np.zeros((0, dim), dtype="float32")
    return np.vstack([np.asarray(v) for v in out])
