"""Parity and falsification experiments.

Three subcommands:

  ddl     Generate DDL from the real SQLAlchemy metadata (never hand-written).
  hybrid  Measure whether the keyword branch contributes to top-k, and capture
          EXPLAIN (ANALYZE, BUFFERS) before and after a candidate fix.
  gate    Drive the real candidate_gate predicates with constructed answers and
          record verdicts.

Everything imports the repository's own modules. Nothing here restates the
schema or the gate logic. Outputs are written to artifacts/experiments/ and are
git-ignored.

See README.md in this directory for run instructions and the claims each
experiment supports.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "services" / "rag-api"))
sys.path.insert(0, str(REPO / "scripts" / "release"))

OUT = Path(os.environ.get("OUT_DIR", REPO / "artifacts" / "experiments"))


# --------------------------------------------------------------------------
# ddl
# --------------------------------------------------------------------------
def cmd_ddl(args):
    from sqlalchemy.schema import CreateIndex, CreateTable
    from sqlalchemy.dialects import postgresql

    from app.core.db import Base
    import app.models  # noqa: F401  registers Document

    dialect = postgresql.dialect()
    chunks = ["CREATE EXTENSION IF NOT EXISTS vector;"]
    for table in Base.metadata.sorted_tables:
        chunks.append(str(CreateTable(table).compile(dialect=dialect)).strip() + ";")
        # table.indexes is a set; iterate in name order or the emitted DDL (and its
        # hash) changes between processes under hash randomisation.
        for index in sorted(table.indexes, key=lambda i: i.name or ""):
            chunks.append(str(CreateIndex(index).compile(dialect=dialect)).strip() + ";")

    ddl = "\n\n".join(chunks) + "\n"
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / "schema.sql"
    path.write_text(ddl, encoding="utf-8")
    digest = hashlib.sha256(ddl.encode()).hexdigest()
    print(json.dumps({"ddl_path": str(path), "sha256": digest, "bytes": len(ddl)}, indent=2))
    return 0


# --------------------------------------------------------------------------
# hybrid
# --------------------------------------------------------------------------
TARGET_ID = "parity-target"
TARGET_TERM = "zygomorphpolicy"

FUSED_SQL = """
WITH dense AS (
    SELECT id, content, 1 - (embedding <=> CAST(:qvec AS vector)) AS dense_score
    FROM documents WHERE embedding IS NOT NULL
    ORDER BY embedding <=> CAST(:qvec AS vector) LIMIT :k
), kw AS (
    SELECT id, content, ts_rank(to_tsvector('english', content),
                                plainto_tsquery('english', :q)) AS kw_score
    FROM documents LIMIT :k
)
SELECT COALESCE(d.id, k.id) AS id, COALESCE(d.dense_score, 0.0) AS dense_score,
       COALESCE(k.kw_score, 0.0) AS kw_score,
       (COALESCE(d.dense_score, 0.0) * 0.7 + COALESCE(k.kw_score, 0.0) * 0.3) AS fused
FROM dense d FULL OUTER JOIN kw k ON d.id = k.id
ORDER BY fused DESC LIMIT :k
"""

DENSE_SQL = """
SELECT id FROM documents WHERE embedding IS NOT NULL
ORDER BY embedding <=> CAST(:qvec AS vector) LIMIT :k
"""

KW_BASELINE_SQL = """
SELECT id, ts_rank(to_tsvector('english', content),
                   plainto_tsquery('english', :q)) AS kw_score
FROM documents LIMIT :k
"""

KW_FIXED_SQL = """
SELECT id, ts_rank(to_tsvector('english', content),
                   plainto_tsquery('english', :q)) AS kw_score
