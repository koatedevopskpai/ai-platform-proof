"""H2b: isolate the keyword branch with embeddings deliberately decoupled from text.

The mock embedder is a character-trigram hash, so a distinctive token influences
both the dense and the lexical signal. That makes a "lexical hit that dense
misses" scenario impossible to construct with the real embedder. Here the
embeddings are random unit vectors with a fixed seed, which tests the retrieval
SQL rather than the embedder. Labelled accordingly in the output.

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

OUT = Path(os.environ.get("OUT_DIR", REPO / "artifacts" / "experiments"))
TARGET = "parity-target"
TERM = "zygomorphpolicy"
K = 4
SEED = 20261116

DENSE = "SELECT id FROM documents WHERE embedding IS NOT NULL " \
        "ORDER BY embedding <=> CAST(:qvec AS vector) LIMIT :k"

KW_BASE = "SELECT id, ts_rank(to_tsvector('english', content), " \
          "plainto_tsquery('english', :q)) AS s FROM documents LIMIT :k"

KW_FIX = "SELECT id, ts_rank(to_tsvector('english', content), " \
         "plainto_tsquery('english', :q)) AS s FROM documents " \
         "WHERE to_tsvector('english', content) @@ plainto_tsquery('english', :q) " \
         "ORDER BY s DESC LIMIT :k"

FUSED_BASE = """
WITH dense AS (
  SELECT id, 1 - (embedding <=> CAST(:qvec AS vector)) AS ds FROM documents
  WHERE embedding IS NOT NULL ORDER BY embedding <=> CAST(:qvec AS vector) LIMIT :k
), kw AS (
  SELECT id, ts_rank(to_tsvector('english', content), plainto_tsquery('english', :q)) AS ks
  FROM documents LIMIT :k
)
SELECT COALESCE(d.id,k.id) AS id, COALESCE(d.ds,0) AS ds, COALESCE(k.ks,0) AS ks,
       COALESCE(d.ds,0)*0.7 + COALESCE(k.ks,0)*0.3 AS fused
FROM dense d FULL OUTER JOIN kw k ON d.id=k.id ORDER BY fused DESC LIMIT :k
"""

FUSED_FIX = """
WITH dense AS (
  SELECT id, 1 - (embedding <=> CAST(:qvec AS vector)) AS ds FROM documents
  WHERE embedding IS NOT NULL ORDER BY embedding <=> CAST(:qvec AS vector) LIMIT :k
), kw AS (
  SELECT id, ts_rank(to_tsvector('english', content), plainto_tsquery('english', :q)) AS ks
  FROM documents
  WHERE to_tsvector('english', content) @@ plainto_tsquery('english', :q)
  ORDER BY ks DESC LIMIT :k
)
SELECT COALESCE(d.id,k.id) AS id, COALESCE(d.ds,0) AS ds, COALESCE(k.ks,0) AS ks,
       COALESCE(d.ds,0)*0.7 + COALESCE(k.ks,0)*0.3 AS fused
FROM dense d FULL OUTER JOIN kw k ON d.id=k.id ORDER BY fused DESC LIMIT :k
"""


def randvec(rng):
    v = [rng.uniform(-1, 1) for _ in range(384)]
    n = sum(x * x for x in v) ** 0.5 or 1.0
    return "[" + ",".join(f"{x / n:.8f}" for x in v) + "]"


def main():
    rng = random.Random(SEED)
    engine = create_engine(os.environ["DATABASE_URL"], future=True)
    from app.core.db import Base  # noqa: E402
    import app.models  # noqa: F401,E402

    with engine.begin() as c:
        c.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        Base.metadata.drop_all(bind=c)
        Base.metadata.create_all(bind=c)
        for i in range(200):
            c.execute(text("INSERT INTO documents (id, content, embedding) VALUES "
                           "(:i, :t, CAST(:v AS vector))"),
                      {"i": f"parity-{i:04d}",
                       "t": f"cluster note {i:04d} with no special vocabulary",
                       "v": randvec(rng)})
        c.execute(text("INSERT INTO documents (id, content, embedding) VALUES "
                       "(:i, :t, CAST(:v AS vector))"),
                  {"i": TARGET, "t": f"The {TERM} provision applies to legacy settlements.",
                   "v": randvec(rng)})

    qvec = randvec(rng)  # unrelated to text
    params = {"qvec": qvec, "q": TERM, "k": K}

    def rows(sql):
        with engine.connect() as c:
            return [dict(r._mapping) for r in c.execute(text(sql), params)]

    def plan(sql):
        with engine.connect() as c:
            return "\n".join(r[0] for r in c.execute(
                text("EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT) " + sql), params))

    dense = [r["id"] for r in rows(DENSE)]
    result = {
        "run_id": "hybrid2-decoupled",
        "grade": "E3",
        "seed": SEED,
        "documents": 201,
        "k": K,
        "query_text": TERM,
        "embedding_regime": "random unit vectors — decoupled from content; tests SQL not embedder",
        "dense_ids": dense,
        "kw_baseline": rows(KW_BASE),
        "kw_fixed": rows(KW_FIX),
        "fused_baseline": rows(FUSED_BASE),
        "fused_fixed": rows(FUSED_FIX),
        "derived": {
            "target_dense_retrievable": TARGET in dense,
            "target_in_kw_baseline": any(r["id"] == TARGET for r in rows(KW_BASE)),
            "target_in_kw_fixed": any(r["id"] == TARGET for r in rows(KW_FIX)),
            "target_in_fused_baseline": any(r["id"] == TARGET for r in rows(FUSED_BASE)),
            "target_in_fused_fixed": any(r["id"] == TARGET for r in rows(FUSED_FIX)),
        },
        "limits": [
            "random embeddings do not represent the shipped mock embedder",
            "single term query; natural-language queries AND all lexemes under plainto_tsquery",
            "no concurrency; k=4 as configured",
        ],
    }
    (OUT / "plans").mkdir(parents=True, exist_ok=True)
    (OUT / "plans" / "h2b_baseline.txt").write_text(plan(FUSED_BASE), encoding="utf-8")
    (OUT / "plans" / "h2b_fixed.txt").write_text(plan(FUSED_FIX), encoding="utf-8")
    (OUT / "hybrid2.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
