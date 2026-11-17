"""Live-provider receipt: run the acceptance fixtures through real embeddings and generation.

Produces the artefact that answers "where is the AI": which provider, which model, how many tokens,
what it cost, what it actually said, and a hash of the transcript.

Requires a provider key in the environment. Never prints or records the key.

Environment:
  DATABASE_URL        postgresql+psycopg://... (a pgvector database)
  OPENAI_API_KEY      required for embeddings
  OPENAI_BASE_URL     embedding endpoint, default https://api.openai.com/v1
  LLM_API_KEY         optional; generation key, falls back to OPENAI_API_KEY
  LLM_BASE_URL        optional; generation endpoint, falls back to OPENAI_BASE_URL
  LLM_MODEL           optional; default gpt-4o-mini
  TARGET_LABEL        optional; defaults to "live"

Accounting:
  --input-rate        USD per 1M input tokens
  --output-rate       USD per 1M output tokens
  Both are optional. Without them the receipt records token counts and reports cost as null,
  because an invented price is worse than no price.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "services" / "rag-api"))

from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

OUT = Path(os.environ.get("OUT_DIR", REPO / "artifacts" / "experiments"))
DATASET = REPO / "scripts" / "release" / "acceptance.json"
LABEL = os.environ.get("TARGET_LABEL", "live")


def main() -> int:
    parser = argparse.ArgumentParser(description="Live-provider receipt over the acceptance fixtures.")
    parser.add_argument("--input-rate", type=float, default=None, help="USD per 1M input tokens")
    parser.add_argument("--output-rate", type=float, default=None, help="USD per 1M output tokens")
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--max-cost-usd", type=float, default=0.05,
                        help="Abort when attributed spend for this run passes this ceiling")
    args = parser.parse_args()

    from app.core.config import settings  # noqa: E402
    from app.core import tokens  # noqa: E402
    from app.core.db import Base  # noqa: E402
    import app.models  # noqa: F401,E402
    from app.services.embedding import embedding_service  # noqa: E402
    from app.services.rag import RAGService  # noqa: E402
    from app.services.retrieval import RetrievalService  # noqa: E402

    if settings.embedding_mode != "openai" or not settings.openai_api_key:
        print("refusing to run: set EMBEDDING_MODE=openai and OPENAI_API_KEY", file=sys.stderr)
        return 2

    # Attribute every model call in this process to the run, so cost-per-accepted-answer can join
    # usage to outcomes by label rather than by timestamp guessing.
    tokens.set_run_label(LABEL)

    data = json.loads(DATASET.read_text(encoding="utf-8"))
    engine = create_engine(os.environ["DATABASE_URL"], future=True)
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        Base.metadata.drop_all(bind=conn)
        Base.metadata.create_all(bind=conn)
        for doc in data["documents"]:
            vector = embedding_service.embed(doc["content"])
            conn.execute(
                text("INSERT INTO documents (id, content, embedding) "
                     "VALUES (:id, :content, CAST(:vec AS vector))"),
                {"id": doc["id"], "content": doc["content"],
                 "vec": "[" + ",".join(f"{v:.8f}" for v in vector) + "]"},
            )

    rag = RAGService()
    cases = []
    total_prompt = total_completion = 0
    baseline_spend = tokens.spent()
    warned = False
    breach = None

    with Session() as session:
        retrieval = RetrievalService(session)
        for case in data["cases"]:
            started = time.time()
            qvec = embedding_service.embed(case["question"])
            contexts = retrieval.search(qvec, top_k=args.top_k, query_text=case["question"])
            answer = rag.answer(case["question"], contexts)
            elapsed = round((time.time() - started) * 1000, 1)

            usage = answer.get("usage") or {}
            total_prompt += int(usage.get("prompt_tokens") or 0)
            total_completion += int(usage.get("completion_tokens") or 0)

            required = [p for p in case.get("required_phrases", [])]
            forbidden = [p for p in case.get("forbidden_phrases", [])]
            cases.append({
                "case_id": case["id"],
                "question": case["question"],
                "retrieved_ids": [c["id"] for c in contexts],
                "expected_source": case["source_id"],
                "source_present": case["source_id"] in [c["id"] for c in contexts],
                "answer": answer["answer"],
                "sources": answer.get("sources"),
                "generation_mode": answer.get("generation_mode"),
                "fallback_reason": answer.get("fallback_reason"),
                "model": answer.get("model"),
                "usage": usage,
                "required_phrases_present": {p: (p.lower() in answer["answer"].lower()) for p in required},
                "forbidden_phrases_absent": {p: (p.lower() not in answer["answer"].lower()) for p in forbidden},
                "latency_ms": elapsed,
            })

            spent = tokens.spent() - baseline_spend
            if not warned and spent >= args.max_cost_usd * 0.5:
                print(f"token.budget warning: ${spent:.6f} of ${args.max_cost_usd:.6f} used",
                      file=sys.stderr)
                warned = True
            if spent > args.max_cost_usd:
                # Stop here rather than after the run: the point of a ceiling is that the next call
                # never happens.
                breach = {"reason": "budget_exceeded", "spent_usd": round(spent, 9),
                          "limit_usd": args.max_cost_usd, "after_case": case["id"]}
                break

    transcript = "\n---\n".join(f"{c['case_id']}: {c['answer']}" for c in cases)
    cost = None
    if args.input_rate is not None and args.output_rate is not None:
        cost = round((total_prompt / 1_000_000) * args.input_rate
                     + (total_completion / 1_000_000) * args.output_rate, 6)

    receipt = {
        "run_id": "live-" + time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()),
        "grade": "E4",
        "label": LABEL,
        "embedding": {
            "mode": settings.embedding_mode,
            "base_url": settings.openai_base_url,
            "model": settings.embedding_model,
            "dimensions": settings.embedding_dim,
        },
        "generation": {
            "base_url": rag.base_url,
            "model": rag.model,
            "temperature": 0.0,
        },
        "cases": cases,
        "totals": {
            "prompt_tokens": total_prompt,
            "completion_tokens": total_completion,
            "total_tokens": total_prompt + total_completion,
            "estimated_cost_usd": cost,
            "rate_table": None if cost is None else {
                "input_usd_per_million": args.input_rate,
                "output_usd_per_million": args.output_rate,
                "source": "operator supplied for this run; verify before citing",
            },
        },
        "transcript_sha256": hashlib.sha256(transcript.encode()).hexdigest(),
        "budget": {"limit_usd": args.max_cost_usd,
                   "spent_usd": round(tokens.spent() - baseline_spend, 9),
                   "exceeded": breach is not None},
        "aborted": breach,
        "limits": [
            "one provider and one model at one point in time",
            "three synthetic fixtures; not a benchmark",
            "temperature 0 does not make a model deterministic across runs",
            "binary acceptance predicates only; semantic quality is not assessed here",
        ],
    }

    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"live-{LABEL}.json"
    path.write_text(json.dumps(receipt, indent=2), encoding="utf-8")

    print(json.dumps({
        "label": LABEL,
        "embedding": receipt["embedding"],
        "generation": receipt["generation"],
        "tokens": receipt["totals"],
        "budget": receipt["budget"],
        "aborted": breach,
        "transcript_sha256": receipt["transcript_sha256"][:16],
        "cases": [
            {"case": c["case_id"], "mode": c["generation_mode"],
             "source_present": c["source_present"],
             "required_ok": all(c["required_phrases_present"].values()),
             "answer_chars": len(c["answer"])}
            for c in cases
        ],
        "receipt": str(path),
    }, indent=2))
    return 0 if breach is None else 2


if __name__ == "__main__":
    sys.exit(main())
