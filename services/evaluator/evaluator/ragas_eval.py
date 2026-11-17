"""RAGAS scoring over answers produced by the running service.

Scores are **reported, never used to gate**. The promotion gate is binary and fail-closed; a
probabilistic score belongs in reporting, not in a merge decision. See docs/architecture.md §8.

Version note (ragas 0.4.4) — the library is mid-migration and this file uses the older of its two
APIs, deliberately:

* ``ragas.metrics.collections`` holds the "modern v2" metrics. They are ``BaseMetric`` instances and
  ``evaluate()`` rejects them with *"All metrics must be initialised metric objects"*.
* ``ragas.metrics`` still exports the legacy singletons (``faithfulness``, ``answer_relevancy``, …),
  which *are* ``Metric`` instances and work with ``evaluate()``.
* Those singletons need the ``Langchain`` wrappers, which are themselves deprecated.

So the working path on 0.4.4 is legacy metrics plus deprecated wrappers. Both are pinned, and the
migration to the collections API should be done as one deliberate change against a future version —
not by mixing the two.

The judge is an OpenAI-compatible endpoint. Only the OpenAI shape is exercised here; scoring with
another provider depends on its structured-output support, which is unverified.
"""
from __future__ import annotations

from typing import Sequence

# Column names as emitted by the legacy metric singletons in ragas 0.4.4. Kept in one place so a
# version bump that renames a metric fails here rather than silently dropping a column.
METRIC_NAMES = (
    "faithfulness",
    "answer_relevancy",
    "context_precision",
    "context_recall",
    "answer_correctness",
)


def build_adapters(judge_model: str, base_url: str, api_key: str, embedding_model: str,
                   handler=None):
    """Judge and embedding adapters for the legacy metric path.

    ``langchain_openai`` is used only as the transport: it accepts a custom ``base_url``, which is
    what allows the judge to be any OpenAI-compatible endpoint.

    ``handler`` is registered on the judge model so every completion LangChain performs writes a
    token.usage record. Without it, judge spend exists only as one aggregate at the end of the run.
    """
    from langchain_openai import ChatOpenAI, OpenAIEmbeddings
    from ragas.embeddings import LangchainEmbeddingsWrapper
    from ragas.llms import LangchainLLMWrapper

    chat = ChatOpenAI(model=judge_model, api_key=api_key, base_url=base_url, temperature=0,
                      callbacks=[handler] if handler is not None else None)
    embeddings = OpenAIEmbeddings(model=embedding_model, api_key=api_key, base_url=base_url)
    return LangchainLLMWrapper(chat), LangchainEmbeddingsWrapper(embeddings)


def build_metrics():
    from ragas.metrics import (
        answer_correctness,
        answer_relevancy,
        context_precision,
        context_recall,
        faithfulness,
    )

    # answer_relevancy normally generates `strictness` (default 3) candidate questions and averages.
    # The LangChain wrapper returns a single generation, so ragas warns "LLM returned 1 generations
    # instead of requested 3" and silently scores from one sample. Setting strictness to 1 states
    # what actually happens instead of implying an average that was never computed.
    answer_relevancy.strictness = 1

    return [faithfulness, answer_relevancy, context_precision, context_recall, answer_correctness]


def score(
    rows: Sequence[dict],
    *,
    judge_model: str,
    judge_base_url: str,
    judge_api_key: str,
    embedding_model: str,
    label: str | None = None,
    max_cost_usd: float | None = None,
) -> dict:
    """Score rows of {case_id, question, contexts, answer, reference}.

    Missing metrics are null rather than omitted, so a column that disappears after a version bump
    is visible instead of quietly absent.
    """
    from ragas import EvaluationDataset, SingleTurnSample, evaluate

    samples = [
        SingleTurnSample(
            user_input=row["question"],
            retrieved_contexts=list(row["contexts"]),
            response=row["answer"],
            reference=row["reference"],
        )
        for row in rows
    ]
    dataset = EvaluationDataset(samples=samples)

    from evaluator.langchain_usage import LangChainUsageHandler
    from evaluator import token_ledger

    usage_handler = LangChainUsageHandler(
        component="evaluator",
        operation="eval.judge",
        label=label,
        model=judge_model,
        base_url=judge_base_url,
        table=token_ledger.load_rates(),
        max_cost_usd=max_cost_usd,
    )

    llm, embeddings = build_adapters(judge_model, judge_base_url, judge_api_key, embedding_model,
                                     handler=usage_handler)
    metrics = build_metrics()

    # Judge token usage is not exposed by evaluate()'s result object, so it is captured from the
    # LangChain callback instead. If the callback is unavailable the run still proceeds and the
    # receipt records that usage was not measured.
    try:
        from langchain_community.callbacks import get_openai_callback

        with get_openai_callback() as callback:
            result = evaluate(
                dataset,
                metrics=metrics,
                llm=llm,
                embeddings=embeddings,
                raise_exceptions=False,
                show_progress=False,
            )
        usage = {
            "prompt_tokens": callback.prompt_tokens,
            "completion_tokens": callback.completion_tokens,
            "total_tokens": callback.total_tokens,
            "total_cost_usd": round(float(callback.total_cost), 6),
            "successful_requests": callback.successful_requests,
        }
    except ImportError:
        result = evaluate(
            dataset,
            metrics=metrics,
            llm=llm,
            embeddings=embeddings,
            raise_exceptions=False,
            show_progress=False,
        )
        usage = None

    frame = result.to_pandas()
    per_sample = []
    # ragas preserves input order, so the frame index maps back to the row list.
    for position, (_, record) in enumerate(frame.iterrows()):
        entry = {"case_id": rows[position]["case_id"] if position < len(rows) else str(position)}
        for name in METRIC_NAMES:
            value = record.get(name)
            entry[name] = None if value is None or value != value else round(float(value), 4)
        per_sample.append(entry)

    aggregate = {}
    for name in METRIC_NAMES:
        if name in frame.columns:
            series = frame[name].dropna()
            aggregate[name] = round(float(series.mean()), 4) if len(series) else None
        else:
            aggregate[name] = None

    return {
        "per_sample": per_sample,
        "aggregate": aggregate,
        "usage": usage,
        "judge_ledger": {
            "records": len(usage_handler.records),
            "total_tokens": usage_handler.total_tokens,
            "total_cost_usd": usage_handler.total_cost_usd,
            "unpriced": sum(1 for record in usage_handler.records if record.get("cost_usd") is None),
            "exceeded": usage_handler.exceeded,
            "limit_usd": max_cost_usd,
        },
        "metric_names_returned": sorted(str(c) for c in frame.columns if c in METRIC_NAMES),
        "result_attributes": sorted(n for n in dir(result) if not n.startswith("_"))[:40],
    }
