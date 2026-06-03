"""Hybrid retrieval over chunks.

Three recall paths run in parallel, results fused with Reciprocal Rank
Fusion (RRF), then reranked by bge-reranker-v2-m3 to the final top-k.

    1. Vector recall on `chunk_vec` (BGE-M3 of the chunk's own text)
    2. Vector recall on `summary_vec` (BGE-M3 of multi-perspective
       summaries — gives a 2nd path with paraphrased / translated
       phrasings)
    3. BM25 on `chunk_fts` (Lindera-tokenized, covers exact terms,
       JIRA codes, table/column names)
    4. Exact-match boost for tokens that look like identifiers
       (DBL-179, KAIZEN-2635, ABC_123, BUDGED_COST_1) — these get a
       direct LIKE match so they cannot be missed even when embedding
       similarity is low.

Filters (folder / document / sheet_type / jira_tag) are applied
post-recall to keep the SQL simple.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from loguru import logger

from config import settings
from db import get_db
from ingestion.tokenizer import tokenize_for_fts
from llm.embed_client import embed_texts_async, rerank_async, vector_to_blob


_ID_PAT = re.compile(r"\b([A-Z][A-Z0-9_]{2,}-?\d+)\b")


@dataclass
class SearchHit:
    chunk_id: int
    score: float
    text: str
    markdown: str
    metadata: dict
    hierarchy_path: str
    sheet_name: str
    document_id: int
    folder_id: int
    sheet_type: str
    filename: str
    sources: list[str]                # which recall paths produced this hit


# ---------------------------------------------------------------------------
# Filter clause builders
# ---------------------------------------------------------------------------

def _scope_clause(folder_id: int | None, document_id: int | None) -> tuple[str, list]:
    where = []
    params: list = []
    if folder_id is not None:
        where.append("c.folder_id = ?")
        params.append(folder_id)
    if document_id is not None:
        where.append("c.document_id = ?")
        params.append(document_id)
    return (" AND " + " AND ".join(where)) if where else "", params


# ---------------------------------------------------------------------------
# Recall paths
# ---------------------------------------------------------------------------

async def _vector_recall_chunks(query_vec: list[float], limit: int,
                                folder_id: int | None,
                                document_id: int | None) -> list[tuple[int, float]]:
    scope_sql, params = _scope_clause(folder_id, document_id)
    sql = f"""
        SELECT c.id AS chunk_id, v.distance AS distance
        FROM chunk_vec v
        JOIN chunk c ON c.id = v.chunk_id
        WHERE v.embedding MATCH ?
          AND k = ?
          {scope_sql}
        ORDER BY v.distance
    """
    with get_db() as conn:
        rows = conn.execute(
            sql, (vector_to_blob(query_vec), limit, *params)
        ).fetchall()
    # distance is cosine distance; convert to similarity score 1 - d
    return [(int(r["chunk_id"]), 1.0 - float(r["distance"])) for r in rows]


async def _vector_recall_summaries(query_vec: list[float], limit: int,
                                   folder_id: int | None,
                                   document_id: int | None) -> list[tuple[int, float]]:
    scope_sql, params = _scope_clause(folder_id, document_id)
    sql = f"""
        SELECT cs.chunk_id AS chunk_id, sv.distance AS distance
        FROM summary_vec sv
        JOIN chunk_summary cs ON cs.id = sv.summary_id
        JOIN chunk c ON c.id = cs.chunk_id
        WHERE sv.embedding MATCH ?
          AND k = ?
          {scope_sql}
        ORDER BY sv.distance
    """
    with get_db() as conn:
        rows = conn.execute(
            sql, (vector_to_blob(query_vec), limit, *params)
        ).fetchall()
    # Aggregate per chunk: keep best (smallest distance)
    best: dict[int, float] = {}
    for r in rows:
        cid = int(r["chunk_id"])
        sim = 1.0 - float(r["distance"])
        if cid not in best or sim > best[cid]:
            best[cid] = sim
    return sorted(best.items(), key=lambda x: -x[1])


def _fts_recall(query: str, limit: int,
                folder_id: int | None,
                document_id: int | None) -> list[tuple[int, float]]:
    q_tok = tokenize_for_fts(query)
    if not q_tok.strip():
        return []
    scope_sql, params = _scope_clause(folder_id, document_id)
    # rowid < 10_000_000 → chunk; else → summary (rowid - 10_000_000 = summary_id)
    sql = f"""
        SELECT
            CASE WHEN f.rowid < 10000000 THEN f.rowid
                 ELSE (SELECT cs.chunk_id FROM chunk_summary cs WHERE cs.id = f.rowid - 10000000)
            END AS chunk_id,
            bm25(chunk_fts) AS score
        FROM chunk_fts f
        JOIN chunk c ON c.id = (
            CASE WHEN f.rowid < 10000000 THEN f.rowid
                 ELSE (SELECT cs.chunk_id FROM chunk_summary cs WHERE cs.id = f.rowid - 10000000)
            END)
        WHERE chunk_fts MATCH ?
          {scope_sql}
        ORDER BY score
        LIMIT ?
    """
    with get_db() as conn:
        rows = conn.execute(sql, (q_tok, *params, limit * 2)).fetchall()
    # bm25 returns negative scores in SQLite (lower = better); invert to similarity-ish
    out: dict[int, float] = {}
    for r in rows:
        cid = r["chunk_id"]
        if cid is None:
            continue
        s = -float(r["score"])  # higher = better
        if cid not in out or s > out[cid]:
            out[cid] = s
    return sorted(out.items(), key=lambda x: -x[1])[:limit]


def _exact_recall(query: str, limit: int,
                  folder_id: int | None,
                  document_id: int | None) -> list[tuple[int, float]]:
    """Direct LIKE search for tokens that look like identifiers/codes."""
    ids = _ID_PAT.findall(query)
    if not ids:
        return []
    scope_sql, params = _scope_clause(folder_id, document_id)
    out: dict[int, float] = {}
    with get_db() as conn:
        for tok in set(ids):
            sql = f"""
                SELECT c.id AS chunk_id
                FROM chunk c
                WHERE (c.text LIKE ? OR c.markdown LIKE ? OR c.chunk_metadata LIKE ?)
                  {scope_sql}
                LIMIT ?
            """
            like = f"%{tok}%"
            rows = conn.execute(sql, (like, like, like, *params, limit)).fetchall()
            for r in rows:
                cid = int(r["chunk_id"])
                # Each token gives a flat bonus; multiple tokens stack.
                out[cid] = out.get(cid, 0.0) + 1.0
    return sorted(out.items(), key=lambda x: -x[1])[:limit]


# ---------------------------------------------------------------------------
# RRF fusion
# ---------------------------------------------------------------------------

def _rrf(*ranked_lists: list[tuple[int, float]], k: int = 60) -> dict[int, tuple[float, list[str]]]:
    """Reciprocal Rank Fusion.

    Each list is ordered best→worst. Returns {chunk_id: (rrf_score, [source_names])}.
    """
    names = ["vec_chunk", "vec_summary", "fts", "exact"]
    scores: dict[int, float] = {}
    sources: dict[int, list[str]] = {}
    for src_name, lst in zip(names, ranked_lists):
        for rank, (cid, _) in enumerate(lst):
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + rank)
            sources.setdefault(cid, []).append(src_name)
    return {cid: (s, sources[cid]) for cid, s in scores.items()}


# ---------------------------------------------------------------------------
# Detail loading + reranking
# ---------------------------------------------------------------------------

def _load_chunks(chunk_ids: list[int]) -> dict[int, dict]:
    if not chunk_ids:
        return {}
    placeholders = ",".join("?" for _ in chunk_ids)
    sql = f"""
        SELECT c.id, c.text, c.markdown, c.chunk_metadata, c.hierarchy_path,
               c.document_id, c.folder_id,
               s.name AS sheet_name, s.sheet_type,
               d.filename AS filename
        FROM chunk c
        JOIN sheet s ON s.id = c.sheet_id
        JOIN document d ON d.id = c.document_id
        WHERE c.id IN ({placeholders})
    """
    with get_db() as conn:
        rows = conn.execute(sql, chunk_ids).fetchall()
    out: dict[int, dict] = {}
    for r in rows:
        out[int(r["id"])] = {
            "text": r["text"],
            "markdown": r["markdown"],
            "metadata": json.loads(r["chunk_metadata"] or "{}"),
            "hierarchy_path": r["hierarchy_path"] or "",
            "document_id": r["document_id"],
            "folder_id": r["folder_id"],
            "sheet_name": r["sheet_name"],
            "sheet_type": r["sheet_type"],
            "filename": r["filename"],
        }
    return out


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

async def hybrid_search(
    query: str,
    folder_id: int | None = None,
    document_id: int | None = None,
    top_k: int | None = None,
    recall_n: int | None = None,
) -> list[SearchHit]:
    """Run all 4 recall paths, fuse, rerank, and return top_k hits."""
    recall_n = recall_n or settings.recall_top_n
    top_k = top_k or settings.rerank_top_k

    # 1. Embed query once for both vector paths.
    vec_lists = await embed_texts_async([query])
    qvec = vec_lists[0]

    # 2. Run all 4 recalls (vector ones go through to_thread inside the
    #    embed/rerank clients; FTS and exact are sync but very fast).
    vec_chunks = await _vector_recall_chunks(qvec, recall_n, folder_id, document_id)
    vec_summary = await _vector_recall_summaries(qvec, recall_n, folder_id, document_id)
    fts_hits = _fts_recall(query, recall_n, folder_id, document_id)
    exact_hits = _exact_recall(query, recall_n, folder_id, document_id)

    # 3. RRF
    fused = _rrf(vec_chunks, vec_summary, fts_hits, exact_hits)
    if not fused:
        return []

    # 4. Take top-N candidates, load details, rerank
    candidates = sorted(fused.items(), key=lambda x: -x[1][0])[:recall_n]
    cand_ids = [cid for cid, _ in candidates]
    details = _load_chunks(cand_ids)

    docs = [details[cid]["text"] for cid in cand_ids if cid in details]
    valid_ids = [cid for cid in cand_ids if cid in details]
    rerank_scores = await rerank_async(query, docs)

    scored = []
    for cid, sc in zip(valid_ids, rerank_scores):
        d = details[cid]
        scored.append(SearchHit(
            chunk_id=cid,
            score=float(sc),
            text=d["text"],
            markdown=d["markdown"],
            metadata=d["metadata"],
            hierarchy_path=d["hierarchy_path"],
            sheet_name=d["sheet_name"],
            document_id=d["document_id"],
            folder_id=d["folder_id"],
            sheet_type=d["sheet_type"],
            filename=d["filename"],
            sources=fused[cid][1],
        ))
    scored.sort(key=lambda x: -x.score)
    logger.info(f"hybrid_search('{query[:40]}...') → {len(scored)} candidates, returning top {top_k}")
    return scored[:top_k]