FROM documents
WHERE to_tsvector('english', content) @@ plainto_tsquery('english', :q)
ORDER BY kw_score DESC LIMIT :k
"""

FUSED_FIXED_SQL = """
WITH dense AS (
    SELECT id, content, 1 - (embedding <=> CAST(:qvec AS vector)) AS dense_score
    FROM documents WHERE embedding IS NOT NULL
    ORDER BY embedding <=> CAST(:qvec AS vector) LIMIT :k
), kw AS (
    SELECT id, content, ts_rank(to_tsvector('english', content),
                                plainto_tsquery('english', :q)) AS kw_score
    FROM documents
    WHERE to_tsvector('english', content) @@ plainto_tsquery('english', :q)
    ORDER BY kw_score DESC LIMIT :k
)
SELECT COALESCE(d.id, k.id) AS id, COALESCE(d.dense_score, 0.0) AS dense_score,
       COALESCE(k.kw_score, 0.0) AS kw_score,
       (COALESCE(d.dense_score, 0.0) * 0.7 + COALESCE(k.kw_score, 0.0) * 0.3) AS fused
FROM dense d FULL OUTER JOIN kw k ON d.id = k.id
ORDER BY fused DESC LIMIT :k
"""

CLUSTERS = [
    "invoice reconciliation ledger accounting journal accrual",
    "kubernetes controller pod replica namespace ingress kubectl",
    "photosynthesis chlorophyll stomata xylem phloem botany",
    "mortgage amortisation principal interest escrow underwriting",
    "sourdough levain hydration crumb autolyse fermentation",
    "tectonic subduction lithosphere epicentre seismograph fault",
    "vaccine antibody antigen immunology lymphocyte serology",
    "tariff customs excise shipping manifest consignment freight",
]


def _doc_rows(n_docs: int):
    rows = []
    for i in range(n_docs):
        cluster = CLUSTERS[i % len(CLUSTERS)]
        rows.append({"id": f"parity-{i:04d}", "content": f"{cluster} reference note {i:04d}"})
    rows.append({"id": TARGET_ID,
                 "content": f"The {TARGET_TERM} provision applies only to legacy settlements."})
    return rows


def cmd_hybrid(args):
    from sqlalchemy import create_engine, text
    from app.models import Document  # noqa: F401
    from app.core.db import Base
    from app.services.embedding import EmbeddingService

    url = os.environ["DATABASE_URL"]
    engine = create_engine(url, future=True)
    embedder = EmbeddingService()

    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        Base.metadata.drop_all(bind=conn)
        Base.metadata.create_all(bind=conn)

        rows = _doc_rows(args.docs)
        for row in rows:
            vec = embedder.embed(row["content"])
            conn.execute(
                text("INSERT INTO documents (id, content, embedding) "
                     "VALUES (:id, :content, CAST(:vec AS vector))"),
                {"id": row["id"], "content": row["content"],
                 "vec": "[" + ",".join(f"{v:.8f}" for v in vec) + "]"},
            )
        total = conn.execute(text("SELECT count(*) FROM documents")).scalar_one()
        dims = conn.execute(text("SELECT vector_dims(embedding) FROM documents LIMIT 1")).scalar_one()

    query = f"What does the {TARGET_TERM} provision cover"
    qvec = "[" + ",".join(f"{v:.8f}" for v in embedder.embed(query)) + "]"
    k = 4

    def ids(sql):
        with engine.connect() as conn:
            return [r[0] for r in conn.execute(text(sql), {"qvec": qvec, "q": query, "k": k})]

    def explain(sql, label):
        with engine.connect() as conn:
            plan = "\n".join(r[0] for r in conn.execute(
                text("EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT) " + sql),
                {"qvec": qvec, "q": query, "k": k}))
        (OUT / "plans").mkdir(parents=True, exist_ok=True)
        (OUT / "plans" / f"{label}.txt").write_text(plan, encoding="utf-8")
        return {
            "label": label,
            "uses_gin_index": "ix_documents_content_tsv" in plan,
            "seq_scan": "Seq Scan on documents" in plan,
            "plan_path": str(OUT / "plans" / f"{label}.txt"),
        }

    dense_ids = ids(DENSE_SQL)
    kw_baseline = ids(KW_BASELINE_SQL)
    kw_fixed = ids(KW_FIXED_SQL)
    fused_ids = ids(FUSED_SQL)
    fused_fixed_ids = ids(FUSED_FIXED_SQL)

    def weirdness(returned):
        only_kw = [i for i in returned if i not in dense_ids]
        return {"returned": returned, "keyword_only": only_kw,
                "keyword_only_share": round(len(only_kw) / max(len(returned), 1), 4),
                "target_present": TARGET_ID in returned}

    from datetime import datetime, timezone
    result = {
        "run_id": "hybrid-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
        "grade": "E3",
        "documents": total,
        "embedding_dims": dims,
        "k": k,
        "query": query,
        "dense_ids": dense_ids,
        "kwargs": {"baseline": kw_baseline, "fixed": kw_fixed},
        "baseline": weirdness(fused_ids),
        "fixed": weirdness(fused_fixed_ids),
        "plans": [explain(FUSED_SQL, "fused_baseline"), explain(FUSED_FIXED_SQL, "fused_fixed")],
        "limits": [
            "local container or RDS only; no both-target comparison in this subcommand",
            "single seeded corpus; k=4 as configured",
            "no concurrency; one query",
        ],
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "hybrid.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps({k2: result[k2] for k2 in
                      ("documents", "embedding_dims", "dense_ids", "kwargs",
                       "baseline", "fixed", "plans")}, indent=2))
    return 0


# --------------------------------------------------------------------------
# gate
# --------------------------------------------------------------------------
def cmd_gate(args):
    import candidate_gate

    dataset = str(REPO / "scripts" / "release" / "acceptance.json")
    data = json.loads(Path(dataset).read_text(encoding="utf-8"))
    corpus = {d["id"]: d["content"] for d in data["documents"]}
    ids = list(corpus)

    PREFIX = "Per the source material: "

    def make_request(scenario):
        def request(base, route, payload):
            if route == "/ingest":
                return {"id": payload["id"], "dimensions": 384}
            if route == "/search":
                return {"results": [{"id": i, "content": corpus[i]} for i in ids]}
            if route == "/rag":
                case_id = next(c["id"] for c in data["cases"]
                               if c["question"] == payload["query"])
                answer, sources = scenario(case_id, corpus)
                return {"answer": answer, "sources": sources, "fallback": True,
                        "blocked": False, "generation_mode": "deterministic_fallback"}
            raise AssertionError(route)
        return request

    def baseline(case_id, corpus_):
        return PREFIX + " ".join(corpus_.values())[:400], ids

    def inversion(case_id, corpus_):
        if case_id == "refund":
            return PREFIX + "Full refunds are never available within 30 days of purchase.", ids
        if case_id == "software":
            return PREFIX + "Opened software is always refundable and not refundable terms differ.", ids
        return PREFIX + "The daily meal expense limit is GBP 25 without restriction.", ids

    def cross_document(case_id, corpus_):
        if case_id == "refund":
            return PREFIX + "Opened software is not refundable within 30 days of purchase.", ids
        if case_id == "software":
            return PREFIX + "Full refunds are available within 30 days of purchase.", ids
        return PREFIX + "Full refunds are available within 30 days of purchase.", ids

    def wrong_source(case_id, corpus_):
        return PREFIX + " ".join(corpus_.values())[:400], \
            ["o02-meals"] if case_id != "meals" else ["o02-refund"]

    scenarios = {
        "baseline_correct": baseline,
        "semantic_inversion": inversion,
        "cross_document_answer": cross_document,
        "wrong_source_id": wrong_source,
    }

    results = {}
    for name, fn in scenarios.items():
        report = candidate_gate.evaluate("/fake", dataset, {"provenance": "experiment"},
                                         request=make_request(fn))
        results[name] = {
            "passed": report["gate"]["passed"],
            "means": report["gate"]["means"],
            "hard_failures": report["gate"]["hard_failures"],
            "rows": [{"case": r["case_id"], "faithfulness": r["faithfulness"],
                      "answer_relevancy": r["answer_relevancy"]} for r in report["rows"]],
        }

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "gate.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(json.dumps(results, indent=2))
    return 0


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("ddl")
    h = sub.add_parser("hybrid")
    h.add_argument("--docs", type=int, default=200)
    sub.add_parser("gate")
    args = parser.parse_args()
    return {"ddl": cmd_ddl, "hybrid": cmd_hybrid, "gate": cmd_gate}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
