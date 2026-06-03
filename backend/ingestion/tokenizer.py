"""Japanese-aware tokenizer for FTS5 indexing.

Lindera (Rust under the hood) gives us MeCab-quality Japanese
segmentation. For Chinese/English mixed-in content, Lindera falls back
gracefully (Chinese characters get per-char tokens, English stays
whitespace-split).

Public:
    tokenize(text)            -> list[str]
    tokenize_for_fts(text)    -> space-joined string for FTS5 insert
"""

from __future__ import annotations

from functools import lru_cache

from loguru import logger

try:
    from lindera import Lindera           # lindera-python 3.x
    _HAS_LINDERA = True
except Exception as e:  # pragma: no cover
    logger.warning(f"lindera not available, falling back to char-split: {e}")
    Lindera = None
    _HAS_LINDERA = False


@lru_cache(maxsize=1)
def _get_tokenizer():
    if not _HAS_LINDERA:
        return None
    # ipadic = the standard Japanese dictionary; bundled in lindera-python
    return Lindera(dictionary="ipadic", mode="normal")


def tokenize(text: str) -> list[str]:
    if not text:
        return []
    tk = _get_tokenizer()
    if tk is None:
        # Fallback: split on whitespace AND per-char for CJK
        out: list[str] = []
        for piece in text.split():
            if any("　" <= ch <= "鿿" for ch in piece):
                out.extend(list(piece))
            else:
                out.append(piece)
        return out
    try:
        tokens = tk.tokenize(text)
        return [t.text for t in tokens if t.text.strip()]
    except Exception as e:
        logger.warning(f"lindera tokenize failed: {e}")
        return text.split()


def tokenize_for_fts(text: str) -> str:
    return " ".join(tokenize(text))
