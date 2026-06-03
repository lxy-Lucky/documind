"""Embedding & reranker via FastEmbed.

FastEmbed runs ONNX models locally on CPU (or GPU if available). The
first call downloads the model to `~/.cache/fastembed`; subsequent
calls are fast.

Two singletons:
    embed_model     - BGE-M3 dense (1024-dim)
    reranker_model  - bge-reranker-v2-m3 (cross-encoder)
"""

from __future__ import annotations

import asyncio
import struct
import threading
from typing import Iterable

import numpy as np
from fastembed import TextEmbedding
from fastembed.rerank.cross_encoder import TextCrossEncoder
from loguru import logger

from config import settings


EMBED_DIM = 1024   # BGE-M3 dense dimension

# Concurrent calls during the first load would each kick off a separate
# download of the ~2GB model. We serialize behind a lock and cache the
# instance for the whole process lifetime.
_embed_singleton: TextEmbedding | None = None
_rerank_singleton: TextCrossEncoder | None = None
_embed_lock = threading.Lock()
_rerank_lock = threading.Lock()


def _embed() -> TextEmbedding:
    global _embed_singleton
    if _embed_singleton is not None:
        return _embed_singleton
    with _embed_lock:
        if _embed_singleton is None:
            logger.info(f"Loading embedding model: {settings.embed_model} (first call, may download)")
            _embed_singleton = TextEmbedding(model_name=settings.embed_model)
            logger.info("Embedding model ready")
    return _embed_singleton


def _reranker() -> TextCrossEncoder:
    global _rerank_singleton
    if _rerank_singleton is not None:
        return _rerank_singleton
    with _rerank_lock:
        if _rerank_singleton is None:
            logger.info(f"Loading reranker model: {settings.reranker_model} (first call, may download)")
            _rerank_singleton = TextCrossEncoder(model_name=settings.reranker_model)
            logger.info("Reranker model ready")
    return _rerank_singleton


def warmup() -> None:
    """Eagerly load both models. Call from startup so the first
    user-facing request doesn't pay the download/load tax."""
    _embed()
    _reranker()


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Batch-embed a list of texts. Returns list of vectors."""
    if not texts:
        return []
    model = _embed()
    # FastEmbed returns a generator of np.ndarray
    vecs = list(model.embed(texts))
    return [v.tolist() for v in vecs]


async def embed_texts_async(texts: list[str]) -> list[list[float]]:
    """Run embedding in a thread; FastEmbed is sync/CPU-bound."""
    return await asyncio.to_thread(embed_texts, texts)


def rerank(query: str, docs: list[str]) -> list[float]:
    """Return cross-encoder scores aligned to `docs` (higher = better)."""
    if not docs:
        return []
    model = _reranker()
    scores = list(model.rerank(query, docs))
    return [float(s) for s in scores]


async def rerank_async(query: str, docs: list[str]) -> list[float]:
    return await asyncio.to_thread(rerank, query, docs)


# ---------- BLOB helpers for sqlite-vec ----------

def vector_to_blob(vec: Iterable[float]) -> bytes:
    """Pack a float32 vector for sqlite-vec storage."""
    arr = np.asarray(list(vec), dtype=np.float32)
    return arr.tobytes()


def blob_to_vector(blob: bytes) -> list[float]:
    n = len(blob) // 4
    return list(struct.unpack(f"{n}f", blob))
