"""Sheet parser registry.

Public API:
    get_parser(sheet_type) -> BaseSheetParser instance

The orchestrator (Task #6) uses `get_parser` to dispatch a sheet to its
parser after classification. To add a new parser, write a subclass of
`BaseSheetParser`, then register it in `_REGISTRY`.
"""

from __future__ import annotations

from ingestion.classifier import SheetType
from ingestion.parsers.base import (
    BaseSheetParser,
    ChangeLogEntry,
    DataDictRow,
    ExtractedImageInfo,
    ParsedChunk,
    ParserContext,
    ParserOutput,
)
from ingestion.parsers.cover import CoverParser
from ingestion.parsers.generic import GenericParser
from ingestion.parsers.history import HistoryParser
from ingestion.parsers.memo import MemoParser
from ingestion.parsers.screen import ScreenParser
from ingestion.parsers.table import TableParser


_REGISTRY: dict[SheetType, BaseSheetParser] = {
    SheetType.COVER:   CoverParser(),
    SheetType.HISTORY: HistoryParser(),
    SheetType.TABLE:   TableParser(),
    SheetType.MEMO:    MemoParser(),
    SheetType.SCREEN:  ScreenParser(),
    SheetType.INDEX:   TableParser(),    # treat index sheets as tables
    SheetType.UNKNOWN: GenericParser(),
}

_FALLBACK = GenericParser()


def get_parser(sheet_type: SheetType) -> BaseSheetParser:
    return _REGISTRY.get(sheet_type, _FALLBACK)


__all__ = [
    "BaseSheetParser",
    "ParserContext",
    "ParserOutput",
    "ParsedChunk",
    "ExtractedImageInfo",
    "ChangeLogEntry",
    "DataDictRow",
    "get_parser",
]
