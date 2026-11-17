"""Run the live-mode acceptance gate against a running service.

Uses the real gate code: `candidate_gate.evaluate()` dispatches on the dataset's `execution_mode`,
so this exercises the same path a release would. Exits non-zero when the gate fails.

The useful comparison is the same command against a service in mock mode: the dataset is identical,
but the answers come from the deterministic fallback, and the gate must reject them.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts" / "release"))

import candidate_gate  # noqa: E402

DATASET = REPO / "scripts" / "release" / "acceptance-live.json"
OUT = Path(os.environ.get("OUT_DIR", REPO / "artifacts" / "experiments"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Live-mode structural acceptance gate.")
    parser.add_argument("--rag-url", default=os.environ.get("RAG_API_URL", "http://rag-api:8000"))
    parser.add_argument("--label", default="live")
    parser.add_argument("--run-label", default=None,
                        help="Attribute token spend to this run; defaults to the label")
    args = parser.parse_args()

    try:
        report = candidate_gate.evaluate(
            args.rag_url,
            str(DATASET),
            {"provenance": "live_gate_check", "label": args.label},
            run_label=args.run_label or args.label,
        )
    except ValueError as exc:
        print(json.dumps({"label": args.label, "gate_passed": False, "error": str(exc)}, indent=2))
        return 1

    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"live-gate-{args.label}.json"
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    gate = report["gate"]
    print(json.dumps({
        "label": args.label,
        "mechanism": report["execution_mode"],
        "policy": gate["policy_id"],
        "gate_passed": gate["passed"],
        "pass_ratio": gate["pass_ratio"],
        "cases": [
            {"case": row["case_id"], "mode": row["generation_mode"],
             "checks": row["checks"]}
            for row in report["rows"]
        ],
        "hard_failures": gate["hard_failures"],
        "receipt": str(path),
    }, indent=2))
    return 0 if gate["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
