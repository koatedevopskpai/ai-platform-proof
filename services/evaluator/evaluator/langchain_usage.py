"""LangChain callback that captures per-call token usage for the evaluator.

RAGAS drives its judge through LangChain, so those calls never passed through the service's client and
never reached the token ledger. Their cost was visible only as one aggregate produced at the end of a
run — measurable after the fact, unattributable during it.

Registering this handler on the judge model closes that path: every completion LangChain performs
becomes a `token.usage` record with component `evaluator`, operation `eval.judge`, the run label and
the model, in the same ledger and the same schema as the service.

Extraction is deliberately defensive. LangChain reports usage in more than one place depending on
version and model wrapper, so the handler looks in both the classic `llm_output.token_usage` and the
newer per-message `usage_metadata`. If neither is present the call is still recorded — with cost
unknown and a reason — because a call that occurred but was not itemised is exactly the black hole
this exists to prevent.
"""
from __future__ import annotations

from langchain_core.callbacks import BaseCallbackHandler

from evaluator import token_ledger


def _normalise(usage: dict) -> dict:
    prompt = usage.get("prompt_tokens", usage.get("input_tokens", 0)) or 0
    completion = usage.get("completion_tokens", usage.get("output_tokens", 0)) or 0
    total = usage.get("total_tokens", int(prompt) + int(completion)) or 0
    return {"prompt_tokens": int(prompt), "completion_tokens": int(completion), "total_tokens": int(total)}


def extract_usage(response) -> dict | None:
    """Find token usage in whichever place the wrapper put it."""
    llm_output = getattr(response, "llm_output", None) or {}

    for key in ("token_usage", "usage"):
        candidate = llm_output.get(key)
        if isinstance(candidate, dict):
            return _normalise(candidate)

    for generation_list in getattr(response, "generations", []) or []:
        for generation in generation_list:
            message = getattr(generation, "message", None)
            metadata = getattr(message, "usage_metadata", None) if message is not None else None
            if isinstance(metadata, dict):
                return _normalise(metadata)
            info = getattr(generation, "generation_info", None) or {}
            for key in ("usage", "token_usage"):
                if isinstance(info.get(key), dict):
                    return _normalise(info[key])

    return None


def extract_model(response, kwargs) -> str | None:
    llm_output = getattr(response, "llm_output", None) or {}
    for key in ("model_name", "model"):
        value = llm_output.get(key)
        if isinstance(value, str) and value:
            return value

    params = (kwargs or {}).get("invocation_params") or {}
    for key in ("model", "model_name"):
        value = params.get(key)
        if isinstance(value, str) and value:
            return value
    return None


class LangChainUsageHandler(BaseCallbackHandler):
    """Records one ledger entry per completed completion."""

    def __init__(self, *, component: str = "evaluator", operation: str = "eval.judge",
                 label: str | None = None, model: str | None = None,
                 base_url: str = "", table: dict | None = None) -> None:
        self.component = component
        self.operation = operation
        self.label = label
        self.default_model = model
        self.base_url = base_url
        self.table = table
        self.records: list[dict] = []

    def on_llm_end(self, response, **kwargs) -> None:  # noqa: D401 - LangChain hook
        usage = extract_usage(response)
        model = extract_model(response, kwargs) or self.default_model or "unknown"

        # A call that happened but was not itemised gets cost unknown and a reason, rather than a
        # zero that would read as "this call was free". Written in one pass so the ledger never
        # briefly holds the wrong value.
        entry = token_ledger.record(
            component=self.component,
            operation=self.operation,
            model=model,
            usage=usage,
            base_url=self.base_url,
            label=self.label,
            table=self.table,
            reason_override=None if usage else "usage not reported by the provider wrapper",
        )
        self.records.append(entry)

    @property
    def total_tokens(self) -> int:
        return sum(int(record["total_tokens"]) for record in self.records)

    @property
    def total_cost_usd(self) -> float:
        return round(sum(float(record["cost_usd"] or 0.0) for record in self.records), 9)
