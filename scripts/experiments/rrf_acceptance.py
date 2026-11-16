"""Acceptance test for the hybrid retrieval fix.

Uses the real `RetrievalService`, not a copy of its SQL, so this fails if the shipped
retrieval path regresses.

Corpus: 200 documents with embeddings decoupled from their text (a fixed seed), plus one
target document whose only distinguishing feature is a rare token. Because the embeddings
are random, the target is effectively dense-unreachable — the same query vector is sent
twice, differing only in whether `query_text` is supplied. If the keyword channel works,
the target appears in the hybrid result and not in the dense-only result.

Run with RAG_MIN_SCORE=0 to isolate the two channels; otherwise the dense floor removes
the random-vector neighbours and the comparison is less informative.

Outputs go to artifacts/experiments/ and are git-ignored.
"""
from __future__ import annotations

import json
import os
import random
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "services" / "rag-api"))

from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

OUT = Path(os.environ.get("OUT_DIR", REPO / "artifacts" / "experiments"))
TARGET = "rrf-target"
TERM = "zygomorphpolicy"
K = 4
SEED = 20261116


def randvec(rng):
    v = [rng.uniform(-1, 1) for _ in range(384)]
    n = sum(x * x for x in v) ** 0.5 or 1.0
    return [x / n for x in v]


def main() -> int:
    from app.core.db import Base  # noqa: E402
    import app.models  # noqa: F401,E402
    from app.services.retrieval import RetrievalService  # noqa: E402

    rng = random.Random(SEED)
    engine = create_engine(os.environ["DATABASE_URL"], future=True)
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    with engine.begin() as c:
        c.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        Base.metadata.drop_all(bind=c)
        Base.metadata.create_all(bind=c)
        for i in range(200):
            vec = randvec(rng)
            c.execute(
                text("INSERT INTO documents (id, content, embedding) "
                     "VALUES (:i, :t, CAST(:v AS vector))"),
                {"i": f"rrf-{i:04d}",
                 "t": f"neutral corpus note {i:04d} without distinguishing vocabulary",
                 "v": "[" + ",".join(f"{x:.8f}" for x in vec) + "]"},
            )
        vec = randvec(rng)
        c.execute(
            text("INSERT INTO documents (id, content, embedding) VALUES "
                 "(:i, :t, CAST(:v AS vector))"),
            {"i": TARGET, "t": f"The {TERM} provision applies to legacy settlements only.",
             "v": "[" + ",".join(f"{x:.8f}" for x in vec) + "]"},
        )

    qvec = randvec(rng)
    question = f"What does the {TERM} provision cover"

    with Session() as session:
        svc = RetrievalService(session)
        hybrid = svc.search(qvec, top_k=K, query_text=question)
        dense_only = svc.search(qvec, top_k=K, query_text="")

    hybrid_ids = [r["id"] for r in hybrid]
    dense_ids = [r["id"] for r in dense_only]

    result = {
        "run_id": "rrf-acceptance",
        "grade": "E3",
        "seed": SEED,
        "documents": 201,
        "top_k": K,
        "query_text": question,
        "min_score_override": os.environ.get("RAG_MIN_SCORE", "<default>"),
        "hybrid_ids": hybrid_ids,
        "hybrid_scores": [r["score"] for r in hybrid],
        "dense_only_ids": dense_ids,
        "target_in_hybrid": TARGET in hybrid_ids,
        "target_in_dense_only": TARGET in dense_ids,
        "target_rank_in_hybrid": hybrid_ids.index(TARGET) + 1 if TARGET in hybrid_ids else None,
        "pass": TARGET in hybrid_ids and TARGET not in dense_ids,
        "limits": [
            "random embeddings test the fusion logic, not semantic retrieval quality",
            "single seed, single query, k=4; no concurrency",
        ],
    }

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "rrf-acceptance.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
