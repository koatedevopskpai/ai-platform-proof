"""Token ledger writer for evaluator-side model calls.

Mirrors `services/rag-api/app/core/tokens.py`. The duplication is deliberate: extracting one shared
package would change both Docker build contexts, and `release.py` builds from `services/<name>`, so
the existing O02 release receipts would no longer describe the build. Two writers against one schema
is the smaller risk.

Both writers are held to the same field list by a test that asserts the exact key set. If one drifts,
that service's suite fails.

The price table is **not** duplicated. Set `TOKEN_RATES_PATH` to the canonical table
(`services/rag-api/app/core/token_rates.json`). Without it, records are written unpriced with a reason
rather than priced from a stale copy — prices drifting between services would be worse than a missing
cost.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
from threading import Lock

# The contract. Kept identical to the rag-api write path; asserted by test_ledger_contract.py in both
# services.
FIELDS = (
    "event", "ts_utc", "trace_id", "workload", "environment", "component", "operation",
    "endpoint", "model", "provider", "mode", "prompt_tokens", "completion_tokens",
    "total_tokens", "rate_version", "input_per_million", "output_per_million",
    "cost_usd", "unpriced_reason", "label",
)

_lock = Lock()


def ledger_path() -> Path:
    return Path(os.environ.get("TOKEN_LEDGER_PATH", "artifacts/token-ledger.jsonl"))


def rates_path() -> Path | None:
    override = os.environ.get("TOKEN_RATES_PATH")
    return Path(override) if override else None


def load_rates(path: Path | None = None) -> dict | None:
    """Return the rate table, or None when none is configured.

    Absence is not an error here: the evaluator should still record that a call happened and that it
    could not be priced. It is an error when a *file is present but malformed*, which raises.
    """
    path = path or rates_path()
    if path is None or not path.exists():
        return None
    table = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(table.get("version"), str) or not table["version"]:
        raise ValueError("rate table requires a version")
    if not isinstance(table.get("models"), dict) or not table["models"]:
        raise ValueError("rate table requires at least one model")
    return table


_SNAPSHOT_SUFFIX = re.compile(r"-\d{4}-\d{2}-\d{2}$")


def normalise_model(model: str) -> str:
    """Strip a dated snapshot suffix so pricing resolves to the family alias.

    Providers echo back the resolved snapshot — `gpt-4o-mini-2024-07-18` — while rate tables are keyed
    on the alias. Attribution keeps the reported name, because that is what actually ran; pricing
    resolves it to the family, because that is what the price list covers.
    """
    return _SNAPSHOT_SUFFIX.sub("", model or "")


def price(model: str, prompt_tokens: int, completion_tokens: int,
          table: dict | None) -> tuple[float | None, dict | None]:
    if not table:
        return None, None
    row = table["models"].get(model) or table["models"].get(normalise_model(model))
    if row is None:
        return None, None
    cost = (prompt_tokens / 1_000_000) * row["input_per_million"] \
        + (completion_tokens / 1_000_000) * row["output_per_million"]
    return round(cost, 9), row


def infer_provider(model: str, table: dict | None, base_url: str = "") -> str:
    if table:
        row = table["models"].get(model) or table["models"].get(normalise_model(model))
        if row:
            return row["provider"]
    return "deepseek" if "deepseek" in (base_url or "").lower() else "openai"


def record(*, component: str, operation: str, model: str, usage: dict | None,
           mode: str = "provider", endpoint: str | None = None,
           trace_id: str | None = None, base_url: str = "",
           label: str | None = None, table: dict | None = None,
           reason_override: str | None = None) -> dict:
    """Append one attribution record. Returns it so tests and callers can assert.

    ``reason_override`` marks a record whose cost is unknown for a reason other than an absent price —
    for example a provider wrapper that did not report usage. The cost is written as null in the same
    pass; mutating the returned dict afterwards would leave the ledger holding a misleading zero.
    """
    prompt = int((usage or {}).get("prompt_tokens") or 0)
    completion = int((usage or {}).get("completion_tokens") or 0)

    table = table if table is not None else load_rates()
    cost, row = price(model, prompt, completion, table)

    if reason_override:
        cost, reason = None, reason_override
    elif row:
        reason = None
    elif not table:
        reason = "no rate table configured (TOKEN_RATES_PATH unset)"
    else:
        reason = f"model {model!r} absent from rate table {table['version']}"

    entry = {
        "event": "token.usage",
        "ts_utc": datetime.now(timezone.utc).isoformat(),
        "trace_id": trace_id,
        "workload": os.environ.get("WORKLOAD", "ai-platform-proof"),
        "environment": os.environ.get("ENVIRONMENT", "local-dev"),
        "component": component,
        "operation": operation,
        "endpoint": endpoint,
        "model": model,
        "provider": infer_provider(model, table, base_url),
        "mode": mode,
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": prompt + completion,
        "rate_version": table["version"] if row else None,
        "input_per_million": row["input_per_million"] if row else None,
        "output_per_million": row["output_per_million"] if row else None,
        "cost_usd": cost,
        "unpriced_reason": reason,
        "label": label,
    }

    print(json.dumps(entry), flush=True)

    path = ledger_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with _lock, path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry) + "\n")

    return entry


DIMENSIONS = (
    "workload", "environment", "component", "operation", "endpoint",
    "model", "provider", "mode", "rate_version", "label",
)


def rollup(group_by: str = "component", path: Path | None = None) -> dict:
    if group_by not in DIMENSIONS:
        raise ValueError(f"group_by must be one of: {', '.join(DIMENSIONS)}")

    path = path or ledger_path()
    buckets: dict[str, dict] = {}
    unpriced = 0
    entries = 0

    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            entry = json.loads(line)
            if entry.get("event") != "token.usage":
                continue
            entries += 1
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
        "entries": entries,
        "unpriced_calls": unpriced,
        "total_cost_usd": round(sum(item["cost_usd"] for item in rows), 9),
        "rows": rows,
    }
