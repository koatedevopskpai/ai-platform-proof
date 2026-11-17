"""The LangChain usage handler: extraction, attribution and the missing-usage path.

The handler exists because RAGAS drives its judge through LangChain. Those calls never passed through
the service's client, so their cost appeared only as one aggregate at the end of a run.
"""
import json
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, Generation, LLMResult

from evaluator import token_ledger
from evaluator.langchain_usage import LangChainUsageHandler, extract_model, extract_usage


def chat_result(*, usage_metadata=None, llm_output=None, generation_info=None):
    message = (AIMessage(content="ok", usage_metadata=usage_metadata)
               if usage_metadata else AIMessage(content="ok"))
    generation = ChatGeneration(message=message, generation_info=generation_info)
    return LLMResult(generations=[[generation]], llm_output=llm_output)


def ledger_entries():
    path = Path(token_ledger.ledger_path())
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_usage_from_classic_llm_output():
    result = chat_result(llm_output={"token_usage": {"prompt_tokens": 10, "completion_tokens": 4,
                                                     "total_tokens": 14}})
    assert extract_usage(result) == {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14}


def test_usage_from_message_metadata():
    result = chat_result(usage_metadata={"input_tokens": 7, "output_tokens": 3, "total_tokens": 10})
    assert extract_usage(result) == {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10}


def test_usage_from_generation_info():
    generation = Generation(text="ok", generation_info={"usage": {"prompt_tokens": 2, "completion_tokens": 1}})
    result = LLMResult(generations=[[generation]], llm_output=None)
    assert extract_usage(result) == {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3}


def test_usage_absent_returns_none():
    assert extract_usage(chat_result(llm_output=None)) is None


def test_model_is_taken_from_output_then_invocation_params():
    assert extract_model(chat_result(llm_output={"model_name": "gpt-4o-mini"}), {}) == "gpt-4o-mini"
    assert extract_model(chat_result(), {"invocation_params": {"model": "deepseek-flash"}}) == "deepseek-flash"


def test_records_one_entry_per_completion_with_evaluator_attribution():
    handler = LangChainUsageHandler(label="judge-run", model="gpt-4o-mini")
    handler.on_llm_end(chat_result(llm_output={
        "token_usage": {"prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110},
        "model_name": "gpt-4o-mini",
    }))

    entries = ledger_entries()
    assert len(entries) == 1
    entry = entries[0]
    assert entry["component"] == "evaluator"
    assert entry["operation"] == "eval.judge"
    assert entry["label"] == "judge-run"
    assert entry["model"] == "gpt-4o-mini"
    assert entry["total_tokens"] == 110
    assert entry["cost_usd"] == pytest.approx(100 / 1_000_000 * 0.15 + 10 / 1_000_000 * 0.60)


def test_accumulates_totals_for_run_level_reporting():
    handler = LangChainUsageHandler(model="gpt-4o-mini")
    handler.on_llm_end(chat_result(llm_output={"token_usage": {"prompt_tokens": 100, "completion_tokens": 10}}))
    handler.on_llm_end(chat_result(llm_output={"token_usage": {"prompt_tokens": 50, "completion_tokens": 5}}))
    assert handler.total_tokens == 165
    assert handler.total_cost_usd > 0


def test_missing_usage_is_recorded_with_unknown_cost_not_zero():
    handler = LangChainUsageHandler(label="judge-run", model="gpt-4o-mini")
    handler.on_llm_end(chat_result(llm_output=None))

    entry = ledger_entries()[0]
    assert entry["cost_usd"] is None
    assert "usage not reported" in entry["unpriced_reason"]
    assert entry["label"] == "judge-run"


def test_unpriced_model_is_recorded_with_reason():
    handler = LangChainUsageHandler(model="mystery-model")
    handler.on_llm_end(chat_result(llm_output={"token_usage": {"prompt_tokens": 5, "completion_tokens": 5}}))

    entry = ledger_entries()[0]
    assert entry["cost_usd"] is None
    assert "absent from rate table" in entry["unpriced_reason"]


def test_a_dated_snapshot_prices_as_its_alias():
    """OpenAI reports gpt-4o-mini-2024-07-18; the price list is keyed on gpt-4o-mini."""
    assert token_ledger.normalise_model("gpt-4o-mini-2024-07-18") == "gpt-4o-mini"
    cost, row = token_ledger.price("gpt-4o-mini-2024-07-18", 1_000_000, 0, token_ledger.load_rates())
    assert cost == pytest.approx(0.15)
    assert row["provider"] == "openai"


def test_handler_prices_a_snapshot_model_but_keeps_the_reported_name():
    handler = LangChainUsageHandler(label="judge-run", model="gpt-4o-mini")
    handler.on_llm_end(chat_result(llm_output={
        "token_usage": {"prompt_tokens": 100, "completion_tokens": 10},
        "model_name": "gpt-4o-mini-2024-07-18",
    }))
    entry = ledger_entries()[0]
    assert entry["model"] == "gpt-4o-mini-2024-07-18"
    assert entry["cost_usd"] is not None
    assert entry["unpriced_reason"] is None


def test_budget_is_enforced_mid_run():
    """The handler sees every judge call, so the ceiling stops the run rather than reporting late."""
    handler = LangChainUsageHandler(model="gpt-4o-mini", max_cost_usd=0.00001)
    with pytest.raises(token_ledger.BudgetExceeded) as caught:
        handler.on_llm_end(chat_result(llm_output={
            "token_usage": {"prompt_tokens": 1_000_000, "completion_tokens": 0},
        }))
    assert handler.exceeded is True
    assert caught.value.spent_usd > caught.value.limit_usd


def test_budget_not_exceeded_does_not_raise():
    handler = LangChainUsageHandler(model="gpt-4o-mini", max_cost_usd=1.0)
    handler.on_llm_end(chat_result(llm_output={"token_usage": {"prompt_tokens": 10, "completion_tokens": 2}}))
    assert handler.exceeded is False
