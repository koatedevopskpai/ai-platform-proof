"""Token economics: attribution, pricing and rollup.

These guard the properties that make a cost ledger trustworthy: every record carries attribution,
cost is computed from a versioned rate table, and a model that cannot be priced is reported loudly
rather than silently excluded from the total.
"""
from pathlib import Path

import pytest

from app.core import tokens


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    path = tmp_path / "ledger.jsonl"
    monkeypatch.setenv("TOKEN_LEDGER_PATH", str(path))
    return path


def test_rate_table_loads_and_declares_a_version():
    table = tokens.load_rates()
    assert table["version"]
    assert "gpt-4o-mini" in table["models"]


def test_rate_table_rejects_a_row_missing_a_rate(tmp_path, monkeypatch):
    path = tmp_path / "rates.json"
    path.write_text('{"version":"v1","models":{"m":{"provider":"x","input_per_million":1.0,"checked":"today"}}}')
    monkeypatch.setenv("TOKEN_RATES_PATH", str(path))
    with pytest.raises(ValueError):
        tokens.load_rates()


def test_price_for_a_known_model():
    cost, row = tokens.price("gpt-4o-mini", 1_000_000, 0)
    assert cost == pytest.approx(0.15)
    assert row["provider"] == "openai"


def test_price_includes_completion_tokens():
    cost, _ = tokens.price("gpt-4o-mini", 0, 1_000_000)
    assert cost == pytest.approx(0.60)


def test_unknown_model_has_no_price():
    cost, row = tokens.price("not-a-model", 100, 100)
    assert cost is None and row is None


def test_record_attributes_the_call_and_prices_it(ledger):
    entry = tokens.record(
        component="rag-api", operation="rag.answer", model="gpt-4o-mini",
        usage={"prompt_tokens": 400, "completion_tokens": 20, "total_tokens": 420},
    )
    assert entry["total_tokens"] == 420
    assert entry["operation"] == "rag.answer"
    assert entry["component"] == "rag-api"
    assert entry["rate_version"] == tokens.load_rates()["version"]
    assert entry["cost_usd"] == pytest.approx((400 / 1_000_000) * 0.15 + (20 / 1_000_000) * 0.60)
    assert ledger.exists() and len(ledger.read_text().splitlines()) == 1


def test_unpriced_model_is_recorded_with_a_reason_not_dropped(ledger):
    entry = tokens.record(
        component="rag-api", operation="rag.answer", model="mystery-model",
        usage={"prompt_tokens": 10, "completion_tokens": 5},
    )
    assert entry["cost_usd"] is None
    assert "absent from rate table" in entry["unpriced_reason"]
    assert len(ledger.read_text().splitlines()) == 1


def test_endpoint_comes_from_the_request_context(ledger):
    tokens.set_endpoint("/rag")
    try:
        entry = tokens.record(component="rag-api", operation="rag.answer",
                              model="gpt-4o-mini", usage={"total_tokens": 5})
    finally:
        tokens.set_endpoint(None)
    assert entry["endpoint"] == "/rag"


def test_rollup_groups_and_sums(ledger):
    tokens.record(component="rag-api", operation="rag.answer", model="gpt-4o-mini",
                  usage={"prompt_tokens": 100, "completion_tokens": 10})
    tokens.record(component="rag-api", operation="rag.answer", model="gpt-4o-mini",
                  usage={"prompt_tokens": 200, "completion_tokens": 20})
    tokens.record(component="rag-api", operation="embedding.embed", model="text-embedding-3-small",
                  usage={"prompt_tokens": 50})

    result = tokens.rollup("operation")
    by_key = {row["key"]: row for row in result["rows"]}
    assert by_key["rag.answer"]["calls"] == 2
    assert by_key["rag.answer"]["total_tokens"] == 330
    assert by_key["embedding.embed"]["calls"] == 1
    assert result["total_cost_usd"] > 0


def test_rollup_reports_unpriced_calls_separately(ledger):
    tokens.record(component="rag-api", operation="rag.answer", model="mystery-model",
                  usage={"prompt_tokens": 10})
    result = tokens.rollup("model")
    assert result["unpriced_calls"] == 1
    assert result["total_cost_usd"] == 0


def test_rollup_rejects_an_unknown_dimension(ledger):
    with pytest.raises(ValueError):
        tokens.rollup("not_a_dimension")


