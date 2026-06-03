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
from functools import lru_cache
from typing import Iterable

import numpy as np
from fastembed import TextEmbedding
from fastembed.rerank.cross_encoder import TextCrossEncoder
from loguru import logger

from config import settings


EMBED_DIM = 1024   # BGE-M3 dense dimension


@lru_cache(maxsize=1)
def _embed() -> TextEmbedding:
    logger.info(f"Loading embedding model: {settings.embed_model}")
    return TextEmbedding(model_name=settings.embed_model)


@lru_cache(maxsize=1)
def _reranker() -> TextCrossEncoder:
    logger.info(f"Loading reranker model: {settings.reranker_model}")
    return TextCrossEncoder(model_name=settings.reranker_model)


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
