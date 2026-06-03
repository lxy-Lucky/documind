"""Thin wrapper around the Ollama HTTP API.

Two entry points:
    chat(messages, ...)            -> blocking response
    chat_stream(messages, ...)     -> async generator of text deltas

VL calls use the same client with images attached.
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import AsyncIterator, Iterable

import httpx
from loguru import logger

from config import settings


def _encode_image(path: str | Path) -> str:
    return base64.b64encode(Path(path).read_bytes()).decode("ascii")


class OllamaClient:
    def __init__(self, host: str | None = None, timeout: int | None = None) -> None:
        self.host = (host or settings.ollama_host).rstrip("/")
        self.timeout = timeout or settings.ollama_timeout

    # ---------- text chat ----------

    async def chat(
        self,
        messages: list[dict],
        model: str | None = None,
        options: dict | None = None,
    ) -> str:
        model = model or settings.ollama_model_qa
        payload = {
            "model": model,
            "messages": messages,
            "stream": False,
            "options": options or {},
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            r = await client.post(f"{self.host}/api/chat", json=payload)
            r.raise_for_status()
            return r.json()["message"]["content"]

    async def chat_stream(
        self,
        messages: list[dict],
        model: str | None = None,
        options: dict | None = None,
    ) -> AsyncIterator[str]:
        model = model or settings.ollama_model_qa
        payload = {
            "model": model,
            "messages": messages,
            "stream": True,
            "options": options or {},
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            async with client.stream("POST", f"{self.host}/api/chat", json=payload) as r:
                r.raise_for_status()
                async for line in r.aiter_lines():
                    if not line:
                        continue
                    import json as _json
                    obj = _json.loads(line)
                    if obj.get("done"):
                        break
                    chunk = obj.get("message", {}).get("content", "")
                    if chunk:
                        yield chunk

    # ---------- vision ----------

    async def vision(
        self,
        prompt: str,
        images: Iterable[str | Path],
        model: str | None = None,
        options: dict | None = None,
    ) -> str:
        model = model or settings.ollama_model_vl
        payload = {
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": prompt,
                    "images": [_encode_image(p) for p in images],
                }
            ],
            "stream": False,
            "options": options or {},
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            r = await client.post(f"{self.host}/api/chat", json=payload)
            r.raise_for_status()
            return r.json()["message"]["content"]

    # ---------- introspection ----------

    async def list_models(self) -> list[str]:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"{self.host}/api/tags")
            r.raise_for_status()
            return [m["name"] for m in r.json().get("models", [])]

    async def ping(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                r = await client.get(f"{self.host}/api/tags")
                return r.status_code == 200
        except Exception as e:
            logger.warning(f"Ollama ping failed: {e}")
            return False


ollama = OllamaClient()