def test_embedding_call_records_its_token_usage(ledger, monkeypatch):
    """Embedding spend was previously invisible; the ledger is what makes it attributable."""
    import httpx
    from app.core.config import settings
    from app.services.embedding import EmbeddingService

    monkeypatch.setattr(settings, "embedding_mode", "openai")
    monkeypatch.setattr(settings, "openai_api_key", "fixture-only")
    monkeypatch.setattr(settings, "openai_base_url", "http://fixture-provider/v1")

    payload = {"data": [{"index": 0, "embedding": [0.01] * 384}],
               "usage": {"prompt_tokens": 12, "total_tokens": 12}}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def post(self, *args, **kwargs):
            return httpx.Response(200, json=payload,
                                  request=httpx.Request("POST", "http://fixture-provider/v1/embeddings"))

    monkeypatch.setattr(httpx, "Client", FakeClient)
    EmbeddingService().embed_texts(["refund policy"])

    lines = Path(ledger).read_text().splitlines()
    assert len(lines) == 1
    import json
    entry = json.loads(lines[0])
    assert entry["operation"] == "embedding.embed"
    assert entry["prompt_tokens"] == 12
    assert entry["model"] == settings.embedding_model


def test_cost_endpoint_groups_by_model(ledger):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.api.routes import router

    tokens.record(component="rag-api", operation="rag.answer", model="gpt-4o-mini",
                  usage={"prompt_tokens": 100, "completion_tokens": 10})

    app = FastAPI()
    app.include_router(router)
    response = TestClient(app).get("/costs", params={"group_by": "model"})
    assert response.status_code == 200
    body = response.json()
    assert body["group_by"] == "model"
    assert body["rows"][0]["key"] == "gpt-4o-mini"


def test_cost_endpoint_rejects_an_unknown_dimension():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.api.routes import router

    app = FastAPI()
    app.include_router(router)
    assert TestClient(app).get("/costs", params={"group_by": "nope"}).status_code == 400

EXPECTED_FIELDS = {
    "event", "ts_utc", "trace_id", "workload", "environment", "component", "operation",
    "endpoint", "model", "provider", "mode", "prompt_tokens", "completion_tokens",
    "total_tokens", "rate_version", "input_per_million", "output_per_million",
    "cost_usd", "unpriced_reason", "label",
}


def test_record_contains_exactly_the_contract_fields(ledger):
    """Mirrors the evaluator's contract test.

    Two services write token records. Extracting a shared package would change both Docker build
    contexts, so the schema is held in place by an identical assertion on each side: if one writer
    drifts, its own suite fails rather than the two silently producing different ledgers.
    """
    entry = tokens.record(component="rag-api", operation="rag.answer", model="gpt-4o-mini",
                          usage={"prompt_tokens": 1, "completion_tokens": 1}, label="contract")
    assert set(entry) == EXPECTED_FIELDS


def test_a_dated_snapshot_prices_as_its_alias():
    """Providers echo the resolved snapshot; the rate table keys on the alias."""
    assert tokens.normalise_model("gpt-4o-mini-2024-07-18") == "gpt-4o-mini"
    cost, row = tokens.price("gpt-4o-mini-2024-07-18", 1_000_000, 0)
    assert cost == pytest.approx(0.15)
    assert row["provider"] == "openai"


def test_snapshot_is_priced_but_recorded_as_reported(ledger):
    entry = tokens.record(component="rag-api", operation="rag.answer",
                          model="gpt-4o-mini-2024-07-18", usage={"prompt_tokens": 10})
    assert entry["model"] == "gpt-4o-mini-2024-07-18"
    assert entry["cost_usd"] is not None
    assert entry["unpriced_reason"] is None


def test_run_label_attributes_the_record(ledger):
    tokens.set_run_label("gate-run-42")
    try:
        entry = tokens.record(component="rag-api", operation="rag.answer",
                              model="gpt-4o-mini", usage={"prompt_tokens": 5})
    finally:
        tokens.set_run_label(None)
    assert entry["label"] == "gate-run-42"


def test_an_explicit_label_wins_over_the_run_context(ledger):
    tokens.set_run_label("from-header")
    try:
        entry = tokens.record(component="rag-api", operation="rag.answer",
                              model="gpt-4o-mini", usage={"prompt_tokens": 5}, label="explicit")
    finally:
        tokens.set_run_label(None)
    assert entry["label"] == "explicit"


def test_a_malformed_run_label_is_dropped_not_stored(ledger):
    for bad in ("has spaces", "x" * 65, "", "semi;colon", "quote\"mark"):
        tokens.set_run_label(bad)
        try:
            entry = tokens.record(component="rag-api", operation="rag.answer",
                                  model="gpt-4o-mini", usage={"prompt_tokens": 5})
        finally:
            tokens.set_run_label(None)
        assert entry["label"] is None, bad


def test_absent_run_label_records_null(ledger):
    tokens.set_run_label(None)
    entry = tokens.record(component="rag-api", operation="rag.answer",
                          model="gpt-4o-mini", usage={"prompt_tokens": 5})
    assert entry["label"] is None
