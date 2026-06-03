"""Folder CRUD."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from db import get_db

router = APIRouter(prefix="/api/folders", tags=["folders"])


class FolderIn(BaseModel):
    name: str
    color: str = "amber"


@router.get("")
def list_folders() -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT f.id, f.name, f.color, f.created_at,
                   (SELECT COUNT(*) FROM document d WHERE d.folder_id = f.id) AS doc_count,
                   (SELECT COUNT(*) FROM chunk c    WHERE c.folder_id = f.id) AS chunk_count
            FROM folder f
            ORDER BY f.created_at DESC
            """
        ).fetchall()
    return [dict(r) for r in rows]


@router.post("")
def create_folder(body: FolderIn) -> dict:
    if not body.name.strip():
        raise HTTPException(400, "name is required")
    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO folder(name, color) VALUES (?, ?)",
            (body.name.strip(), body.color),
        )
        conn.commit()
        fid = int(cur.lastrowid)
    return {"id": fid, "name": body.name, "color": body.color}


@router.patch("/{folder_id}")
def update_folder(folder_id: int, body: FolderIn) -> dict:
    with get_db() as conn:
        conn.execute(
            "UPDATE folder SET name=?, color=? WHERE id=?",
            (body.name.strip(), body.color, folder_id),
        )
        conn.commit()
    return {"id": folder_id, "name": body.name, "color": body.color}


@router.delete("/{folder_id}")
def delete_folder(folder_id: int) -> dict:
    with get_db() as conn:
        conn.execute("DELETE FROM folder WHERE id=?", (folder_id,))
        conn.commit()
    return {"deleted": folder_id}
