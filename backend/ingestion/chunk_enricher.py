"""Multi-perspective chunk enrichment.

For each parsed chunk, ask Qwen3-14B to produce three summaries:
    - technical : keeps original terminology (JIRA codes, column names,
                  Japanese/English technical terms preserved verbatim)
    - business  : plain-language Chinese description of what this does
    - keywords  : multilingual keyword cloud (zh/ja/en aliases)

These three summaries are embedded separately so a user query in any
of three languages has multiple paths to hit the right chunk.

Configurable via `settings.enable_multi_perspective`; when off, the
orchestrator skips this step and only embeds the chunk's own text.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

from config import settings
from llm.ollama_client import ollama


_PROMPT_PATH = Path(__file__).parent.parent / "llm" / "prompts" / "enrich.txt"
_PROMPT_TEMPLATE = _PROMPT_PATH.read_text(encoding="utf-8")


@dataclass
class ChunkSummaries:
    technical: str
    business: str
    keywords: str


async def _enrich_one(text: str) -> ChunkSummaries | None:
    prompt = _PROMPT_TEMPLATE.replace("{CONTENT}", text[:3000])  # cap to keep prompt small
    try:
        raw = await ollama.chat(
            [{"role": "user", "content": prompt}],
            options={"temperature": 0.2, "num_predict": 600},
        )
    except Exception as e:
        logger.warning(f"Enrichment LLM call failed: {e}")
        return None

    txt = raw.strip()
    if txt.startswith("```"):
        txt = re.sub(r"^```[a-zA-Z]*", "", txt).rstrip("`").strip()
    try:
        obj = json.loads(txt)
        return ChunkSummaries(
            technical=str(obj.get("technical", "")).strip(),
            business=str(obj.get("business", "")).strip(),
            keywords=str(obj.get("keywords", "")).strip(),
        )
    except Exception as e:
        logger.warning(f"Enrichment JSON parse failed: {e}, raw: {raw[:200]!r}")
        return None


async def enrich_chunks(texts: list[str], concurrency: int = 2) -> list[ChunkSummaries | None]:
    """Enrich a list of chunk texts with bounded concurrency.

    Ollama with a 14B model is single-batch; running 2 in parallel keeps
    GPU saturated without queueing too much. Tune if needed.
    """
    if not settings.enable_multi_perspective:
        return [None] * len(texts)

    sem = asyncio.Semaphore(concurrency)

    async def _bounded(t: str) -> ChunkSummaries | None:
        async with sem:
            return await _enrich_one(t)

    return await asyncio.gather(*(_bounded(t) for t in texts))
