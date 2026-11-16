"""H1: run the identical DDL, corpus and query set against two targets and dump
a comparable fingerprint. Run twice — once against a local container, once
against RDS (or any other PostgreSQL with pgvector) — then compare the outputs.

Environment:
  DATABASE_URL    postgresql+psycopg://...
  TARGET_LABEL    local-pinned | rds-16.14 | ...

Outputs go to artifacts/experiments/ and are git-ignored.
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "services" / "rag-api"))

from sqlalchemy import create_engine, text  # noqa: E402

OUT = Path(os.environ.get("OUT_DIR", REPO / "artifacts" / "experiments"))
LABEL = os.environ.get("TARGET_LABEL", "local")

SEED = 20261116
K = 4
TERM = "zygomorphpolicy"
TARGET = "parity-target"
N_DOCS = 200

DENSE = ("SELECT id FROM documents WHERE embedding IS NOT NULL "
         "ORDER BY embedding <=> CAST(:qvec AS vector) LIMIT :k")

KW_BASE = ("SELECT id, ts_rank(to_tsvector('english', content), "
           "plainto_tsquery('english', :q)) AS s FROM documents LIMIT :k")

KW_FIX = ("SELECT id, ts_rank(to_tsvector('english', content), "
          "plainto_tsquery('english', :q)) AS s FROM documents "
          "WHERE to_tsvector('english', content) @@ plainto_tsquery('english', :q) "
          "ORDER BY s DESC LIMIT :k")

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

QUERIES = {"dense": DENSE, "kw_baseline": KW_BASE, "kw_fixed": KW_FIX,
           "fused_baseline": FUSED_BASE, "fused_fixed": FUSED_FIX}

NODE_RE = re.compile(r"(?:->\s*)?([A-Z][A-Za-z ]*?)\s+\((?:cost|actual)")


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
        for i in range(N_DOCS):
            c.execute(text("INSERT INTO documents (id, content, embedding) VALUES "
                           "(:i,:t,CAST(:v AS vector))"),
                      {"i": f"parity-{i:04d}",
                       "t": f"cluster note {i:04d} with no special vocabulary",
                       "v": randvec(rng)})
        c.execute(text("INSERT INTO documents (id, content, embedding) VALUES "
                       "(:i,:t,CAST(:v AS vector))"),
                  {"i": TARGET,
                   "t": f"The {TERM} provision applies to legacy settlements.",
                   "v": randvec(rng)})

    qvec = randvec(rng)
    params = {"qvec": qvec, "q": TERM, "k": K}

    def run(sql):
        with engine.connect() as c:
            rows = [dict(r._mapping) for r in c.execute(text(sql), params)]
            plan = "\n".join(r[0] for r in c.execute(
                text("EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT) " + sql), params))
        return rows, plan

    with engine.connect() as c:
        server = c.execute(text("SELECT current_setting('server_version')")).scalar_one()
        available = c.execute(text(
            "SELECT default_version FROM pg_available_extensions WHERE name='vector'"
        )).scalar()
        installed = c.execute(text(
            "SELECT extversion FROM pg_extension WHERE extname='vector'"
        )).scalar()
        cols = c.execute(text(
            "SELECT column_name||':'||data_type||':'||udt_name "
            "FROM information_schema.columns WHERE table_name='documents' "
            "ORDER BY ordinal_position")).scalars().all()
        idx = c.execute(text(
            "SELECT indexname||'='||indexdef FROM pg_indexes "
            "WHERE tablename='documents' ORDER BY indexname")).scalars().all()
        count = c.execute(text("SELECT count(*) FROM documents")).scalar_one()

    schema_sig = hashlib.sha256(
        ("|".join(cols) + "||" + "|".join(idx)).encode()).hexdigest()

    results = {}
    for name, sql in QUERIES.items():
        rows, plan = run(sql)
        results[name] = {
            "ids": [r.get("id") for r in rows],
            "scores": [{k2: (round(v, 6) if isinstance(v, float) else v)
                        for k2, v in r.items() if k2 != "id"} for r in rows],
            "plan_nodes": [s.strip() for s in NODE_RE.findall(plan)],
            "plan_sha256": hashlib.sha256(plan.encode()).hexdigest(),
        }

    payload = {
        "target": LABEL,
        "grade": "E3",
        "seed": SEED,
        "documents": count,
        "server_version": server,
        "pgvector_available": available,
        "pgvector_installed": installed,
        "schema_signature_sha256": schema_sig,
        "columns": cols,
        "indexes": idx,
        "results": results,
        "limits": [
            "random embeddings; tests retrieval SQL, not semantic quality",
            "single seed, single query, k=4; no concurrency",
        ],
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"h1-{LABEL}.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps({
        "target": LABEL, "server_version": server,
        "pgvector_installed": installed, "documents": count,
        "schema_signature": schema_sig[:16],
        "dense_ids": results["dense"]["ids"],
        "kw_baseline_ids": results["kw_baseline"]["ids"],
        "kw_fixed_ids": results["kw_fixed"]["ids"],
        "fused_baseline_ids": results["fused_baseline"]["ids"],
        "fused_fixed_ids": results["fused_fixed"]["ids"],
        "plan_nodes_fused_baseline": results["fused_baseline"]["plan_nodes"],
        "plan_nodes_fused_fixed": results["fused_fixed"]["plan_nodes"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
