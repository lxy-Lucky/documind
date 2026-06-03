"""Change-history (変更履歴) parser.

Each row is typically `<date> <description>`. We extract entries into
`change_log` table and also emit one chunk per entry so they're
retrievable like normal content (users *do* ask "when was DBL-364
added?").
"""

from __future__ import annotations

import re

from ingestion.parsers.base import (
    BaseSheetParser,
    ChangeLogEntry,
    ParsedChunk,
    ParserContext,
    ParserOutput,
)


_DATE_PAT = re.compile(
    r"(?P<y>\d{4})[年./-](?P<m>\d{1,2})[月./-](?P<d>\d{1,2})日?"
)
_JIRA_PAT = re.compile(r"\b([A-Z]{2,}[A-Z0-9]*-\d+)\b")


def _normalize_date(m: re.Match) -> str:
    return f"{m.group('y')}-{int(m.group('m')):02d}-{int(m.group('d')):02d}"


class HistoryParser(BaseSheetParser):
    name = "history"

    async def parse(self, ctx: ParserContext) -> ParserOutput:
        # Group cells by row, sorted by column.
        rows: dict[int, list] = {}
        for c in ctx.snapshot.cells:
            rows.setdefault(c.row, []).append(c)

        entries: list[ChangeLogEntry] = []
        chunks: list[ParsedChunk] = []
        order = 0
        for row_idx in sorted(rows):
            row = sorted(rows[row_idx], key=lambda x: x.col)
            line = " ".join(c.text for c in row).strip()
            if not line:
                continue
            dm = _DATE_PAT.search(line)
            if not dm:
                # Skip rows without a date (likely header).
                continue
            date = _normalize_date(dm)
            desc = (line[:dm.start()] + line[dm.end():]).strip(" -・|｜　\t")
            if not desc:
                desc = line  # fallback
            tags = list(set(_JIRA_PAT.findall(desc)))
            entries.append(ChangeLogEntry(log_date=date, description=desc, tags=tags))
            chunks.append(ParsedChunk(
                text=f"{date} {desc}",
                markdown=f"- **{date}** — {desc}",
                metadata={
                    "kind": "change_log",
                    "log_date": date,
                    "jira_tags": tags,
                },
                hierarchy_path="変更履歴",
                order=order,
            ))
            order += 1

        return ParserOutput(chunks=chunks, change_logs=entries)
