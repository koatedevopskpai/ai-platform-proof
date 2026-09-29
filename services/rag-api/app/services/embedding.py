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


class EmbeddingService:
    def __init__(self) -> None:
        self.mode = settings.embedding_mode
        self.dim = settings.embedding_dim

    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        if self.mode == "openai" and settings.openai_api_key:
            return self._embed_openai(list(texts))
        return [self._embed_mock(t) for t in texts]

    def embed(self, text: str) -> list[float]:
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

    def _embed_openai(self, texts: list[str]) -> list[list[float]]:
        headers = {"Authorization": f"Bearer {settings.openai_api_key}"}
        payload = {"model": settings.embedding_model, "input": texts}
        with httpx.Client(timeout=30.0) as client:
            resp = client.post(
                f"{settings.openai_base_url}/embeddings", json=payload, headers=headers
            )
            resp.raise_for_status()
            return [item["embedding"] for item in resp.json()["data"]]


embedding_service = EmbeddingService()