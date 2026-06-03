"""DocuMind backend entrypoint.

Run locally:
    cd backend
    cp .env.example .env       # then edit if needed
    pip install -r requirements.txt
    uvicorn main:app --reload --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import sys

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

from api import debug, health
from config import settings
from db import init_db


def create_app() -> FastAPI:
    logger.remove()
    logger.add(sys.stderr, level=settings.log_level)

    app = FastAPI(title="DocuMind", version="0.1.0")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health.router)
    app.include_router(debug.router)

    @app.on_event("startup")
    async def _startup() -> None:
        init_db()
        logger.info(f"DocuMind backend listening on {settings.host}:{settings.port}")

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host=settings.host, port=settings.port, reload=False)
