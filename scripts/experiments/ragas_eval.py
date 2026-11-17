"""Run RAGAS over live answers produced by the running service.

Ingests a small evaluation set, asks the service for answers, then scores them with the RAGAS
library. Refuses to score deterministic fallback output: scores over mock answers would describe a
template, not a model — the same trap O02 documented when a stored dataset answer was being scored
instead of the application's response.

Requires a running service in provider mode. See scripts/experiments/README.md.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "services" / "evaluator"))

DATASET = REPO / "services" / "evaluator" / "datasets" / "ragas_set.jsonl"
OUT = Path(os.environ.get("OUT_DIR", REPO / "artifacts" / "experiments"))


def post(base: str, route: str, payload: dict) -> dict:
    request = urllib.request.Request(
        base + route,
        data=json.dumps(payload).encode(),
        headers={"content-type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.load(response)


def ingest_all(base: str, records: list[dict]) -> None:
    for record in records:
        try:
            post(base, "/ingest", {"id": record["id"], "content": record["document"]})
        except urllib.error.HTTPError as exc:
            if exc.code != 409:  # already present is fine
                raise


def collect(base: str, records: list[dict], top_k: int) -> list[dict]:
    rows = []
    for record in records:
        search = post(base, "/search", {"query": record["question"], "top_k": top_k})
        answer = post(base, "/rag", {"query": record["question"], "top_k": top_k})
        if answer.get("generation_mode") != "provider":
            raise SystemExit(
                f"refusing to score: case {record['id']} answered in "
                f"{answer.get('generation_mode')!r} mode. RAGAS over deterministic fallback "
                "describes the template, not the model."
            )
        rows.append(
            {
                "case_id": record["id"],
                "split": record.get("split"),
                "question": record["question"],
                "contexts": [item["content"] for item in search["results"]],
                "retrieved_ids": [item["id"] for item in search["results"]],
                "answer": answer["answer"],
                "sources": answer.get("sources"),
                "reference": record["reference"],
                "generation_mode": answer.get("generation_mode"),
                "model": answer.get("model"),
            }
        )
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="RAGAS scoring over live service answers.")
    parser.add_argument("--rag-url", default=os.environ.get("RAG_API_URL", "http://rag-api:8000"))
    parser.add_argument("--judge-model", default=os.environ.get("JUDGE_MODEL", "gpt-4o-mini"))
    parser.add_argument("--judge-base-url", default=os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"))
    parser.add_argument("--embedding-model", default="text-embedding-3-small")
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--label", default="ragas")
    args = parser.parse_args()

    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        print("refusing to run: OPENAI_API_KEY is not set", file=sys.stderr)
        return 2

    from evaluator.ragas_eval import score  # noqa: E402  (import after sys.path setup)

    raw = DATASET.read_text(encoding="utf-8")
    records = [json.loads(line) for line in raw.splitlines() if line.strip()]

    started = time.time()
    ingest_all(args.rag_url, records)
    rows = collect(args.rag_url, records, args.top_k)

    result = score(
        rows,
        judge_model=args.judge_model,
        judge_base_url=args.judge_base_url,
        judge_api_key=api_key,
        embedding_model=args.embedding_model,
    )

    by_id = {sample["case_id"]: sample for sample in result["per_sample"]}
    for row in rows:
        row["scores"] = {k: v for k, v in by_id.get(row["case_id"], {}).items() if k != "case_id"}

    aggregate = result["aggregate"]
    receipt = {
        "run_id": "ragas-" + time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()),
        "grade": "E4",
        "label": args.label,
        "judge": {"model": args.judge_model, "base_url": args.judge_base_url},
        "embedding_model": args.embedding_model,
        "dataset": {
            "path": str(DATASET.relative_to(REPO)),
            "samples": len(records),
            "sha256": hashlib.sha256(raw.encode()).hexdigest(),
            "splits": sorted({r.get("split", "unknown") for r in records}),
        },
        "samples": rows,
        "aggregate": aggregate,
        "judge_usage": result["usage"],
        "metric_names_returned": result["metric_names_returned"],
        "duration_seconds": round(time.time() - started, 1),
        "limits": [
            "six samples; a demonstration of the framework, not a benchmark",
            "judge is a single model at one point in time, not calibrated against human labels",
            "scores are probabilistic; repeated runs will differ",
            "offline evaluation only — these scores do not gate promotion",
        ],
    }

    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"ragas-{args.label}.json"
    path.write_text(json.dumps(receipt, indent=2), encoding="utf-8")

    print(json.dumps({
        "label": args.label,
        "judge": args.judge_model,
        "samples": len(records),
        "aggregate": aggregate,
        "judge_usage": result["usage"],
        "duration_seconds": receipt["duration_seconds"],
        "receipt": str(path),
    }, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
