"""Generic fallback parser.

Used when:
    - The classifier returned `unknown` with no confidence.
    - Or a sheet shape doesn't match any other parser.

Strategy:
    - If a screenshot is available, ask the VL model to summarize and
      enumerate the structure it sees, then split into chunks based on
      the structure it returned.
    - Otherwise, dump the cell text into a single chunk.

This guarantees no sheet is ever silently dropped.
"""

from __future__ import annotations

import json
import re

from loguru import logger

from ingestion.excel_utils import SheetSnapshot
from ingestion.parsers.base import (
    BaseSheetParser,
    ParsedChunk,
    ParserContext,
    ParserOutput,
)
from llm.ollama_client import ollama


_VL_PROMPT = """这是一份 Excel 工作表的截图。请仔细识别它的结构并输出 JSON：

{
  "summary": "这张表整体在讲什么（1-2 句）",
  "sections": [
    {"title": "小节标题", "content": "该小节主要内容（保留关键术语与数字）"}
  ]
}

只输出 JSON，不要附加任何文字。sections 应该尽量按视觉上的分组拆分；
如果整张表就是一个整体，sections 数组里放一项即可。"""


def _dump_text(snap: SheetSnapshot) -> str:
    rows: dict[int, list] = {}
    for c in snap.cells:
        rows.setdefault(c.row, []).append(c)
    out: list[str] = []
    for row_idx in sorted(rows):
        cells = sorted(rows[row_idx], key=lambda x: x.col)
        out.append(" ".join(c.text for c in cells))
    return "\n".join(out)


async def _vl_structure(image_path) -> dict:
    try:
        raw = await ollama.vision(_VL_PROMPT, [image_path])
        txt = raw.strip()
        if txt.startswith("```"):
            txt = re.sub(r"^```[a-zA-Z]*", "", txt).rstrip("`").strip()
        return json.loads(txt)
    except Exception as e:
        logger.warning(f"Generic VL fallback failed: {e}")
        return {}


class GenericParser(BaseSheetParser):
    name = "generic"

    async def parse(self, ctx: ParserContext) -> ParserOutput:
        snap = ctx.snapshot
        text_dump = _dump_text(snap)
        doc_jiras = ctx.document_metadata.get("jira_codes", [])

        # Try VL structuring first if a screenshot is available
        structured: dict = {}
        if ctx.screenshot_path is not None:
            structured = await _vl_structure(ctx.screenshot_path)

        chunks: list[ParsedChunk] = []
        if structured and structured.get("sections"):
            order = 0
            for sec in structured["sections"]:
                title = sec.get("title") or f"{snap.name} 区块 {order + 1}"
                content = sec.get("content") or ""
                if not content:
                    continue
                md = f"### {snap.name} — {title}\n\n{content}"
                chunks.append(ParsedChunk(
                    text=f"{title} {content}",
                    markdown=md,
                    metadata={
                        "kind": "generic_section",
                        "sheet": snap.name,
                        "summary": structured.get("summary"),
                        "jira_tags": doc_jiras,
                    },
                    hierarchy_path=f"{snap.name} > {title}",
                    order=order,
                ))
                order += 1

        # Always also keep a raw-text chunk as a safety net (cell text
        # carries exact identifiers VL might paraphrase away).
        if text_dump.strip():
            chunks.append(ParsedChunk(
                text=text_dump,
                markdown=f"### {snap.name} (raw)\n\n```\n{text_dump}\n```",
                metadata={
                    "kind": "generic_raw",
                    "sheet": snap.name,
                    "jira_tags": doc_jiras,
                },
                hierarchy_path=snap.name,
                order=len(chunks),
            ))

        return ParserOutput(chunks=chunks)
