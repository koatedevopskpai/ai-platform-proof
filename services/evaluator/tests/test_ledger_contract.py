"""The ledger field contract.

Two services write token records: `rag-api/app/core/tokens.py` and `evaluator/token_ledger.py`. They
are separate modules because extracting a shared package would change both Docker build contexts, and
`release.py` builds from `services/<name>` — the existing release receipts would stop describing the
build.

The duplication is held in place by this test on each side: both assert the exact field set. If one
writer drifts, that service's suite fails rather than the two silently producing different ledgers.
"""
from evaluator import token_ledger

EXPECTED_FIELDS = {
    "event", "ts_utc", "trace_id", "workload", "environment", "component", "operation",
    "endpoint", "model", "provider", "mode", "prompt_tokens", "completion_tokens",
    "total_tokens", "rate_version", "input_per_million", "output_per_million",
    "cost_usd", "unpriced_reason", "label",
}


def test_module_declares_the_contract():
    assert set(token_ledger.FIELDS) == EXPECTED_FIELDS


def test_a_record_contains_exactly_the_contract_fields():
    entry = token_ledger.record(
        component="evaluator",
        operation="eval.judge",
        model="gpt-4o-mini",
        usage={"prompt_tokens": 1, "completion_tokens": 1},
        label="contract",
    )
    assert set(entry) == EXPECTED_FIELDS


def test_all_grouping_dimensions_are_record_fields():
    assert set(token_ledger.DIMENSIONS) <= EXPECTED_FIELDS
