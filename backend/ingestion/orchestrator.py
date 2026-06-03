"""Upload pipeline orchestrator.

Top-level flow for one uploaded Excel:

    1. Insert `document` row (status=pending)
    2. Open workbook → read every sheet snapshot
    3. (Optional) Render screenshots for all sheets
    4. Classify each sheet
    5. Pass 1 — run cover parsers first to obtain doc-level metadata +
       color→JIRA map
    6. Pass 2 — run remaining parsers with full ParserContext
    7. Persist sheet / chunk / image / change_log / data_dict rows
    8. Enrich chunks with multi-perspective summaries (optional)
    9. Embed chunk text + each summary, write to sqlite-vec
   10. Insert into FTS5 with Lindera-tokenized text
   11. Update document.status='ready'

Progress events are pushed through an asyncio.Queue so an SSE endpoint
can stream them to the client.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from loguru import logger

from config import settings
from db import get_db
from ingestion.chunk_enricher import enrich_chunks
from ingestion.classifier import SheetType, classify_sheet
from ingestion.excel_utils import open_workbook, read_sheet
from ingestion.parsers import (
    ParsedChunk,
    ParserContext,
    ParserOutput,
    get_parser,
)
from ingestion.screenshot import render_single_sheet
from ingestion.tokenizer import tokenize_for_fts
from llm.embed_client import embed_texts_async, vector_to_blob


# ---------------------------------------------------------------------------
# Progress events
# ---------------------------------------------------------------------------

@dataclass
class ProgressEvent:
    stage: str               # e.g. 'reading' / 'classifying' / 'parsing' / 'enriching' / 'embedding' / 'done' / 'error'
    message: str
    current: int = 0
    total: int = 0
    extra: dict[str, Any] | None = None

    def to_dict(self) -> dict:
        d = asdict(self)
        return d


ProgressCallback = "asyncio.Queue[ProgressEvent] | None"


async def _emit(q, evt: ProgressEvent) -> None:
    if q is not None:
        await q.put(evt)
    logger.info(f"[{evt.stage}] {evt.message} ({evt.current}/{evt.total})")


# ---------------------------------------------------------------------------
# DB write helpers (sync — sqlite is fast enough)
# ---------------------------------------------------------------------------

def _insert_document(folder_id: int, filename: str, file_path: str, size: int) -> int:
    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO document(folder_id, filename, file_path, file_size, status) "
            "VALUES (?, ?, ?, ?, 'parsing')",
            (folder_id, filename, file_path, size),
        )
        conn.commit()
        return int(cur.lastrowid)


def _set_doc_status(doc_id: int, status: str, error: str | None = None) -> None:
    with get_db() as conn:
        if status == "ready":
            conn.execute(
                "UPDATE document SET status=?, indexed_at=datetime('now') WHERE id=?",
                (status, doc_id),
            )
        else:
            conn.execute(
                "UPDATE document SET status=?, error_msg=? WHERE id=?",
                (status, error, doc_id),
            )
        conn.commit()


def _update_doc_metadata(doc_id: int, meta: dict) -> None:
    with get_db() as conn:
        conn.execute(
            "UPDATE document SET doc_metadata=? WHERE id=?",
            (json.dumps(meta, ensure_ascii=False), doc_id),
        )
        conn.commit()


def _insert_sheet(doc_id: int, name: str, idx: int, stype: str, conf: float,
                  screenshot: str | None, raw_text: str) -> int:
    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO sheet(document_id, name, sheet_index, sheet_type, "
            "classifier_confidence, screenshot_path, raw_text) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (doc_id, name, idx, stype, conf, screenshot, raw_text),
        )
        conn.commit()
        return int(cur.lastrowid)


def _insert_chunks(sheet_id: int, doc_id: int, folder_id: int,
                   chunks: list[ParsedChunk]) -> list[int]:
    ids: list[int] = []
    with get_db() as conn:
        for c in chunks:
            cur = conn.execute(
                "INSERT INTO chunk(sheet_id, document_id, folder_id, text, markdown, "
                "chunk_metadata, hierarchy_path, chunk_order) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (sheet_id, doc_id, folder_id, c.text, c.markdown,
                 json.dumps(c.metadata, ensure_ascii=False),
                 c.hierarchy_path, c.order),
            )
            ids.append(int(cur.lastrowid))
        conn.commit()
    return ids


def _insert_images(sheet_id: int, out: ParserOutput) -> None:
    if not out.images:
        return
    with get_db() as conn:
        for img in out.images:
            conn.execute(
                "INSERT INTO image(sheet_id, file_path, anchor_cell, vl_description, related_annotations) "
                "VALUES (?, ?, ?, ?, ?)",
                (sheet_id, str(img.file_path), img.anchor_cell,
                 img.vl_description,
                 json.dumps(img.annotations, ensure_ascii=False)),
            )
        conn.commit()


def _insert_change_logs(doc_id: int, out: ParserOutput) -> None:
    if not out.change_logs:
        return
    with get_db() as conn:
        for e in out.change_logs:
            conn.execute(
                "INSERT INTO change_log(document_id, log_date, description, tags) "
                "VALUES (?, ?, ?, ?)",
                (doc_id, e.log_date, e.description,
                 json.dumps(e.tags, ensure_ascii=False)),
            )
        conn.commit()


def _insert_data_dict(doc_id: int, sheet_id: int, out: ParserOutput) -> None:
    if not out.data_dict_rows:
        return
    with get_db() as conn:
        for r in out.data_dict_rows:
            conn.execute(
                "INSERT INTO data_dict(document_id, sheet_id, table_name, table_name_en, "
                "column_name, column_name_en, description) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (doc_id, sheet_id, r.table_name, r.table_name_en,
                 r.column_name, r.column_name_en, r.description),
            )
        conn.commit()


def _insert_summaries(chunk_ids: list[int], summaries: list) -> list[tuple[int, str, str]]:
    """Insert chunk_summary rows; return [(summary_id, perspective, text)] for embedding."""
    out: list[tuple[int, str, str]] = []
    if not summaries:
        return out
    with get_db() as conn:
        for cid, summ in zip(chunk_ids, summaries):
            if summ is None:
                continue
            for persp in ("technical", "business", "keywords"):
                t = getattr(summ, persp, "")
                if not t:
                    continue
                cur = conn.execute(
                    "INSERT INTO chunk_summary(chunk_id, perspective, text) VALUES (?, ?, ?)",
                    (cid, persp, t),
                )
                out.append((int(cur.lastrowid), persp, t))
        conn.commit()
    return out


def _write_embeddings(chunk_ids: list[int], chunk_vecs: list[list[float]],
                      summary_rows: list[tuple[int, str, str]],
                      summary_vecs: list[list[float]]) -> None:
    with get_db() as conn:
        for cid, vec in zip(chunk_ids, chunk_vecs):
            conn.execute(
                "INSERT OR REPLACE INTO chunk_vec(chunk_id, embedding) VALUES (?, ?)",
                (cid, vector_to_blob(vec)),
            )
        for (sid, _, _), vec in zip(summary_rows, summary_vecs):
            conn.execute(
                "INSERT OR REPLACE INTO summary_vec(summary_id, embedding) VALUES (?, ?)",
                (sid, vector_to_blob(vec)),
            )
        conn.commit()


def _write_fts(chunk_ids: list[int], chunk_texts: list[str],
               summary_rows: list[tuple[int, str, str]]) -> None:
    """Insert into chunk_fts. We index both chunk text and each summary
    in the same FTS table but use a rowid offset for summaries (negative
    is not allowed, so we encode: rowid = chunk_id for chunks, and
    summary_id + 10_000_000 for summaries — collision-free given our
    scale)."""
    with get_db() as conn:
        for cid, text in zip(chunk_ids, chunk_texts):
            conn.execute(
                "INSERT INTO chunk_fts(rowid, fts_text) VALUES (?, ?)",
                (cid, tokenize_for_fts(text)),
            )
        for sid, _, text in summary_rows:
            conn.execute(
                "INSERT INTO chunk_fts(rowid, fts_text) VALUES (?, ?)",
                (sid + 10_000_000, tokenize_for_fts(text)),
            )
        conn.commit()


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

async def ingest_file(
    folder_id: int,
    file_path: Path,
    filename: str,
    progress: "asyncio.Queue[ProgressEvent] | None" = None,
) -> int:
    """Ingest one Excel. Returns document_id.

    Raises on unrecoverable errors after marking the document failed.
    """
    size = file_path.stat().st_size
    doc_id = _insert_document(folder_id, filename, str(file_path), size)
    try:
        await _emit(progress, ProgressEvent("started", f"document_id={doc_id}",
                                            0, 1, extra={"document_id": doc_id}))
        await _emit(progress, ProgressEvent("reading", f"opening {filename}", 0, 1))
        wb = open_workbook(file_path)
        snaps = [(idx, ws.title, read_sheet(ws, idx)) for idx, ws in enumerate(wb.worksheets)]
        wb.close()
        total_sheets = len(snaps)

        # ---- screenshots ----
        screenshots: dict[str, Path | None] = {}
        for i, (_, name, _) in enumerate(snaps):
            try:
                shot = render_single_sheet(file_path, name, settings.screenshot_dir)
                screenshots[name] = shot.file_path if shot else None
            except Exception as e:
                logger.warning(f"Screenshot for '{name}' failed: {e}")
                screenshots[name] = None
            await _emit(progress, ProgressEvent("screenshot",
                                                f"rendered {name}",
                                                i + 1, total_sheets))

        # ---- classification ----
        classifications = []
        for i, (idx, name, snap) in enumerate(snaps):
            r = await classify_sheet(snap, screenshot_path=screenshots[name])
            classifications.append((idx, name, snap, r))
            await _emit(progress, ProgressEvent("classifying",
                                                f"{name} → {r.sheet_type.value} ({r.confidence:.2f})",
                                                i + 1, total_sheets))

        # ---- Pass 1: cover ----
        doc_meta: dict = {}
        color_map: dict[str, str] = {}
        cover_outputs: dict[str, ParserOutput] = {}
        for idx, name, snap, r in classifications:
            if r.sheet_type != SheetType.COVER:
                continue
            ctx = ParserContext(
                snapshot=snap, xlsx_path=file_path,
                screenshot_path=screenshots[name],
                image_dir=settings.image_dir,
            )
            out = await get_parser(r.sheet_type).parse(ctx)
            cover_outputs[name] = out
            doc_meta.update(out.document_metadata_patch)
            color_map.update(out.color_to_jira_patch)
        if doc_meta:
            _update_doc_metadata(doc_id, doc_meta)

        # ---- Pass 2: everyone ----
        all_chunk_ids: list[int] = []
        all_chunk_texts: list[str] = []

        for i, (idx, name, snap, r) in enumerate(classifications):
            await _emit(progress, ProgressEvent("parsing",
                                                f"{name} ({r.sheet_type.value})",
                                                i + 1, total_sheets))
            sheet_id = _insert_sheet(
                doc_id, name, idx, r.sheet_type.value, r.confidence,
                str(screenshots[name]) if screenshots[name] else None,
                _raw_text_of(snap),
            )

            if r.sheet_type == SheetType.COVER and name in cover_outputs:
                out = cover_outputs[name]
            else:
                ctx = ParserContext(
                    snapshot=snap, xlsx_path=file_path,
                    screenshot_path=screenshots[name],
                    image_dir=settings.image_dir,
                    color_to_jira=color_map,
                    document_metadata=doc_meta,
                )
                try:
                    out = await get_parser(r.sheet_type).parse(ctx)
                except Exception as e:
                    logger.exception(f"Parser failed for sheet {name}: {e}")
                    out = ParserOutput(notes=[f"parser-error: {e}"])

            chunk_ids = _insert_chunks(sheet_id, doc_id, folder_id, out.chunks)
            _insert_images(sheet_id, out)
            _insert_change_logs(doc_id, out)
            _insert_data_dict(doc_id, sheet_id, out)
            all_chunk_ids.extend(chunk_ids)
            all_chunk_texts.extend(c.text for c in out.chunks)

        if not all_chunk_ids:
            await _emit(progress, ProgressEvent("done",
                                                "no chunks produced — document indexed empty",
                                                1, 1))
            _set_doc_status(doc_id, "ready")
            return doc_id

        # ---- enrichment ----
        await _emit(progress, ProgressEvent("enriching",
                                            f"generating multi-perspective summaries",
                                            0, len(all_chunk_ids)))
        summaries = await enrich_chunks(all_chunk_texts)
        summary_rows = _insert_summaries(all_chunk_ids, summaries)

        # ---- embedding ----
        await _emit(progress, ProgressEvent("embedding",
                                            "encoding chunks", 0, len(all_chunk_ids)))
        chunk_vecs = await embed_texts_async(all_chunk_texts)
        summary_texts = [r[2] for r in summary_rows]
        summary_vecs = await embed_texts_async(summary_texts) if summary_texts else []
        _write_embeddings(all_chunk_ids, chunk_vecs, summary_rows, summary_vecs)

        # ---- FTS ----
        await _emit(progress, ProgressEvent("indexing", "writing FTS", 0, 1))
        _write_fts(all_chunk_ids, all_chunk_texts, summary_rows)

        _set_doc_status(doc_id, "ready")
        await _emit(progress, ProgressEvent("done",
                                            f"ingested {len(all_chunk_ids)} chunks",
                                            1, 1, extra={"document_id": doc_id}))
        return doc_id

    except Exception as e:
        logger.exception(f"Ingestion failed: {e}")
        _set_doc_status(doc_id, "failed", error=str(e))
        await _emit(progress, ProgressEvent("error", str(e), 0, 0))
        raise


def _raw_text_of(snap) -> str:
    rows: dict[int, list] = {}
    for c in snap.cells:
        rows.setdefault(c.row, []).append(c)
    out = []
    for row_idx in sorted(rows):
        cells = sorted(rows[row_idx], key=lambda x: x.col)
        out.append("\t".join(c.text for c in cells))
    return "\n".join(out)
