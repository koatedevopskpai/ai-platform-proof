"""Hybrid retrieval: dense (pgvector cosine) + keyword (full-text) with fused scoring."""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import settings


class RetrievalService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def search(
        self,
        query_embedding: list[float],
        top_k: int | None = None,
        query_text: str = "",
    ) -> list[dict]:
        top_k = top_k or settings.rag_top_k
        sql = text(
            """
            WITH dense AS (
                SELECT id, content,
                       1 - (embedding <=> :qvec) AS dense_score
                FROM documents
                WHERE embedding IS NOT NULL
                ORDER BY embedding <=> :qvec
                LIMIT :k
            ),
            kw AS (
                SELECT id, content,
                       ts_rank(to_tsvector('english', content),
                               plainto_tsquery('english', :q)) AS kw_score
                FROM documents
                LIMIT :k
            )
            SELECT COALESCE(d.id, k.id) AS id,
                   COALESCE(d.content, k.content) AS content,
                   COALESCE(d.dense_score, 0.0) AS dense_score,
                   COALESCE(k.kw_score, 0.0) AS kw_score,
                   (COALESCE(d.dense_score, 0.0) * 0.7 + COALESCE(k.kw_score, 0.0) * 0.3) AS fused
            FROM dense d
            FULL OUTER JOIN kw k ON d.id = k.id
            ORDER BY fused DESC
            LIMIT :k
            """
        )
        rows = self.session.execute(
            sql,
            {"qvec": str(query_embedding), "q": query_text or "ai platform", "k": top_k},
        ).mappings().all()

        results = []
        for row in rows:
            if row["fused"] < settings.rag_min_score:
                continue
            results.append(
                {
                    "id": row["id"],
                    "content": row["content"],
                    "score": round(float(row["fused"]), 4),
                }
            )
        return results