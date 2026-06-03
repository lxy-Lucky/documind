"""Chat endpoint with SSE streaming.

POST /api/chat (request body JSON):
    {
        "question": "...",
        "folder_id": <int|null>,
        "document_id": <int|null>,
        "session_id": "...",
        "top_k": <int|null>
    }

Returns Server-Sent Events:
    event: hits      data: <json array of citation metadata>
    event: token     data: <text delta>
    event: confidence data: <int 0..10>
    event: done      data: {"message_id": <int>}
    event: error     data: <string>
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from db import get_db
from llm.ollama_client import ollama
from retrieval.hybrid_search import hybrid_search

router = APIRouter(prefix="/api/chat", tags=["chat"])


_QA_TEMPLATE = (
    Path(__file__).parent.parent / "llm" / "prompts" / "qa.txt"
).read_text(encoding="utf-8")


class ChatRequest(BaseModel):
    question: str
    folder_id: int | None = None
    document_id: int | None = None
    session_id: str | None = None
    top_k: int | None = None


def _build_context(hits) -> tuple[str, list[dict]]:
    """Build the prompt's CONTEXT block and a parallel citation array."""
    parts: list[str] = []
    cites: list[dict] = []
    for i, h in enumerate(hits, start=1):
        header = (
            f"[#{i}] 文件: {h.filename} | 工作表: {h.sheet_name} ({h.sheet_type}) | "
            f"层级: {h.hierarchy_path or '-'}"
        )
        parts.append(f"{header}\n{h.markdown}")
        cites.append({
            "index": i,
            "chunk_id": h.chunk_id,
            "filename": h.filename,
            "sheet_name": h.sheet_name,
            "sheet_type": h.sheet_type,
            "hierarchy_path": h.hierarchy_path,
            "score": h.score,
            "sources": h.sources,
            "metadata": h.metadata,
        })
    return "\n\n".join(parts), cites


_CONF_PAT = re.compile(r"__CONFIDENCE__\s*[:：]\s*(\d+)")


def _save_message(session_id: str | None, role: str, content: str,
                  context_mode: str, citations: list[dict],
                  confidence: int | None) -> int:
    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO chat_message(session_id, role, content, context_mode, "
            "citations, confidence) VALUES (?, ?, ?, ?, ?, ?)",
            (session_id, role, content, context_mode,
             json.dumps(citations, ensure_ascii=False), confidence),
        )
        conn.commit()
        return int(cur.lastrowid)


@router.post("")
async def chat(req: ChatRequest):
    """Streamed answer with citations."""
    ctx_mode = "all"
    if req.document_id is not None:
        ctx_mode = f"doc:{req.document_id}"
    elif req.folder_id is not None:
        ctx_mode = f"folder:{req.folder_id}"

    async def event_gen():
        try:
            # 1. Retrieve
            hits = await hybrid_search(
                req.question,
                folder_id=req.folder_id,
                document_id=req.document_id,
                top_k=req.top_k,
            )

            ctx_text, cites = _build_context(hits)
            yield {"event": "hits", "data": json.dumps(cites, ensure_ascii=False)}

            # Save user message
            _save_message(req.session_id, "user", req.question, ctx_mode, [], None)

            if not hits:
                # Still ask the LLM but make the absence explicit; or short-circuit:
                no_ctx = (
                    "未检索到任何相关片段。请回复："
                    "「在所选范围内没有找到与该问题相关的内容。」"
                    "并输出 `__CONFIDENCE__: 1`。"
                )
                prompt = no_ctx
            else:
                prompt = (
                    _QA_TEMPLATE
                    .replace("{QUESTION}", req.question)
                    .replace("{CONTEXT}", ctx_text)
                )

            # 2. Stream from LLM
            full = []
            async for delta in ollama.chat_stream(
                [{"role": "user", "content": prompt}],
                options={"temperature": 0.2},
            ):
                full.append(delta)
                yield {"event": "token", "data": delta}

            content = "".join(full)
            # Extract confidence and strip it from the saved answer
            conf = None
            m = _CONF_PAT.search(content)
            if m:
                conf = int(m.group(1))
                content = _CONF_PAT.sub("", content).rstrip()
            yield {"event": "confidence", "data": str(conf or "")}

            # Save assistant message
            mid = _save_message(req.session_id, "assistant", content,
                                ctx_mode, cites, conf)
            yield {"event": "done", "data": json.dumps({"message_id": mid})}
        except Exception as e:
            yield {"event": "error", "data": str(e)}

    return EventSourceResponse(event_gen())
