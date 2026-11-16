"""Hybrid retrieval: dense (pgvector cosine) + keyword (full-text), fused by rank.

Dense and keyword scores are not on a common scale — `1 - cosine_distance` is bounded
while `ts_rank` is not — so they are combined with Reciprocal Rank Fusion rather than a
weighted sum. RRF uses only each channel's position, which makes the two contributions
directly comparable:

    score = 1 / (60 + rank_dense) + 1 / (60 + rank_keyword)

The keyword channel uses `websearch_to_tsquery`, which tolerates natural-language input.
`plainto_tsquery` was used previously and ANDs every lexeme, so a question such as
"What is the refund window" became `'refund' & 'window'` and matched nothing.

`rag_min_score` is a floor on **dense cosine similarity only**. A document found purely
by keyword has no dense similarity, so it is not floored — that is the point of having a
second channel. See docs/architecture.md for the measurements behind these choices.
"""
from __future__ import annotations

import re

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.telemetry import span

# Standard RRF damping constant. Higher values flatten the contribution of top ranks.
RRF_K = 60

_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")


def keyword_query(query_text: str) -> str | None:
    """Turn a natural-language question into an OR-joined web-search query.

    `websearch_to_tsquery` ANDs bare terms, so passing a question directly requires every
    word to appear in one document. Measured: "What does the zygomorphpolicy provision
    cover" becomes `'zygomorphpolici' & 'provis' & 'cover'` and matches nothing, even
    though the target document contains two of the three terms.

    OR-joining makes the keyword channel recall-first; `ts_rank` then orders documents by
    how much of the query they cover, so a document matching every term outranks one
    matching a single term. Stopwords are removed by the `english` configuration.
    """
    tokens = _TOKEN_RE.findall(query_text or "")
    return " or ".join(tokens) if tokens else None

_HYBRID_SQL = text(
    f"""
    WITH dense AS (
        SELECT id, content, distance,
               ROW_NUMBER() OVER (ORDER BY distance) AS rank
        FROM (
            SELECT id, content,
                   embedding <=> CAST(:qvec AS vector) AS distance
            FROM documents
            WHERE embedding IS NOT NULL
            ORDER BY embedding <=> CAST(:qvec AS vector)
            LIMIT :k
        ) nearest
    ),
    keyword AS (
        SELECT id, content,
               ROW_NUMBER() OVER (ORDER BY relevance DESC) AS rank
        FROM (
            SELECT id, content,
                   ts_rank(to_tsvector('english', content),
                           websearch_to_tsquery('english', :q)) AS relevance
            FROM documents
            WHERE :q IS NOT NULL
              AND to_tsvector('english', content) @@ websearch_to_tsquery('english', :q)
            ORDER BY relevance DESC
            LIMIT :k
        ) matches
    ),
    fused AS (
        SELECT COALESCE(d.id, w.id) AS id,
               COALESCE(d.content, w.content) AS content,
               CASE WHEN d.id IS NULL THEN NULL ELSE 1 - d.distance END AS similarity,
               COALESCE(1.0 / ({RRF_K} + d.rank), 0)
             + COALESCE(1.0 / ({RRF_K} + w.rank), 0) AS score
        FROM dense d
        FULL OUTER JOIN keyword w ON d.id = w.id
    )
    SELECT id, content, similarity, score
    FROM fused
    ORDER BY score DESC, id
    LIMIT :k
    """
)


class RetrievalService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def search(
        self, query_embedding: list[float], top_k: int | None = None, query_text: str = ""
    ) -> list[dict]:
        with span('retrieval.search', 'pgvector'):
            return self._search(query_embedding, top_k, query_text)

    def _search(
        self,
        query_embedding: list[float],
        top_k: int | None = None,
        query_text: str = "",
    ) -> list[dict]:
        top_k = top_k or settings.rag_top_k
        rows = self.session.execute(
            _HYBRID_SQL,
            {
                "qvec": str(query_embedding),
                "q": keyword_query(query_text),
                "k": top_k,
            },
        ).mappings().all()

        results = []
        for row in rows:
            similarity = row["similarity"]
            if similarity is not None and similarity < settings.rag_min_score:
                continue
            results.append(
                {
                    "id": row["id"],
                    "content": row["content"],
                    "score": round(float(row["score"]), 6),
                }
            )
        return results
