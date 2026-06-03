"""Debug endpoints for inspecting the parsing toolchain without
running the full ingestion pipeline.

Useful for sanity-checking color extraction, indent inference, image
extraction and screenshot generation against real sample files.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse

from config import settings
from ingestion.classifier import classify_sheet
from ingestion.excel_utils import dump_markdown, dump_text, open_workbook, read_sheet
from ingestion.image_extract import extract_sheet_images
from ingestion.screenshot import render_single_sheet

router = APIRouter(prefix="/api/debug", tags=["debug"])


def _save_upload(upload: UploadFile) -> Path:
    suffix = Path(upload.filename or "x.xlsx").suffix or ".xlsx"
    tmp = Path(tempfile.mkstemp(suffix=suffix, dir=settings.upload_dir)[1])
    tmp.write_bytes(upload.file.read())
    return tmp


@router.post("/inspect")
async def inspect(
    file: UploadFile = File(...),
    sheet: str | None = Query(None, description="optional sheet name; otherwise all"),
    include_cells: bool = Query(False, description="include full cell list (large)"),
) -> dict:
    """Parse an uploaded xlsx and return a structural summary per sheet."""
    path = _save_upload(file)
    try:
        wb = open_workbook(path)
        sheets_out = []
        for idx, ws in enumerate(wb.worksheets):
            if sheet and ws.title != sheet:
                continue
            snap = read_sheet(ws, idx)
            entry: dict = {
                "name": snap.name,
                "index": snap.index,
                "max_row": snap.max_row,
                "max_col": snap.max_col,
                "cell_count": len(snap.cells),
                "merged_ranges": snap.merged_ranges[:50],
                "has_images": snap.has_images,
                "colors_seen": sorted({c.font_color for c in snap.cells if c.font_color}),
                "fills_seen": sorted({c.fill_color for c in snap.cells if c.fill_color}),
                "indent_levels": sorted({c.indent_level for c in snap.cells}),
                "preview_markdown": dump_markdown(snap, max_rows=40),
            }
            if include_cells:
                entry["cells"] = [
                    {
                        "row": c.row, "col": c.col_letter, "text": c.text,
                        "fg": c.font_color, "bg": c.fill_color,
                        "bold": c.bold, "indent": c.indent_level,
                        "merged": c.merged_range,
                    }
                    for c in snap.cells
                ]
            sheets_out.append(entry)
        wb.close()
        return {"file": file.filename, "sheets": sheets_out}
    finally:
        path.unlink(missing_ok=True)


@router.post("/inspect/images")
async def inspect_images(file: UploadFile = File(...)) -> dict:
    """List images embedded in each sheet."""
    path = _save_upload(file)
    try:
        wb = open_workbook(path)
        out = []
        for ws in wb.worksheets:
            imgs = extract_sheet_images(ws, settings.image_dir)
            out.append({
                "sheet": ws.title,
                "images": [
                    {
                        "path": str(i.file_path),
                        "anchor": i.anchor_cell,
                        "w": i.width,
                        "h": i.height,
                    }
                    for i in imgs
                ],
            })
        wb.close()
        return {"file": file.filename, "sheets": out}
    finally:
        path.unlink(missing_ok=True)


@router.post("/inspect/screenshot")
async def inspect_screenshot(
    file: UploadFile = File(...),
    sheet: str = Query(..., description="sheet name to screenshot"),
):
    """Render a single sheet to PNG and return the image."""
    path = _save_upload(file)
    try:
        shot = render_single_sheet(path, sheet, settings.screenshot_dir)
        if not shot:
            raise HTTPException(404, f"Sheet '{sheet}' not found or render failed")
        return FileResponse(shot.file_path, media_type="image/png")
    finally:
        path.unlink(missing_ok=True)


@router.post("/inspect/classify")
async def inspect_classify(
    file: UploadFile = File(...),
    use_vl: bool = Query(False, description="render screenshot + run VL fallback on each sheet"),
) -> dict:
    """Classify every sheet in the uploaded workbook.

    With use_vl=true, sheets that fall through the rule stack also get a
    VL pass — slower (one extra Ollama call per ambiguous sheet) but
    catches edge cases.
    """
    path = _save_upload(file)
    try:
        wb = open_workbook(path)
        results = []
        for idx, ws in enumerate(wb.worksheets):
            snap = read_sheet(ws, idx)
            shot = None
            if use_vl:
                shot_obj = render_single_sheet(path, ws.title, settings.screenshot_dir)
                shot = shot_obj.file_path if shot_obj else None
            r = await classify_sheet(snap, screenshot_path=shot)
            results.append({
                "sheet": snap.name,
                "type": r.sheet_type.value,
                "confidence": round(r.confidence, 3),
                "used_vl": r.used_vl,
                "reasons": r.reasons,
            })
        wb.close()
        return {"file": file.filename, "classifications": results}
    finally:
        path.unlink(missing_ok=True)


@router.post("/inspect/text")
async def inspect_text(
    file: UploadFile = File(...),
    sheet: str = Query(...),
) -> dict:
    """Return the plain-text dump of one sheet (for FTS / LLM sanity checks)."""
    path = _save_upload(file)
    try:
        wb = open_workbook(path)
        if sheet not in wb.sheetnames:
            raise HTTPException(404, f"Sheet '{sheet}' not found")
        snap = read_sheet(wb[sheet], wb.sheetnames.index(sheet))
        wb.close()
        return {"sheet": sheet, "text": dump_text(snap)}
    finally:
        path.unlink(missing_ok=True)
