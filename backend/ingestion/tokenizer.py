"""Japanese-aware tokenizer for FTS5 indexing.

`lindera-python` exposes different APIs across versions. We probe them
at import time and pick whichever works; if none do, we fall back to a
character-level split that's good enough for mixed CJK queries.
"""

from __future__ import annotations

from functools import lru_cache

from loguru import logger


# ---------------------------------------------------------------------------
# API probing
# ---------------------------------------------------------------------------

def _probe_tokenizer():
    """Return a callable `tokenize(text) -> list[str]` or None.

    We try the known lindera-python API surfaces in order; the first
    successful probe wins.
    """
    # 3.x API: Tokenizer + Segmenter + load_dictionary
    try:
        from lindera import Tokenizer, Segmenter, load_dictionary  # type: ignore
        dictionary = load_dictionary("ipadic")
        segmenter = Segmenter("normal", dictionary)
        tok = Tokenizer(segmenter)
        def _t(text: str):
            return [t.text for t in tok.tokenize(text)]
        _t("試験")
        logger.info("tokenizer: lindera 3.x (Tokenizer+Segmenter) ✓")
        return _t
    except Exception as e:
        logger.debug(f"tokenizer probe (3.x A): {e}")

    # 3.x alt: TokenizerConfigBuilder
    try:
        from lindera import TokenizerConfigBuilder, Tokenizer  # type: ignore
        cfg = TokenizerConfigBuilder()
        cfg.set_dictionary_kind("ipadic")
        cfg.set_mode("normal")
        tok = Tokenizer(cfg.build())
        def _t(text: str):
            return [t.text for t in tok.tokenize(text)]
        _t("試験")
        logger.info("tokenizer: lindera 3.x (TokenizerConfigBuilder) ✓")
        return _t
    except Exception as e:
        logger.debug(f"tokenizer probe (3.x B): {e}")

    # 2.x / 0.x API: Lindera class
    try:
        from lindera import Lindera  # type: ignore
        tok = Lindera(dictionary="ipadic", mode="normal")
        def _t(text: str):
            return [t.text for t in tok.tokenize(text)]
        _t("試験")
        logger.info("tokenizer: lindera legacy (Lindera) ✓")
        return _t
    except Exception as e:
        logger.debug(f"tokenizer probe (legacy): {e}")

    # lindera_py module (older PyPI name)
    try:
        from lindera_py import Tokenizer  # type: ignore
        tok = Tokenizer()
        def _t(text: str):
            return [t.text for t in tok.tokenize(text)]
        _t("試験")
        logger.info("tokenizer: lindera_py module ✓")
        return _t
    except Exception as e:
        logger.debug(f"tokenizer probe (lindera_py): {e}")

    logger.warning("no Lindera variant worked; using char-split fallback (Japanese FTS quality reduced)")
    return None


@lru_cache(maxsize=1)
def _get_impl():
    return _probe_tokenizer()


def _fallback(text: str) -> list[str]:
    out: list[str] = []
    for piece in text.split():
        if any("぀" <= ch <= "ヿ" or "一" <= ch <= "鿿" for ch in piece):
            # split CJK char-by-char, keep ASCII pieces whole
            cur: list[str] = []
            buf = ""
            for ch in piece:
                is_cjk = "぀" <= ch <= "ヿ" or "一" <= ch <= "鿿"
                if is_cjk:
                    if buf:
                        cur.append(buf)
                        buf = ""
                    cur.append(ch)
                else:
                    buf += ch
            if buf:
                cur.append(buf)
            out.extend(cur)
        else:
            out.append(piece)
    return out


def tokenize(text: str) -> list[str]:
    if not text:
        return []
    impl = _get_impl()
    if impl is None:
        return _fallback(text)
    try:
        return [t for t in impl(text) if t.strip()]
    except Exception as e:
        logger.warning(f"tokenize failed, falling back: {e}")
        return _fallback(text)


def tokenize_for_fts(text: str) -> str:
    """Tokens joined by spaces, used when *writing* to FTS5."""
    return " ".join(tokenize(text))


def tokenize_for_fts_query(text: str) -> str:
    """Tokens for an FTS5 MATCH query.

    Each token is wrapped in double quotes so FTS5 treats it as a literal
    phrase — this prevents special characters (-/+/:/^/AND/OR/NEAR) inside
    user input (e.g. 'DBL-179', 'KAIZEN-2635') from being interpreted as
    operators. Internal double-quotes are escaped per FTS5 spec.
    """
    out: list[str] = []
    for t in tokenize(text):
        t = t.strip()
        if not t:
            continue
        # FTS5 escapes a double-quote inside a quoted phrase by doubling it.
        escaped = t.replace('"', '""')
        out.append(f'"{escaped}"')
    return " ".join(out)
