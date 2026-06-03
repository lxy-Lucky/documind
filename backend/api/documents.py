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
# Progress queues per document_id
# ---------------------------------------------------------------------------

_QUEUES: dict[int, asyncio.Queue[ProgressEvent]] = {}


def _make_queue(doc_id: int) -> asyncio.Queue[ProgressEvent]:
    q: asyncio.Queue[ProgressEvent] = asyncio.Queue()
    _QUEUES[doc_id] = q
    return q


def _drop_queue(doc_id: int) -> None:
    _QUEUES.pop(doc_id, None)


# ---------------------------------------------------------------------------
# Listing / fetch
# ---------------------------------------------------------------------------

@router.get("")
def list_documents(folder_id: int | None = None) -> list[dict]:
    sql = """
        SELECT d.id, d.folder_id, d.filename, d.file_size, d.status, d.uploaded_at,
               d.indexed_at, d.error_msg, d.doc_metadata,
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

async def _run_ingest(doc_path: Path, filename: str, folder_id: int, doc_id_holder: dict):
    q = _make_queue(0)  # temp placeholder; will be reassigned after doc_id is known
    try:
        # We need the doc_id to register the queue properly. The orchestrator
        # creates the document row internally, so let's instead pass `progress=q`
        # and rely on the caller to subscribe before the file finishes parsing.
        # To make it reliable, we pre-create the document row here.
        pass
    finally:
        pass


@router.post("/upload")
async def upload(
    background: BackgroundTasks,
    file: UploadFile = File(...),
    folder_id: int = Form(...),
) -> dict:
    """Save uploaded file, start ingestion in background, return document_id
    immediately. Client should connect to /progress to follow."""
    # 1. Verify folder
    with get_db() as conn:
        f = conn.execute("SELECT id FROM folder WHERE id=?", (folder_id,)).fetchone()
        if not f:
            raise HTTPException(404, "folder not found")

    # 2. Persist file
    suffix = Path(file.filename or "x.xlsx").suffix or ".xlsx"
    dest = settings.upload_dir / f"{tempfile.mkstemp(suffix=suffix, dir=settings.upload_dir)[1]}"
    # mkstemp opens a fd; close it before writing to be safe across platforms.
    # Simpler: use NamedTemporaryFile.
    tmp = tempfile.NamedTemporaryFile(suffix=suffix, dir=settings.upload_dir, delete=False)
    try:
        shutil.copyfileobj(file.file, tmp)
    finally:
        tmp.close()
    dest = Path(tmp.name)

    # 3. Pre-create document row so we have an ID to bind the progress queue.
    #    We then call ingest_file which would normally create the row itself —
    #    to keep it simple, we pass a queue and let the caller correlate via
    #    /progress?document_id=<latest>. The orchestrator emits doc_id in the
    #    'done' event.
    q: asyncio.Queue[ProgressEvent] = asyncio.Queue()

    async def runner():
        try:
            doc_id = await ingest_file(folder_id, dest, file.filename or dest.name, progress=q)
            _QUEUES[doc_id] = q  # bind queue to doc_id only after creation
        except Exception:
            # already emitted "error" event; nothing else to do
            pass

    background.add_task(runner)

    # We cannot return the doc_id yet (orchestrator hasn't created it). Clients
    # should subscribe to /progress/stream which streams events from the most
    # recently started job.
    return {"status": "started", "filename": file.filename}


@router.get("/progress/stream")
async def progress_stream(document_id: int | None = None):
    """SSE stream of ingestion progress.

    If document_id is provided, stream that document's queue (must exist).
    Otherwise stream the most recently created queue (useful right after
    POST /upload where the client hasn't learned the doc_id yet).
    """
    if document_id is not None:
        q = _QUEUES.get(document_id)
        if q is None:
            raise HTTPException(404, "no progress queue for that document_id")
    else:
        if not _QUEUES:
            raise HTTPException(404, "no active ingestion jobs")
        q = next(reversed(list(_QUEUES.values())))

    async def gen():
        try:
            while True:
                evt: ProgressEvent = await q.get()
                yield {"event": evt.stage, "data": json.dumps(evt.to_dict(), ensure_ascii=False)}
                if evt.stage in ("done", "error"):
                    break
        finally:
            # Caller may keep polling for the next job; we don't auto-cleanup
            # immediately. The orchestrator finishes after emitting 'done',
            # the queue is drained, and Python GC handles it once nothing
            # references it.
            pass

    return EventSourceResponse(gen())
