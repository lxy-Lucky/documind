"""Cover-sheet parser.

The cover sheet (表紙) carries document-level metadata: JIRA codes
(DBL-10, KAIZEN-2635, ...), customer name (ダイビル), module name,
document type (詳細設計書) and date. Often the title text lives in a
shape / text box rather than in a cell, which means openpyxl returns
almost no data. We compensate by asking the VL model to read the
sheet screenshot.

We also try to detect a "凡例" / "参考JIRA" color legend if present —
the cover sometimes lists "DBL-10 ... blue / DBL-179 ... red" which
becomes our color_to_jira map for the rest of the document.

Output:
    - 0 chunks (cover info is not retrievable content, it's metadata)
    - document_metadata_patch: structured fields
    - color_to_jira_patch: e.g. {"#FF0000": "DBL-179"}
"""

from __future__ import annotations

import json
import re

from loguru import logger

from ingestion.parsers.base import (
    BaseSheetParser,
    ParserContext,
    ParserOutput,
)
from llm.ollama_client import ollama


_JIRA_PAT = re.compile(r"\b([A-Z]{2,}[A-Z0-9]*-\d+)\b")
_DATE_PAT = re.compile(r"(\d{4})[年/-](\d{1,2})[月/-](\d{1,2})")

_VL_PROMPT = """这是一份 Excel 设计文档的封面截图。请从中提取以下结构化元数据。如果某项不存在，用 null。
只输出 JSON，不要附加任何其他文字。

{
  "title": "文档主标题（如 詳細設計書/詳細設計書 等）",
  "module": "模块/功能名（如 予算実績）",
  "customer": "客户/品牌（如 ダイビル / @property 等）",
  "vendor": "制作公司（如 株式会社パラダイム・システムズ）",
  "jira_codes": ["JIRA 编号列表，如 DBL-10、KAIZEN-2635"],
  "version": "版本号或日期（如 2021/12/22）",
  "color_legend": [
    {"color_hex": "#FF0000", "label": "DBL-179", "meaning": "ダイビルカスタマイズ"}
  ]
}

color_legend 仅当截图中存在颜色 → JIRA 编号的图例（凡例 / 参考JIRA）时才填，否则空数组。"""


def _extract_from_cells(ctx: ParserContext) -> dict:
    """Pull whatever we can from raw cells without VL."""
    out: dict = {"jira_codes": [], "version": None, "raw_lines": []}
    for c in ctx.snapshot.cells:
        out["raw_lines"].append(c.text)
        for m in _JIRA_PAT.finditer(c.text):
            code = m.group(1)
            if code not in out["jira_codes"]:
                out["jira_codes"].append(code)
        dm = _DATE_PAT.search(c.text)
        if dm and not out["version"]:
            out["version"] = f"{dm.group(1)}/{int(dm.group(2)):02d}/{int(dm.group(3)):02d}"
    return out


async def _vl_extract(ctx: ParserContext) -> dict:
    if ctx.screenshot_path is None:
        return {}
    try:
        raw = await ollama.vision(_VL_PROMPT, [ctx.screenshot_path])
        txt = raw.strip()
        if txt.startswith("```"):
            txt = re.sub(r"^```[a-zA-Z]*", "", txt).rstrip("`").strip()
        return json.loads(txt)
    except Exception as e:
        logger.warning(f"Cover VL extraction failed: {e}")
        return {}


def _merge(cells_data: dict, vl_data: dict) -> tuple[dict, dict[str, str]]:
    """Combine cell-extracted and VL-extracted metadata.

    Returns (document_metadata, color_to_jira).
    """
    doc: dict = {}
    for k in ("title", "module", "customer", "vendor"):
        v = vl_data.get(k)
        if v:
            doc[k] = v

    jiras = set(cells_data.get("jira_codes", [])) | set(vl_data.get("jira_codes") or [])
    if jiras:
        doc["jira_codes"] = sorted(jiras)

    version = cells_data.get("version") or vl_data.get("version")
    if version:
        doc["version"] = version

    color_map: dict[str, str] = {}
    legend = vl_data.get("color_legend") if isinstance(vl_data, dict) else None
    if isinstance(legend, list):
        for entry in legend:
            if not isinstance(entry, dict):
                continue
            c = entry.get("color_hex")
            lbl = entry.get("label")
            if c and lbl:
                color_map[str(c).upper()] = str(lbl)

    return doc, color_map


class CoverParser(BaseSheetParser):
    name = "cover"

    async def parse(self, ctx: ParserContext) -> ParserOutput:
        cells_data = _extract_from_cells(ctx)
        vl_data = await _vl_extract(ctx)
        doc, color_map = _merge(cells_data, vl_data)
        notes: list[str] = []
        if not doc and not color_map:
            notes.append("cover parsed but no metadata extracted")
        return ParserOutput(
            chunks=[],
            document_metadata_patch=doc,
            color_to_jira_patch=color_map,
            notes=notes,
        )
