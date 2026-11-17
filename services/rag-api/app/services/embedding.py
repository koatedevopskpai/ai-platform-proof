"""Embedding service with a deterministic mock mode.

Mock mode produces a stable, seeded vector per text so the entire platform runs offline and
evaluation is reproducible. Real mode calls an OpenAI-compatible embeddings endpoint.
"""
from __future__ import annotations

import hashlib
import math
from typing import Sequence

import httpx

from app.core.config import settings
from app.core.telemetry import span
from app.core import tokens


class EmbeddingService:
    def __init__(self) -> None:
        self.mode = settings.embedding_mode
        self.dim = settings.embedding_dim
        self.model = settings.embedding_model
        self.send_dimensions = settings.embedding_send_dimensions

    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        if self.mode == "openai" and settings.openai_api_key:
            vectors, usage = self._embed_openai(list(texts))
            tokens.record(
                component="rag-api",
                operation="embedding.embed",
                model=self.model,
                usage=usage,
                base_url=settings.openai_base_url,
            )
            return vectors
        return [self._embed_mock(t) for t in texts]

    def embed(self, text: str) -> list[float]:
        with span('embedding.embed', 'embedding'):
            return self.embed_texts([text])[0]

    def _embed_mock(self, text: str) -> list[float]:
        """Deterministic seeded vector from hashed character trigrams.

        Character trigrams give morphological similarity (e.g. "refund" ~ "refunds"),
        so semantic retrieval behaves realistically without an external model.
        Must be byte-identical to the .NET mock embedder so the shared pgvector
        store is interoperable across languages.
        """
        vec = [0.0] * self.dim
        for word in text.lower().split():
            padded = f"#{word}#"
            for i in range(len(padded) - 2):
                tri = padded[i : i + 3]
                digest = hashlib.sha256(tri.encode("utf-8")).digest()
                idx = int.from_bytes(digest[:4], "little") % self.dim
                sign = 1.0 if digest[4] % 2 == 0 else -1.0
                vec[idx] += sign
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]

    def _embed_openai(self, texts: list[str]) -> tuple[list[list[float]], dict | None]:
        headers = {"Authorization": f"Bearer {settings.openai_api_key}"}
        payload = {"model": self.model, "input": texts}
        # text-embedding-3-* supports a dimensions parameter, so the provider can be asked for the
        # schema width. Without this the provider returns 1536 dimensions and every insert into
        # vector(384) fails — a migration would then be needed to keep the schema, the HNSW index
        # and the RDS parity receipt valid.
        if self.send_dimensions:
            payload["dimensions"] = self.dim
        with httpx.Client(timeout=30.0) as client:
            resp = client.post(
                f"{settings.openai_base_url}/embeddings", json=payload, headers=headers
            )
            resp.raise_for_status()
            body = resp.json()
        # Providers may return batch items out of order; ordering by index keeps the mapping
        # between input text and stored vector correct.
        vectors = [item["embedding"] for item in sorted(body["data"], key=lambda item: item["index"])]
        self._require_dimensions(vectors)
        return vectors, body.get("usage")

    def _require_dimensions(self, vectors: list[list[float]]) -> None:
        """Fail loudly on a width mismatch rather than letting the insert fail later.

        A vector of the wrong width is rejected by PostgreSQL as an opaque type error at insert
        time, several layers away from the cause. This raises where the mismatch happened.
        """
        for index, vector in enumerate(vectors):
            width = len(vector) if isinstance(vector, list) else None
            if width != self.dim:
                raise ValueError(
                    f"provider returned {width} dimensions for input {index}, expected {self.dim}; "
                    "set embedding_send_dimensions or match the model to the schema"
                )


embedding_service = EmbeddingService()
