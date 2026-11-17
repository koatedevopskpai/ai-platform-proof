"""RAG answer generation with guardrails, PII redaction and deterministic fallback."""
from __future__ import annotations

import re
from typing import Sequence

import httpx

from app.core.config import settings
from app.core.telemetry import span
from app.core import tokens


class GenerationBlocked(RuntimeError):
    """A pre-flight guard refused the call before any tokens were spent.

    Raised rather than returning a fallback answer: a fallback would still produce text, and a client
    that ignores `fallback: true` would ship it. A guard that silently answers is not a guard.
    """

    def __init__(self, reason: str, status_code: int = 503) -> None:
        super().__init__(reason)
        self.reason = reason
        self.status_code = status_code


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

    def __init__(self) -> None:
        # Generation may use a different OpenAI-compatible provider from embeddings, so it gets
        # its own credentials with a fallback to the shared ones. This is what makes a two-provider
        # receipt possible: embed with one vendor, generate with another.
        self.api_key = settings.llm_api_key or settings.openai_api_key
        self.base_url = settings.llm_base_url or settings.openai_base_url
        self.model = settings.llm_model

    def answer(self, query: str, contexts: Sequence[dict]) -> dict:
        guardrail = self._check_guardrails(query)
        if guardrail:
            return {"answer": guardrail, "sources": [], "fallback": False, "blocked": True,
                    "generation_mode": None, "fallback_reason": None, "usage": None, "model": None,
                    "truncated": False}

        redacted = self._redact_pii(query)

        prompt = (
            "You are a precise, safety-critical AI assistant. Answer ONLY from the provided "
            "context. If the context does not contain the answer, say so explicitly. "
            "Never invent information.\n\n"
            f"CONTEXT:\n{self._format_context(contexts)}\n\n"
            f"QUESTION: {redacted}\nANSWER:"
        )

        with span('model.generate', 'rag-model') as observation:
            answer, used_llm, reason, usage, truncated = self._generate(prompt, contexts)
            observation.update(generation_mode='provider' if used_llm else 'deterministic_fallback', fallback_reason=reason,
                               model=self.model if used_llm else None,
                               total_tokens=(usage or {}).get('total_tokens'))
            if reason and reason.startswith('provider_'):
                observation.update(status='error', error_type=reason)

        return {
            "answer": answer,
            "sources": [c["id"] for c in contexts],
            "fallback": not used_llm,
            "blocked": False,
            "generation_mode": "provider" if used_llm else "deterministic_fallback",
            "fallback_reason": reason,
            "usage": usage,
            "model": self.model if used_llm else None,
            "truncated": truncated,
        }

    def _preflight(self, prompt: str, contexts: Sequence[dict]) -> None:
        """Refuse the call before it is made. A breach here costs nothing.

        Skipped entirely when no provider call would happen, so mock mode is unaffected.
        """
        if not (self.api_key and contexts):
            return

        allowed = {name.strip() for name in settings.llm_model_allowlist.split(',') if name.strip()}
        if allowed and self.model not in allowed:
            raise GenerationBlocked('model_not_allowed', 503)

        if settings.token_require_priced_model:
            _, row = tokens.price(self.model, 0, 0)
            if row is None:
                raise GenerationBlocked('model_not_priced', 503)

        estimated_tokens = max(1, len(prompt) // 4)
        if estimated_tokens > settings.llm_max_prompt_tokens:
            raise GenerationBlocked('prompt_too_large', 400)

    def _generate(self, prompt: str, contexts: Sequence[dict]) -> tuple[str, bool, str | None, dict | None, bool]:
        self._preflight(prompt, contexts)
        if self.api_key and contexts:
            try:
                resp = httpx.post(
                    f"{self.base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json={
                        "model": self.model,
                        "messages": [{"role": "user", "content": prompt}],
                        "temperature": 0.0,
                        # The completion cannot exceed this, so one call cannot run away.
                        "max_tokens": settings.llm_max_completion_tokens,
                    },
                    timeout=30.0,
                )
                resp.raise_for_status()
                body = resp.json()
                choice = body["choices"][0]
                answer = choice["message"]["content"]
                if not isinstance(answer, str) or not answer.strip():
                    raise ValueError("empty provider answer")
                # A provider that stops at the cap returns a partial answer. Surfacing that is the
                # difference between a bounded call and a silently truncated one.
                truncated = choice.get("finish_reason") == "length"
                tokens.record(
                    component="rag-api",
                    operation="rag.answer",
                    model=self.model,
                    usage=body.get("usage"),
                    base_url=self.base_url,
                )
                return answer.strip(), True, None, body.get("usage"), truncated
            except httpx.HTTPStatusError as exc:
                reason = f"provider_http_{exc.response.status_code}"
            except httpx.HTTPError:
                reason = "provider_transport_error"
            except (KeyError, ValueError, TypeError, IndexError):
                reason = "provider_invalid_response"
            return self._deterministic_fallback(prompt, contexts), False, reason, None, False
        reason = "no_context" if not contexts else "mock_generation"
        return self._deterministic_fallback(prompt, contexts), False, reason, None, False

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
