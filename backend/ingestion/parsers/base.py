"""Base classes for sheet parsers.

Every parser receives a `ParserContext` and returns a `ParserOutput`.
The orchestrator (Task #6) wires parsers into the upload pipeline; each
parser must be self-contained and idempotent.

Side-effects (data_dict rows, change_log rows, image records,
document-level metadata patches) are returned in `ParserOutput.extras`
rather than written directly, so the orchestrator can run everything
in a single transaction.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ingestion.excel_utils import SheetSnapshot


# ---------------------------------------------------------------------------
# Parser I/O
# ---------------------------------------------------------------------------

@dataclass
class ParsedChunk:
    """A single retrievable unit produced by a parser."""
    text: str                                    # plain text for BM25
    markdown: str                                # formatted for LLM
    metadata: dict[str, Any] = field(default_factory=dict)
    hierarchy_path: str = ""                     # e.g. '工事→予算反映 > ダイビル'
    order: int = 0                               # ordering within the sheet
    # Whether this chunk benefits from multi-perspective enrichment.
    # Structural chunks (table rows, change-log entries) are already
    # keyword-dense; enrichment adds little but costs an LLM call per chunk.
    enrich_eligible: bool = True


@dataclass
class ExtractedImageInfo:
    file_path: Path
    anchor_cell: str
    vl_description: str = ""
    annotations: dict[str, Any] = field(default_factory=dict)


@dataclass
class ChangeLogEntry:
    log_date: str
    description: str
    tags: list[str] = field(default_factory=list)


@dataclass
class DataDictRow:
    table_name: str = ""
    table_name_en: str = ""
    column_name: str = ""
    column_name_en: str = ""
    description: str = ""


@dataclass
class ParserContext:
    """Everything a parser may want during `parse()`."""
    snapshot: SheetSnapshot
    xlsx_path: Path
    screenshot_path: Path | None              # full-sheet PNG for VL parsers
    image_dir: Path                            # where to store extracted images
    # The cover parser fills this after running; later parsers (mostly
    # `MemoParser`) consult it to map font color → JIRA tag.
    color_to_jira: dict[str, str] = field(default_factory=dict)
    # JIRA / customer / module already extracted from the cover. May be
    # inherited as default tags on chunks.
    document_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ParserOutput:
    chunks: list[ParsedChunk] = field(default_factory=list)
    images: list[ExtractedImageInfo] = field(default_factory=list)
    change_logs: list[ChangeLogEntry] = field(default_factory=list)
    data_dict_rows: list[DataDictRow] = field(default_factory=list)
    # If a parser discovers document-level info (cover does), it can
    # contribute keys here. The orchestrator merges them into
    # document.doc_metadata.
    document_metadata_patch: dict[str, Any] = field(default_factory=dict)
    # If a parser discovers a color → JIRA map (cover does), it
    # contributes here.
    color_to_jira_patch: dict[str, str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------

class BaseSheetParser(ABC):
    """Subclasses implement `parse(ctx)` returning a ParserOutput."""

    name: str = "base"

    @abstractmethod
    async def parse(self, ctx: ParserContext) -> ParserOutput: ...
