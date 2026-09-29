"""RAG answer generation with guardrails, PII redaction and deterministic fallback."""
from __future__ import annotations

import re
from typing import Sequence

import httpx

from app.core.config import settings


class RAGService:
    PROMPT_INJECTION_PATTERNS = [
        re.compile(r"ignore (all )?previous instructions", re.I),
        re.compile(r"system prompt", re.I),
        re.compile(r"you are now (a|an|dan)", re.I),
        re.compile(r"<\|im_start\|>", re.I),
    ]

    PII_PATTERNS = [
        re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"),
        re.compile(r"\+?\d[\d\s()-]{8,}\d"),
        re.compile(r"\b\d{16}\b"),  # card numbers
    ]

    def answer(self, query: str, contexts: Sequence[dict]) -> dict:
        guardrail = self._check_guardrails(query)
        if guardrail:
            return {"answer": guardrail, "sources": [], "fallback": False, "blocked": True}

        redacted = self._redact_pii(query)

        prompt = (
            "You are a precise, safety-critical AI assistant. Answer ONLY from the provided "
            "context. If the context does not contain the answer, say so explicitly. "
            "Never invent information.\n\n"
            f"CONTEXT:\n{self._format_context(contexts)}\n\n"
            f"QUESTION: {redacted}\nANSWER:"
        )

        answer, used_llm = self._generate(prompt, contexts)

        return {
            "answer": answer,
            "sources": [c["id"] for c in contexts],
            "fallback": not used_llm,
            "blocked": False,
        }

    def _generate(self, prompt: str, contexts: Sequence[dict]) -> tuple[str, bool]:
        if settings.openai_api_key and contexts:
            try:
                resp = httpx.post(
                    f"{settings.openai_base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {settings.openai_api_key}"},
                    json={
                        "model": settings.llm_model,
                        "messages": [{"role": "user", "content": prompt}],
                        "temperature": 0.0,
                    },
                    timeout=30.0,
                )
                resp.raise_for_status()
                return resp.json()["choices"][0]["message"]["content"].strip(), True
            except (httpx.HTTPError, KeyError):
                pass
        return self._deterministic_fallback(prompt, contexts), False

    @staticmethod
    def _deterministic_fallback(prompt: str, contexts: Sequence[dict]) -> str:
        if not contexts:
            return "No relevant information found in the knowledge base."
        return f"Per the source material: {' '.join(c['content'] for c in contexts)[:400]}"

    @staticmethod
    def _format_context(contexts: Sequence[dict]) -> str:
        return "\n\n".join(f"[{i + 1}] {c['content']}" for i, c in enumerate(contexts))

    def _check_guardrails(self, text: str) -> str | None:
        for pattern in self.PROMPT_INJECTION_PATTERNS:
            if pattern.search(text):
                return "Input blocked: prompt-injection pattern detected."
        return None

    def _redact_pii(self, text: str) -> str:
        for pattern in self.PII_PATTERNS:
            text = pattern.sub("[REDACTED]", text)
        return text


rag_service = RAGService()