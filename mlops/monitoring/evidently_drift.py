"""Drift detection for the RAG embedding space (production vs reference window).

This is the operator-facing MLOps tool that would run periodically (CronJob) and push the
p-value into Prometheus via a push-gateway or expose it on /metrics of a sidecar.

Runs with zero external dependencies so it is safe to use in CI/demos.
"""
from __future__ import annotations

import argparse
import json
import math
import random
import statistics
from pathlib import Path


def _rng(seed: int) -> random.Random:
    return random.Random(seed)


def _window_mean_norm(vectors: list[list[float]]) -> float:
    return statistics.fmean(math.sqrt(sum(v * v for v in vec)) for vec in vectors)


def kolmogorov_smirnov_two_sample(a: list[float], b: list[float], n_bootstrap: int = 10_000) -> float:
    """Approximate two-sample KS p-value via bootstrap resampling."""
    combined = a + b
    observed = max(
        abs(sum(1 for x in a if x <= t) / len(a) - sum(1 for x in b if x <= t) / len(b))
        for t in combined
    )

    rng = _rng(42)
    count = 0
    for _ in range(n_bootstrap):
        shuffled = combined[:]
        rng.shuffle(shuffled)
        a_s = shuffled[: len(a)]
        b_s = shuffled[len(a):]
        boot = max(
            abs(sum(1 for x in a_s if x <= t) / len(a_s) - sum(1 for x in b_s if x <= t) / len(b_s))
            for t in combined
        )
        if boot >= observed:
            count += 1
    return count / n_bootstrap


def main() -> int:
    parser = argparse.ArgumentParser(description="Embedding drift detection")
    parser.add_argument("--reference", type=Path, required=True, help="reference window vectors")
    parser.add_argument("--current", type=Path, required=True, help="current production window")
    parser.add_argument("--alpha", type=float, default=0.05)
    args = parser.parse_args()

    reference = [json.loads(line)["embedding"] for line in args.reference.open()]
    current = [json.loads(line)["embedding"] for line in args.current.open()]

    ref_norms = [_window_mean_norm([v]) for v in reference]
    cur_norms = [_window_mean_norm([v]) for v in current]

    p_value = kolmogorov_smirnov_two_sample(ref_norms, cur_norms)
    drifted = p_value < args.alpha

    print(
        json.dumps(
            {
                "reference_count": len(reference),
                "current_count": len(current),
                "p_value": round(p_value, 4),
                "alpha": args.alpha,
                "drifted": drifted,
            },
            indent=2,
        )
    )
    return 1 if drifted else 0


if __name__ == "__main__":
    raise SystemExit(main())