"""Structural acceptance predicates for live-provider candidates.

A live answer cannot be checked by token containment: the deterministic echo is a substring of its
contexts, a model answer is not. Mock mode therefore keeps its binary fixture predicates, and live
mode uses this separate set — the dual-predicate decision recorded in the briefing document.

What live mode gates on is deliberately **not** a model judge. Measured on 17 November 2026, a judge
scored an answer character-identical to its source 0.333 faithful (evidence/ragas-eval.md), so a gate
built on those scores would block correct work. Live mode gates on properties that are deterministic
and cheap:

* the answer came from a provider, not the deterministic fallback
* the provider returned token usage, proving the call was real
* the citation set matches the retrieved set exactly, and the expected source is cited
* fixture phrases are present and forbidden phrases absent

Quality remains a reported measurement, never a promotion decision.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

CHECKS = (
    "provider_answer",
    "not_fallback",
    "usage_present",
    "sources_valid",
    "required_phrases",
    "forbidden_phrases",
)


def load_policy(path=None) -> dict:
    path = Path(path) if path else Path(__file__).with_name("gate-policy-live.json")
    policy = json.loads(path.read_text(encoding="utf-8"))

    if not isinstance(policy.get("policy_id"), str) or not policy["policy_id"]:
        raise ValueError("policy_id is required")

    required = policy.get("required_checks")
    if not isinstance(required, list) or not required:
        raise ValueError("required_checks must be a non-empty list")
    unknown = [name for name in required if name not in CHECKS]
    if unknown:
        raise ValueError(f"unknown required check(s): {', '.join(sorted(unknown))}")

    ratio = policy.get("min_pass_ratio")
    if isinstance(ratio, bool) or not isinstance(ratio, (int, float)) or not 0 < ratio <= 1:
        raise ValueError("min_pass_ratio must be in (0, 1]")

    return policy


def case_checks(case: dict, corpus: dict, search: dict, answer: dict) -> dict:
    """Structural predicates for one case, from the actual responses."""
    results = search.get("results") or []
    ids = [item.get("id") for item in results]
    contents = [item.get("content") for item in results]
    sources = answer.get("sources")
    usage = answer.get("usage") or {}
    text = answer.get("answer") or ""

    return {
        "provider_answer": answer.get("generation_mode") == "provider",
        "not_fallback": answer.get("fallback") is False and answer.get("blocked") is not True,
        "usage_present": isinstance(usage.get("total_tokens"), int) and usage["total_tokens"] > 0,
        "sources_valid": bool(ids)
        and len(ids) == len(set(ids))
        and all(identifier in corpus and content == corpus.get(identifier)
                for identifier, content in zip(ids, contents))
        and sources == ids
        and case.get("source_id") in ids,
        "required_phrases": bool(text.strip())
        and all(phrase.lower() in text.lower() for phrase in case.get("required_phrases") or []),
        "forbidden_phrases": all(phrase.lower() not in text.lower()
                                 for phrase in case.get("forbidden_phrases") or []),
    }


def assess(rows: list[dict], policy: dict, expected_count: int) -> dict:
    failures: list[dict] = []
    if not rows or len(rows) != expected_count or expected_count <= 0:
        failures.append({"case": None, "reason": "missing_or_unexpected_cases"})

    passed = 0
    for row in rows:
        bad = [name for name in policy["required_checks"] if not row["checks"].get(name, False)]
        if bad:
            failures.append({"case": row["case_id"], "reason": "failed_checks", "checks": bad})
        else:
            passed += 1

    ratio = passed / expected_count if expected_count else 0.0
    if ratio < policy["min_pass_ratio"]:
        failures.append({"case": None, "reason": "pass_ratio_below_floor",
                         "observed": round(ratio, 4), "floor": policy["min_pass_ratio"]})

    return {
        "passed": not failures,
        "policy_id": policy["policy_id"],
        "pass_ratio": round(ratio, 4),
        "case_count": len(rows),
        "expected_count": expected_count,
        "hard_failures": failures,
    }


def report(rows: list[dict], policy: dict, expected_count: int, dataset_id: str, provenance) -> dict:
    return {
        "schema": 1,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "execution_mode": "live_provider_structural_acceptance",
        "sample_size": len(rows),
        "dataset_id": dataset_id,
        "provenance": provenance,
        "metric_basis": "structural and provenance predicates; no model judge",
        "rows": rows,
        "gate": assess(rows, policy, expected_count),
    }
