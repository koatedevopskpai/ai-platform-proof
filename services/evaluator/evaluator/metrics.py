"""Evaluation metrics: faithfulness, answer relevancy, ROUGE-L, MAP@k, nDCG."""
from __future__ import annotations

import math
import re
from collections import Counter


def _tokens(text: str) -> list[str]:
    return [_stem(t) for t in re.findall(r"[a-z0-9]+", text.lower())]


def _stem(word: str) -> str:
    """Light suffix stemming so lexical similarity behaves like real retrieval."""
    stemmed = word
    for suffix in ("ing", "ed", "es", "s"):
        if len(stemmed) > 4 and stemmed.endswith(suffix):
            stemmed = stemmed[: -len(suffix)]
            break
    if len(stemmed) > 3 and stemmed.endswith("e"):
        stemmed = stemmed[:-1]
    return stemmed


def rouge_l(prediction: str, reference: str) -> float:
    """F-measure based on longest common subsequence."""
    p = _tokens(prediction)
    r = _tokens(reference)
    if not p or not r:
        return 0.0

    dp = [[0] * (len(r) + 1) for _ in range(len(p) + 1)]
    for i in range(1, len(p) + 1):
        for j in range(1, len(r) + 1):
            if p[i - 1] == r[j - 1]:
                dp[i][j] = dp[i - 1][j - 1] + 1
            else:
                dp[i][j] = max(dp[i - 1][j], dp[i][j - 1])
    lcs = dp[len(p)][len(r)]

    precision = lcs / len(p)
    recall = lcs / len(r)
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def map_at_k(relevant: set[str], retrieved: list[str], k: int | None = None) -> float:
    """Mean average precision at k over the ranked list."""
    k = k or len(retrieved)
    hits, ap = 0, 0.0
    for rank, doc_id in enumerate(retrieved[:k], start=1):
        if doc_id in relevant:
            hits += 1
            ap += hits / rank
    return ap / min(len(relevant), k) if relevant else 0.0


def ndcg_at_k(relevant: set[str], retrieved: list[str], k: int | None = None) -> float:
    """Normalised discounted cumulative gain at k (binary relevance)."""
    k = k or len(retrieved)
    dcg = sum(1.0 / math.log2(rank + 1) for rank, doc_id in enumerate(retrieved[:k], start=1)
              if doc_id in relevant)
    ideal = sum(1.0 / math.log2(rank + 1) for rank in range(1, min(len(relevant), k) + 1))
    return dcg / ideal if ideal > 0 else 0.0


def faithfulness(answer: str, contexts: list[str]) -> float:
    """Contextual lexical overlap proxy (RAGAS-style faithfulness without a judge)."""
    ctx = sum((Counter(_tokens(c)) for c in contexts), Counter())
    ans = Counter(_tokens(answer))
    if not ans:
        return 0.0
    supported = sum(count for tok, count in ans.items() if tok in ctx)
    return min(supported / sum(ans.values()), 1.0)


def answer_relevancy(question: str, answer: str) -> float:
    q = Counter(_tokens(question))
    a = Counter(_tokens(answer))
    if not a:
        return 0.0
    overlap = sum(count for tok, count in q.items() if tok in a)
    return min(overlap / max(sum(q.values()), 1) * 2.0, 1.0)