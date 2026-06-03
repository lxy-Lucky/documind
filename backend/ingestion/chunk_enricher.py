"""Multi-perspective chunk enrichment.

For each parsed chunk, ask Qwen3-14B to produce three summaries:
    - technical : keeps original terminology (JIRA codes, column names,
                  Japanese/English technical terms preserved verbatim)
    - business  : plain-language Chinese description of what this does
    - keywords  : multilingual keyword cloud (zh/ja/en aliases)

We use a labeled-section format (TECHNICAL: / BUSINESS: / KEYWORDS:)
instead of JSON because 14B models truncate / break JSON quotes on
long outputs, especially with Japanese content. The section format is
forgiving — even partial outputs salvage 1-2 perspectives.

Configurable via `settings.enable_multi_perspective`; when off, the
orchestrator skips this step.
"""

from __future__ import annotations

import asyncio
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

    def is_empty(self) -> bool:
        return not (self.technical or self.business or self.keywords)


# Section labels we expect, in order. The parser is tolerant of casing
# / surrounding whitespace / colon/full-width-colon variations.
_LABELS = ("TECHNICAL", "BUSINESS", "KEYWORDS")
_LABEL_PAT = re.compile(
    r"^\s*(TECHNICAL|BUSINESS|KEYWORDS|END)\s*[:：]?\s*$",
    re.IGNORECASE | re.MULTILINE,
)


def _parse_sections(raw: str) -> ChunkSummaries:
    """Pull TECHNICAL / BUSINESS / KEYWORDS sections out of raw text."""
    if not raw.strip():
        return ChunkSummaries("", "", "")

    # Strip any code fences the model may have wrapped output in.
    txt = raw.strip()
    if txt.startswith("```"):
        txt = re.sub(r"^```[a-zA-Z]*", "", txt).rstrip("`").strip()

    sections: dict[str, str] = {}
    cur_label: str | None = None
    cur_buf: list[str] = []
    for line in txt.splitlines():
        m = _LABEL_PAT.match(line)
        if m:
            # Flush previous
            if cur_label and cur_label != "END":
                sections[cur_label] = "\n".join(cur_buf).strip()
            cur_label = m.group(1).upper()
            cur_buf = []
            continue
        if cur_label and cur_label != "END":
            cur_buf.append(line)
    # Flush trailing
    if cur_label and cur_label != "END" and cur_label not in sections:
        sections[cur_label] = "\n".join(cur_buf).strip()

    return ChunkSummaries(
        technical=sections.get("TECHNICAL", "").strip(),
        business=sections.get("BUSINESS", "").strip(),
        keywords=sections.get("KEYWORDS", "").strip(),
    )


async def _enrich_one(text: str, attempt: int = 1) -> ChunkSummaries | None:
    """Call the LLM and parse. Retries once on empty / fully-blank output."""
    prompt = _PROMPT_TEMPLATE.replace("{CONTENT}", text[:2500])
    try:
        raw = await ollama.chat(
            [{"role": "user", "content": prompt}],
            options={
                "temperature": 0.2,
                "num_predict": 1200,  # plenty of room for 3 paragraphs of Japanese
            },
        )
    except Exception as e:
        logger.warning(f"Enrichment LLM call failed (attempt {attempt}): {e}")
        return None

    summaries = _parse_sections(raw)
    if summaries.is_empty():
        if attempt < 2:
            logger.warning(
                f"Enrichment empty output (attempt {attempt}); retrying. raw[:120]: {raw[:120]!r}"
            )
            return await _enrich_one(text, attempt=attempt + 1)
        logger.warning(f"Enrichment empty after retry; raw[:120]: {raw[:120]!r}")
        return None
    return summaries


async def enrich_chunks(texts: list[str], concurrency: int = 2) -> list[ChunkSummaries | None]:
    """Enrich a list of chunk texts with bounded concurrency.

    Ollama with a 14B model handles one inference at a time per GPU;
    keeping concurrency low avoids queue stalls.
    """
    if not settings.enable_multi_perspective:
        return [None] * len(texts)

    sem = asyncio.Semaphore(concurrency)

    async def _bounded(t: str) -> ChunkSummaries | None:
        async with sem:
            return await _enrich_one(t)

    return await asyncio.gather(*(_bounded(t) for t in texts))
