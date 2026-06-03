"""Tabular sheet parser (data dictionaries, attribute lists, etc.).

We assume the classifier already accepted this sheet as table-shaped:
one header row near the top + N homogeneous body rows below. The
parser:

    1. Locates the header row (same heuristic as the classifier).
    2. Treats each non-empty body row as a record keyed by header text.
    3. If the headers look like a *data dictionary* (テーブル名 /
       カラム名 / カラム名_英語 / 更新内容 / ...), also produces
       `DataDictRow` entries so we can answer "what does column XYZ
       mean" by structured lookup.
    4. Emits one chunk per body row (small enough that the LLM can read
       several at once; large enough to carry the full record).
"""

from __future__ import annotations

import re

from ingestion.excel_utils import CellInfo, SheetSnapshot
from ingestion.parsers.base import (
    BaseSheetParser,
    DataDictRow,
    ParsedChunk,
    ParserContext,
    ParserOutput,
)


# Header → field-name mapping. Multilingual aliases.
_DICT_HEADER_HINTS = {
    "table_name":     re.compile(r"テーブル名$|テーブル$|table[_ ]?name$|表名$|表名称$", re.I),
    "table_name_en":  re.compile(r"テーブル名[_ ]?英語|table[_ ]?(name)?[_ ]?en", re.I),
    "column_name":    re.compile(r"カラム名$|列名$|フィールド名|column[_ ]?name$|项目名|属性名", re.I),
    "column_name_en": re.compile(r"カラム名[_ ]?英語|列名[_ ]?英語|column[_ ]?en|英語名", re.I),
    "description":   re.compile(r"説明|意味|内容|更新内容|備考|description|note|remark", re.I),
}


def _find_header_row(snap: SheetSnapshot) -> tuple[int, list[CellInfo]] | None:
    """Same logic as classifier._looks_like_table — duplicated here to
    avoid coupling, kept short."""
    rows: dict[int, list[CellInfo]] = {}
    for c in snap.cells:
        rows.setdefault(c.row, []).append(c)
    sorted_rows = sorted(rows.items())
    for row_idx, cells in sorted_rows[:10]:
        if len(cells) >= 3 and (any(c.bold for c in cells) or any(c.fill_color for c in cells)):
            return row_idx, sorted(cells, key=lambda x: x.col)
    for row_idx, cells in sorted_rows:
        if len(cells) >= 3:
            return row_idx, sorted(cells, key=lambda x: x.col)
    return None


def _map_headers_to_dict_fields(headers: list[CellInfo]) -> dict[int, str]:
    """Return {col_index: dict_field_name} for headers matching dict hints."""
    out: dict[int, str] = {}
    for h in headers:
        for field_name, pat in _DICT_HEADER_HINTS.items():
            if pat.search(h.text):
                out[h.col] = field_name
                break
    return out


class TableParser(BaseSheetParser):
    name = "table"

    async def parse(self, ctx: ParserContext) -> ParserOutput:
        snap = ctx.snapshot
        located = _find_header_row(snap)
        if not located:
            return ParserOutput(notes=["no header row found; producing no chunks"])

        header_idx, headers = located
        header_text: dict[int, str] = {h.col: h.text for h in headers}
        dict_field_map = _map_headers_to_dict_fields(headers)
        is_data_dict = len(dict_field_map) >= 2

        # Group body rows
        rows: dict[int, list[CellInfo]] = {}
        for c in snap.cells:
            if c.row > header_idx:
                rows.setdefault(c.row, []).append(c)

        chunks: list[ParsedChunk] = []
        dict_rows: list[DataDictRow] = []
        order = 0
        # Carry-forward for cells "inherited" from the row above when the
        # current row leaves them blank (common in 結合 / 合并表头模式).
        last_values: dict[int, str] = {}

        for row_idx in sorted(rows):
            cells = sorted(rows[row_idx], key=lambda x: x.col)
            record: dict[str, str] = {}
            row_values_by_col: dict[int, str] = {}
            for c in cells:
                row_values_by_col[c.col] = c.text
            for col, hdr in header_text.items():
                val = row_values_by_col.get(col, "")
                if not val and col in last_values:
                    val = last_values[col]
                if val:
                    last_values[col] = val
                record[hdr] = val

            # Skip rows whose all-non-header values are empty
            if not any(record.values()):
                continue

            # ---- Data-dict structured output ----
            if is_data_dict:
                dd = DataDictRow()
                for col, field_name in dict_field_map.items():
                    setattr(dd, field_name, row_values_by_col.get(col) or last_values.get(col, "") or "")
                if dd.column_name or dd.column_name_en:
                    dict_rows.append(dd)

            # ---- Chunk per row ----
            kv_lines = [f"- **{k}**: {v}" for k, v in record.items() if v]
            md = f"### {snap.name} — 行 {row_idx}\n" + "\n".join(kv_lines)
            text = " ; ".join(f"{k}: {v}" for k, v in record.items() if v)
            chunks.append(ParsedChunk(
                text=text,
                markdown=md,
                metadata={
                    "kind": "table_row",
                    "sheet": snap.name,
                    "row": row_idx,
                    "headers": list(header_text.values()),
                    "is_data_dict": is_data_dict,
                    "jira_tags": ctx.document_metadata.get("jira_codes", []),
                },
                hierarchy_path=snap.name,
                order=order,
            ))
            order += 1

        return ParserOutput(chunks=chunks, data_dict_rows=dict_rows)
