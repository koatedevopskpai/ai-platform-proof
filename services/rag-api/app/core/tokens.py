"""Token economics: attribute model spend to the resource that caused it.

The cloud analogue is FinOps tagging — every instance carries cost-centre, workload and owner so
spend can be attributed. Model calls are no different, except the resource being consumed is tokens
rather than instance-hours. Every provider call therefore emits one `token.usage` record carrying:

* **attribution** — workload, environment, component, operation, endpoint, model, provider, mode
* **measurement** — prompt, completion and total tokens
* **pricing** — the rate used, its table version, and the computed cost

Storing the rate alongside the record is deliberate. If a provider changes prices next month, last
month's entries must still report what they actually cost.

An unknown model is recorded with `cost_usd: null` and a reason, never dropped. A ledger that
silently omits the calls it cannot price is worse than no ledger, because the total looks complete.

Only provider calls are recorded. Mock calls consume nothing, and mixing zero-cost rows into a cost
ledger makes every average harder to read.
"""
from __future__ import annotations

from contextvars import ContextVar
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from threading import Lock

from app.core.config import settings
from app.core.telemetry import current_trace_id

DIMENSIONS = (    "workload",
    "environment",
    "component",
    "operation",
    "endpoint",
    "model",
    "provider",
    "mode",
    "rate_version",
)

_endpoint: ContextVar[str | None] = ContextVar("token_endpoint", default=None)
_lock = Lock()

_RATE_TABLE_ENV = "TOKEN_RATES_PATH"
_LEDGER_ENV = "TOKEN_LEDGER_PATH"
_DEFAULT_LEDGER = "artifacts/token-ledger.jsonl"


def set_endpoint(value: str | None) -> None:
    """Attribute subsequent calls to the HTTP route handling this request."""
    _endpoint.set(value)


def current_endpoint() -> str | None:
    return _endpoint.get()


def rates_path() -> Path:
    override = os.environ.get(_RATE_TABLE_ENV)
    return Path(override) if override else Path(__file__).with_name("token_rates.json")


def ledger_path() -> Path:
    return Path(os.environ.get(_LEDGER_ENV, _DEFAULT_LEDGER))


def load_rates(path: Path | None = None) -> dict:
    table = json.loads((path or rates_path()).read_text(encoding="utf-8"))

    if not isinstance(table.get("version"), str) or not table["version"]:
        raise ValueError("rate table requires a version")
    models = table.get("models")
    if not isinstance(models, dict) or not models:
        raise ValueError("rate table requires at least one model")

    for name, row in models.items():
        for key in ("provider", "input_per_million", "output_per_million", "checked"):
            if key not in row:
                raise ValueError(f"rate row {name!r} is missing {key}")
        for key in ("input_per_million", "output_per_million"):
            value = row[key]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
                raise ValueError(f"rate row {name!r} has invalid {key}: {value!r}")

    return table


def price(model: str, prompt_tokens: int, completion_tokens: int,
          table: dict | None = None) -> tuple[float | None, dict | None]:
    """Cost in USD, or (None, None) when the model is absent from the table."""
    table = table or load_rates()
    row = table["models"].get(model)
    if row is None:
        return None, None
    cost = (prompt_tokens / 1_000_000) * row["input_per_million"] \
        + (completion_tokens / 1_000_000) * row["output_per_million"]
    return round(cost, 9), row


def infer_provider(model: str, base_url: str = "") -> str:
    table = load_rates()
    row = table["models"].get(model)
    if row:
        return row["provider"]
    # Unknown models still need an attribution value rather than null.
    return "deepseek" if "deepseek" in (base_url or "").lower() else "openai"


def record(*, component: str, operation: str, model: str, usage: dict | None,
           mode: str = "provider", endpoint: str | None = None,
           trace_id: str | None = None, base_url: str = "") -> dict:
    """Emit and persist one attribution record. Returns the record for testing."""
    prompt = int((usage or {}).get("prompt_tokens") or 0)
    completion = int((usage or {}).get("completion_tokens") or 0)

    table = load_rates()
    cost, row = price(model, prompt, completion, table)

    entry = {
        "event": "token.usage",
        "ts_utc": datetime.now(timezone.utc).isoformat(),
        "trace_id": trace_id or current_trace_id(),
        "workload": settings.workload,
        "environment": settings.environment,
        "component": component,
        "operation": operation,
        "endpoint": endpoint or current_endpoint(),
        "model": model,
        "provider": infer_provider(model, base_url),
        "mode": mode,
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": prompt + completion,
        "rate_version": table["version"] if row else None,
        "input_per_million": row["input_per_million"] if row else None,
        "output_per_million": row["output_per_million"] if row else None,
        "cost_usd": cost,
        "unpriced_reason": None if row else f"model {model!r} absent from rate table {table['version']}",
    }

    print(json.dumps(entry), flush=True)

    path = ledger_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with _lock, path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry) + "\n")

    return entry


def rollup(group_by: str = "operation", path: Path | None = None) -> dict:
    """Aggregate the ledger by one attribution dimension.

    `unpriced_calls` is reported separately and is deliberately loud: a total that quietly excludes
    calls it could not price is misleading.
    """
    if group_by not in DIMENSIONS:
        raise ValueError(f"group_by must be one of: {', '.join(DIMENSIONS)}")

    path = path or ledger_path()
    buckets: dict[str, dict] = {}
    unpriced = 0
    lines = 0

    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            entry = json.loads(line)
            if entry.get("event") != "token.usage":
                continue
            lines += 1
            key = entry.get(group_by) or "(none)"
            bucket = buckets.setdefault(key, {
                "key": key, "calls": 0, "prompt_tokens": 0,
                "completion_tokens": 0, "total_tokens": 0, "cost_usd": 0.0,
            })
            bucket["calls"] += 1
            for field in ("prompt_tokens", "completion_tokens", "total_tokens"):
                bucket[field] += int(entry.get(field) or 0)
            if entry.get("cost_usd") is None:
                unpriced += 1
            else:
                bucket["cost_usd"] = round(bucket["cost_usd"] + float(entry["cost_usd"]), 9)

    rows = sorted(buckets.values(), key=lambda item: (-item["cost_usd"], str(item["key"])))
    return {
        "group_by": group_by,
        "entries": lines,
        "unpriced_calls": unpriced,
        "total_cost_usd": round(sum(item["cost_usd"] for item in rows), 9),
        "rows": rows,
    }
