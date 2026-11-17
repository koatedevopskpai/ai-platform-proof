"""Unit economics from the token ledger.

Three measures, each with a denominator that has to be stated or the number is meaningless:

* **cost per accepted answer** — spend attributed to a run ÷ cases that run accepted
* **cost per 1k tokens** — spend ÷ tokens in thousands, reported **per model** because rates differ
* **spend share** — each model's contribution to the total, which must sum to 100%

Usage and outcomes are separate event types in the same ledger and join on `label`. That is why
`x-run-label` exists: without it, spend could only be matched to a run by guessing at timestamps.

Anything that cannot be joined is reported rather than dropped. Spend with no outcome and outcomes with
no spend are both facts a cost report needs to show.
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DEFAULT_LEDGER = Path(os.environ.get("TOKEN_LEDGER_PATH", "artifacts/token-ledger.jsonl"))

NO_LABEL = "(none)"


def load_ledger(path: Path) -> tuple[list[dict], list[dict]]:
    usage: list[dict] = []
    outcomes: list[dict] = []
    if not path.exists():
        return usage, outcomes

    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        entry = json.loads(line)
        if entry.get("event") == "token.usage":
            usage.append(entry)
        elif entry.get("event") == "run.outcome":
            outcomes.append(entry)
    return usage, outcomes


def cost_per_accepted(cost_usd: float | None, accepted: int | None) -> float | None:
    """Undefined, not infinite, when nothing was accepted — an infinite cost is not a useful number."""
    if cost_usd is None or not accepted:
        return None
    return round(cost_usd / accepted, 9)


def cost_per_1k_tokens(cost_usd: float | None, tokens: int | None) -> float | None:
    if cost_usd is None or not tokens:
        return None
    return round(cost_usd / (tokens / 1000), 9)


def share_by(usage: list[dict], key: str) -> list[dict]:
    """Spend share per value of one attribution field.

    Costs that could not be priced are counted separately; including them as zero would make the shares
    sum to 100% while part of the spend is unaccounted for.
    """
    totals: dict[str, dict] = {}
    unpriced = 0

    for entry in usage:
        value = entry.get(key) or NO_LABEL
        bucket = totals.setdefault(value, {"key": value, "calls": 0, "tokens": 0, "cost_usd": 0.0})
        bucket["calls"] += 1
        bucket["tokens"] += int(entry.get("total_tokens") or 0)
        if entry.get("cost_usd") is None:
            unpriced += 1
        else:
            bucket["cost_usd"] = round(bucket["cost_usd"] + float(entry["cost_usd"]), 9)

    total = sum(bucket["cost_usd"] for bucket in totals.values())
    rows = []
    for bucket in totals.values():
        bucket["share"] = round(bucket["cost_usd"] / total, 4) if total else None
        rows.append(bucket)
    rows.sort(key=lambda item: (-item["cost_usd"], str(item["key"])))
    return rows


def join_by_label(usage: list[dict], outcomes: list[dict]) -> dict[str, dict]:
    runs: dict[str, dict] = {}

    for entry in usage:
        label = entry.get("label") or NO_LABEL
        run = runs.setdefault(label, {"label": label, "calls": 0, "tokens": 0, "cost_usd": 0.0,
                                      "unpriced_calls": 0, "attempted": None, "accepted": None,
                                      "passed": None})
        run["calls"] += 1
        run["tokens"] += int(entry.get("total_tokens") or 0)
        if entry.get("cost_usd") is None:
            run["unpriced_calls"] += 1
        else:
            run["cost_usd"] = round(run["cost_usd"] + float(entry["cost_usd"]), 9)

    for entry in outcomes:
        label = entry.get("label") or NO_LABEL
        run = runs.setdefault(label, {"label": label, "calls": 0, "tokens": 0, "cost_usd": 0.0,
                                      "unpriced_calls": 0, "attempted": None, "accepted": None,
                                      "passed": None})
        run["attempted"] = entry.get("attempted")
        run["accepted"] = entry.get("accepted")
        run["passed"] = entry.get("passed")

    for run in runs.values():
        run["cost_per_accepted_usd"] = cost_per_accepted(run["cost_usd"], run["accepted"])
        run["cost_per_1k_tokens_usd"] = cost_per_1k_tokens(run["cost_usd"], run["tokens"])

    return runs


def build_report(path: Path = DEFAULT_LEDGER) -> dict:
    usage, outcomes = load_ledger(path)
    runs = join_by_label(usage, outcomes)

    with_outcome = {k: v for k, v in runs.items() if v["attempted"] is not None}
    without_outcome = {k: v for k, v in runs.items() if v["attempted"] is None}

    return {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "ledger": {"path": str(path), "usage_entries": len(usage), "outcome_entries": len(outcomes)},
        "runs": sorted(runs.values(), key=lambda r: (-r["cost_usd"], str(r["label"]))),
        "runs_with_outcome": sorted(with_outcome.values(), key=lambda r: str(r["label"])),
        "spend_without_outcome": sorted(without_outcome.values(), key=lambda r: str(r["label"])),
        "by_model": share_by(usage, "model"),
        "by_endpoint": share_by(usage, "endpoint"),
        "total_cost_usd": round(sum(r["cost_usd"] for r in runs.values()), 9),
        "limits": [
            "cost is computed from a rate table, not from an invoice",
            "fixture runs are small: one rejected case moves cost-per-accepted by a third",
            "labels are client-supplied and unauthenticated",
            "the ledger is a local JSONL file with no rotation or retention policy",
        ],
    }


def render_markdown(report: dict) -> str:
    outcomes = report["ledger"]["outcome_entries"]
    lines = [
        "# Unit economics",
        "",
        f"Generated {report['generated_utc']} from `{Path(report['ledger']['path']).as_posix()}` — "
        f"{report['ledger']['usage_entries']} usage entries, "
        f"{outcomes} outcome{'s' if outcomes != 1 else ''}.",
        "",
        f"**Total attributed spend: ${report['total_cost_usd']}**",
        "",
        "## Cost per accepted answer",
        "",
        "| Run | Calls | Tokens | Spend | Accepted | Cost / accepted | Cost / 1k tokens |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]

    if report["runs_with_outcome"]:
        for run in report["runs_with_outcome"]:
            accepted = f"{run['accepted']}/{run['attempted']}" if run["attempted"] else "—"
            per_accepted = _money(run["cost_per_accepted_usd"])
            per_1k = _money(run["cost_per_1k_tokens_usd"])
            lines.append(f"| `{run['label']}` | {run['calls']} | {run['tokens']} | "
                         f"${run['cost_usd']} | {accepted} | {per_accepted} | {per_1k} |")
    else:
        lines.append("| — | — | — | — | — | — | — |")

    lines += ["", "## Spend share by model", "",
              "| Model | Calls | Tokens | Spend | Share |", "|---|---:|---:|---:|---:|"]
    if report["by_model"]:
        for row in report["by_model"]:
            share = f"{row['share'] * 100:.1f}%" if row["share"] is not None else "—"
            lines.append(f"| `{row['key']}` | {row['calls']} | {row['tokens']} | "
                         f"${row['cost_usd']} | {share} |")
    else:
        lines.append("| — | — | — | — | — |")

    lines += ["", "## Spend share by endpoint", "",
              "| Endpoint | Calls | Tokens | Spend | Share |", "|---|---:|---:|---:|---:|"]
    if report["by_endpoint"]:
        for row in report["by_endpoint"]:
            share = f"{row['share'] * 100:.1f}%" if row["share"] is not None else "—"
            lines.append(f"| `{row['key']}` | {row['calls']} | {row['tokens']} | "
                         f"${row['cost_usd']} | {share} |")
    else:
        lines.append("| — | — | — | — | — |")

    if report["spend_without_outcome"]:
        lines += ["", "## Spend with no recorded outcome", "",
                  "Attributed to a run label that has no gate outcome. Shown rather than dropped: this is "
                  "spend a cost-per-accepted-answer calculation cannot use.", "",
                  "| Label | Calls | Tokens | Spend |", "|---|---:|---:|---:|"]
        for run in report["spend_without_outcome"]:
            lines.append(f"| `{run['label']}` | {run['calls']} | {run['tokens']} | ${run['cost_usd']} |")

    lines += ["", "## Limits", ""]
    lines += [f"- {item}" for item in report["limits"]]
    return "\n".join(lines) + "\n"


def _money(value: float | None) -> str:
    return "—" if value is None else f"${value}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Unit economics from the token ledger.")
    parser.add_argument("--ledger", default=str(DEFAULT_LEDGER))
    parser.add_argument("--json-out", default=None, help="Write the JSON report here")
    parser.add_argument("--markdown-out", default=None, help="Write the markdown report here")
    args = parser.parse_args()

    report = build_report(Path(args.ledger))
    markdown = render_markdown(report)

    if args.json_out:
        Path(args.json_out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json_out).write_text(json.dumps(report, indent=2), encoding="utf-8")
    if args.markdown_out:
        Path(args.markdown_out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.markdown_out).write_text(markdown, encoding="utf-8")

    print(json.dumps({
        "ledger": report["ledger"],
        "runs_with_outcome": len(report["runs_with_outcome"]),
        "spend_without_outcome": len(report["spend_without_outcome"]),
        "total_cost_usd": report["total_cost_usd"],
        "by_model": [{"model": r["key"], "share": r["share"]} for r in report["by_model"]],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
