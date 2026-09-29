"""LLM-as-judge client with a deterministic lexical fallback.

Running evaluation in CI with a real LLM is expensive and flaky. The mock judge is a
well-understood lexical heuristic so the gate is reproducible; set OPENAI_API_KEY to use a real
judge model.
"""
from __future__ import annotations

import os
import re
from collections import Counter

import httpx


class JudgeClient:
    def __init__(self) -> None:
        self.api_key = os.getenv("OPENAI_API_KEY", "")
        self.base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
        self.model = os.getenv("JUDGE_MODEL", "gpt-4o-mini")

    def faithfulness(self, answer: str, contexts: list[str]) -> float:
        """Proportion of answer claims supported by the context."""
        if self.api_key:
            prompt = (
                "Rate faithfulness of the ANSWER to the CONTEXT from 0.0 to 1.0. "
                "Return only the number.\n\n"
                f"CONTEXT:\n{' '.join(contexts)}\n\nANSWER:\n{answer}"
            )
            return self._llm_score(prompt)
        return self._lexical_faithfulness(answer, contexts)

    def answer_relevancy(self, question: str, answer: str) -> float:
        if self.api_key:
            prompt = (
                "Rate how well ANSWER addresses QUESTION from 0.0 to 1.0. Return only the number.\n\n"
                f"QUESTION:\n{question}\n\nANSWER:\n{answer}"
            )
            return self._llm_score(prompt)
        return self._lexical_relevancy(question, answer)

    def _llm_score(self, prompt: str) -> float:
        try:
            resp = httpx.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": self.model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.0,
                },
                timeout=30.0,
            )
            resp.raise_for_status()
            text = resp.json()["choices"][0]["message"]["content"]
            match = re.search(r"\b(?:0(?:\.\d+)?|1(?:\.0+)?)\b", text)
            if match:
                return min(max(float(match.group()), 0.0), 1.0)
        except (httpx.HTTPError, KeyError, ValueError):
            pass
        return 0.5

    @staticmethod
    def _tokens(text: str) -> Counter:
        return Counter(
            JudgeClient._stem(t)
            for t in re.findall(r"[a-z0-9]+", text.lower())
        )

    @staticmethod
    def _stem(word: str) -> str:
        stemmed = word
        for suffix in ("ing", "ed", "es", "s"):
            if len(stemmed) > 4 and stemmed.endswith(suffix):
                stemmed = stemmed[: -len(suffix)]
                break
        if len(stemmed) > 3 and stemmed.endswith("e"):
            stemmed = stemmed[:-1]
        return stemmed

    def _lexical_faithfulness(self, answer: str, contexts: list[str]) -> float:
        context_bag = sum((self._tokens(c) for c in contexts), Counter())
        ans = self._tokens(answer)
        if not ans:
            return 0.0
        supported = sum(count for tok, count in ans.items() if tok in context_bag)
        return min(supported / sum(ans.values()), 1.0)

    def _lexical_relevancy(self, question: str, answer: str) -> float:
        q = self._tokens(question)
        a = self._tokens(answer)
        if not a:
            return 0.0
        overlap = sum(count for tok, count in q.items() if tok in a)
        return min(overlap / max(sum(q.values()), 1) * 2.0, 1.0)


judge = JudgeClient()