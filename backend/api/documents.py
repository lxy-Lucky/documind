"""Document CRUD + upload + chunk-level inspection.

Upload goes through a background task; clients can subscribe to
/api/documents/{id}/progress (SSE) to follow ingestion in real time.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import tempfile
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sse_starlette.sse import EventSourceResponse

from config import settings
from db import get_db
from ingestion.orchestrator import ProgressEvent, ingest_file

router = APIRouter(prefix="/api/documents", tags=["documents"])


# ---------------------------------------------------------------------------
# Progress queues
#
# Keyed by int document_id once known, plus a "latest" string key that is
# bound the instant a new upload arrives (so /progress/stream never 404s
# in the brief window before the background task starts and obtains a
# document_id).
# ---------------------------------------------------------------------------

_QUEUES: dict[int | str, asyncio.Queue[ProgressEvent]] = {}


# ---------------------------------------------------------------------------
# Listing / fetch
# ---------------------------------------------------------------------------

@router.get("")
def list_documents(folder_id: int | None = None) -> list[dict]:
    sql = """
        SELECT d.id, d.folder_id, d.filename, d.file_size, d.status, d.enrich_status,
               d.uploaded_at, d.indexed_at, d.error_msg, d.doc_metadata,
               (SELECT COUNT(*) FROM chunk c WHERE c.document_id = d.id) AS chunk_count
        FROM document d
        WHERE 1=1
    """
    params: list = []
    if folder_id is not None:
        sql += " AND d.folder_id = ?"
        params.append(folder_id)
    sql += " ORDER BY d.uploaded_at DESC"
    with get_db() as conn:
        rows = conn.execute(sql, params).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["doc_metadata"] = json.loads(d.get("doc_metadata") or "{}")
        out.append(d)
    return out


@router.get("/{doc_id}")
def get_document(doc_id: int) -> dict:
    with get_db() as conn:
        doc = conn.execute("SELECT * FROM document WHERE id=?", (doc_id,)).fetchone()
        if not doc:
            raise HTTPException(404, "document not found")
        sheets = conn.execute(
            "SELECT id, name, sheet_index, sheet_type, classifier_confidence "
            "FROM sheet WHERE document_id=? ORDER BY sheet_index",
            (doc_id,),
        ).fetchall()
    d = dict(doc)
    d["doc_metadata"] = json.loads(d.get("doc_metadata") or "{}")
    d["sheets"] = [dict(s) for s in sheets]
    return d


@router.delete("/{doc_id}")
def delete_document(doc_id: int) -> dict:
    with get_db() as conn:
        # vec rows must be cleaned manually (vec0 doesn't support FK cascade)
        conn.execute(
            "DELETE FROM chunk_vec WHERE chunk_id IN "
            "(SELECT id FROM chunk WHERE document_id=?)",
            (doc_id,),
        )
        conn.execute(
            "DELETE FROM summary_vec WHERE summary_id IN "
            "(SELECT cs.id FROM chunk_summary cs JOIN chunk c ON c.id=cs.chunk_id "
            "WHERE c.document_id=?)",
            (doc_id,),
        )
        conn.execute(
            "DELETE FROM chunk_fts WHERE rowid IN "
            "(SELECT id FROM chunk WHERE document_id=?) "
            "OR rowid IN ("
            "  SELECT cs.id + 10000000 FROM chunk_summary cs "
            "  JOIN chunk c ON c.id=cs.chunk_id WHERE c.document_id=?)",
            (doc_id, doc_id),
        )
        conn.execute("DELETE FROM document WHERE id=?", (doc_id,))
        conn.commit()
    return {"deleted": doc_id}


# ---------------------------------------------------------------------------
# Chunk inspection
# ---------------------------------------------------------------------------

@router.get("/{doc_id}/chunks")
def list_chunks(doc_id: int, sheet_id: int | None = None) -> list[dict]:
    sql = """
        SELECT c.id, c.sheet_id, s.name AS sheet_name, c.hierarchy_path,
               c.text, c.markdown, c.chunk_metadata, c.chunk_order
        FROM chunk c
        JOIN sheet s ON s.id = c.sheet_id
        WHERE c.document_id = ?
    """
    params: list = [doc_id]
    if sheet_id is not None:
        sql += " AND c.sheet_id = ?"
        params.append(sheet_id)
    sql += " ORDER BY c.sheet_id, c.chunk_order"
    with get_db() as conn:
        rows = conn.execute(sql, params).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["chunk_metadata"] = json.loads(d.get("chunk_metadata") or "{}")
        out.append(d)
    return out


@router.get("/chunk/{chunk_id}")
def get_chunk(chunk_id: int) -> dict:
    with get_db() as conn:
        r = conn.execute(
            """
            SELECT c.*, s.name AS sheet_name, s.screenshot_path,
                   d.filename, d.doc_metadata
            FROM chunk c
            JOIN sheet s ON s.id=c.sheet_id
            JOIN document d ON d.id=c.document_id
            WHERE c.id=?
            """,
            (chunk_id,),
        ).fetchone()
        if not r:
            raise HTTPException(404, "chunk not found")
        summaries = conn.execute(
            "SELECT perspective, text FROM chunk_summary WHERE chunk_id=?",
            (chunk_id,),
        ).fetchall()
    d = dict(r)
    d["chunk_metadata"] = json.loads(d.get("chunk_metadata") or "{}")
    d["doc_metadata"] = json.loads(d.get("doc_metadata") or "{}")
    d["summaries"] = [dict(s) for s in summaries]
    return d


# ---------------------------------------------------------------------------
# Screenshot serving
# ---------------------------------------------------------------------------

@router.get("/sheet/{sheet_id}/screenshot")
def get_sheet_screenshot(sheet_id: int):
    with get_db() as conn:
        r = conn.execute("SELECT screenshot_path FROM sheet WHERE id=?", (sheet_id,)).fetchone()
    if not r or not r["screenshot_path"]:
        raise HTTPException(404, "no screenshot")
    p = Path(r["screenshot_path"])
    if not p.exists():
        raise HTTPException(404, "file missing on disk")
    return FileResponse(p, media_type="image/png")


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------

@router.post("/upload")
async def upload(
    background: BackgroundTasks,
    file: UploadFile = File(...),
    folder_id: int = Form(...),
) -> dict:
    """Save uploaded file, start ingestion in background, register the
    progress queue immediately under "latest" so /progress/stream can
    subscribe before the runner starts."""
    with get_db() as conn:
        f = conn.execute("SELECT id FROM folder WHERE id=?", (folder_id,)).fetchone()
        if not f:
            raise HTTPException(404, "folder not found")

    # Persist upload
    suffix = Path(file.filename or "x.xlsx").suffix or ".xlsx"
    tmp = tempfile.NamedTemporaryFile(suffix=suffix, dir=settings.upload_dir, delete=False)
    try:
        shutil.copyfileobj(file.file, tmp)
    finally:
        tmp.close()
    dest = Path(tmp.name)

    # Register queue *before* the background task runs.
    q: asyncio.Queue[ProgressEvent] = asyncio.Queue()
    _QUEUES["latest"] = q

    async def runner():
        try:
            doc_id = await ingest_file(folder_id, dest, file.filename or dest.name, progress=q)
            _QUEUES[doc_id] = q
        except Exception:
            # Orchestrator already emitted an 'error' event on the queue.
            pass

    background.add_task(runner)
    return {"status": "started", "filename": file.filename}


@router.get("/progress/stream")
async def progress_stream(document_id: int | None = None):
    """SSE stream of ingestion progress.

    With document_id: stream that document's queue (404 if absent).
    Without: stream the queue tied to the most recent /upload call.
    """
    if document_id is not None:
        q = _QUEUES.get(document_id)
    else:
        q = _QUEUES.get("latest")
    if q is None:
        raise HTTPException(404, "no active ingestion job")

    async def gen():
        while True:
            evt: ProgressEvent = await q.get()
            yield {"event": evt.stage, "data": json.dumps(evt.to_dict(), ensure_ascii=False)}
            if evt.stage in ("done", "error"):
                break

    return EventSourceResponse(gen())
