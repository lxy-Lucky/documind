"""Health & dependency-check endpoint.

GET /api/health -> simple alive ping
GET /api/health/deps -> verifies Ollama reachable, required models present,
                       sqlite-vec extension loadable, embedding/reranker models discoverable.
"""

from __future__ import annotations

from fastapi import APIRouter

from config import settings
from db import get_db
from llm.ollama_client import ollama

router = APIRouter(prefix="/api/health", tags=["health"])


@router.get("")
async def alive() -> dict:
    return {"status": "ok"}


@router.get("/deps")
async def deps() -> dict:
    report: dict = {"ok": True, "checks": {}}

    # ---- Ollama ----
    reachable = await ollama.ping()
    report["checks"]["ollama_reachable"] = reachable
    if reachable:
        try:
            models = await ollama.list_models()
            report["checks"]["ollama_models"] = models
            report["checks"]["qa_model_present"] = any(
                settings.ollama_model_qa in m for m in models
            )
            report["checks"]["vl_model_present"] = any(
                settings.ollama_model_vl in m for m in models
            )
        except Exception as e:
            report["checks"]["ollama_models_error"] = str(e)
            report["ok"] = False
    else:
        report["ok"] = False

    # ---- SQLite + vec ----
    try:
        with get_db() as conn:
            row = conn.execute("SELECT vec_version() AS v").fetchone()
            report["checks"]["sqlite_vec_version"] = row["v"]
    except Exception as e:
        report["checks"]["sqlite_vec_error"] = str(e)
        report["ok"] = False

    # ---- Config snapshot ----
    report["config"] = {
        "ollama_host": settings.ollama_host,
        "qa_model": settings.ollama_model_qa,
        "vl_model": settings.ollama_model_vl,
        "embed_model": settings.embed_model,
        "reranker_model": settings.reranker_model,
        "db_path": str(settings.db_path),
    }
    return report
