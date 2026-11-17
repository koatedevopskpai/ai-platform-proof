# Experiments

Reproducible measurements behind the limitations stated in `docs/architecture.md` and the parity
receipt in `evidence/parity-h1.md`.

These are not part of the release path and are not run by CI. They exist so a reader can re-derive
the numbers rather than take them on trust.

Outputs are written to `artifacts/experiments/` and are git-ignored. Nothing here calls an external
model; the mock embedder is used throughout.

## Prerequisites

A PostgreSQL with the `pgvector` extension. The reference image is pinned in `docker-compose.yml`:

```sh
docker network create experiments
docker run -d --name experiments-pg --network experiments \
  -e POSTGRES_USER=ai -e POSTGRES_PASSWORD=ai -e POSTGRES_DB=aiplatform \
  pgvector/pgvector:0.8.2-pg16@sha256:00ba258a66dac104fd5171074a0084462a64a1369d8513f3d0a634e2f24d15bc
```

Run the scripts inside the `rag-api` image, which already carries the dependencies:

```sh
docker build -t ai-platform-experiments ./services/rag-api

docker run --rm --network experiments \
  -v "$PWD:/src" -w /src \
  -e DATABASE_URL="postgresql+psycopg://ai:ai@experiments-pg:5432/aiplatform" \
  ai-platform-experiments python scripts/experiments/<script>.py
```

## Scripts

### `parity.py`

| Subcommand | Produces | Supports |
|---|---|---|
| `ddl` | `artifacts/experiments/schema.sql` — DDL generated from `Base.metadata` | The schema is the ORM's, not hand-written |
| `gate` | `artifacts/experiments/gate.json` — verdicts for four constructed answers | The scope of the acceptance gate (§8 of `architecture.md`) |
| `hybrid` | `artifacts/experiments/hybrid.json` — keyword contribution with the real mock embedder | Why hybrid quality is unfalsifiable in mock mode. Reproduces the **pre-fix** SQL, like `hybrid2.py` |

### `hybrid2.py` — historical baseline

**This script reproduces the pre-fix SQL**, so it still reports the defects rather than the current
behaviour. It is kept because a "before" measurement is the only thing that makes the "after"
meaningful.

Decouples embeddings from text using random unit vectors with a fixed seed, so a document can be
lexically matchable but dense-unreachable. This is the only way to measure whether the keyword
branch contributes anything.

Produces `artifacts/experiments/hybrid2.json` and the `EXPLAIN` pair. It measured three defects in
the original implementation:

1. The keyword branch returns `k` arbitrary rows — all `ts_rank = 0.0`
2. A maximal lexical hit contributes ~0.018 against dense ~0.114, so it cannot reach top-k even
   after the branch is fixed
3. `plainto_tsquery` ANDs lexemes, so a natural-language question matches nothing

### `rrf_acceptance.py` — current behaviour

Acceptance test for the repaired retrieval path. It calls the **real `RetrievalService`**, not a
copy of its SQL, so it fails if the shipped implementation regresses.

Seeds 200 documents with embeddings decoupled from text, plus a target reachable only by keyword,
then sends the same query vector twice — once with `query_text`, once without. Passes only if the
target appears in the hybrid result and nowhere in the dense-only result.

```sh
docker run --rm --network experiments -v "$PWD:/src" -w /src \
  -e DATABASE_URL="postgresql+psycopg://ai:ai@experiments-pg:5432/aiplatform" \
  -e RAG_MIN_SCORE=0 \
  ai-platform-experiments python scripts/experiments/rrf_acceptance.py
```

`RAG_MIN_SCORE=0` isolates the two channels; with the default floor the random-vector neighbours are
filtered out and the comparison is less informative. Recorded result: target at rank 2, absent from
dense-only.

### `parity_h1.py`

Runs identical DDL, corpus and queries against one target and emits a fingerprint: server version,
extension version, schema signature, index definitions, result IDs, scores and plan shapes.

Run it twice — once against the local container, once against RDS — and compare
`artifacts/experiments/h1-<label>.json`. This is the method behind `evidence/parity-h1.md`.

```sh
# local reference
docker run --rm --network experiments -v "$PWD:/src" -w /src \
  -e DATABASE_URL="postgresql+psycopg://ai:ai@experiments-pg:5432/aiplatform" \
  -e TARGET_LABEL=local-pinned \
  ai-platform-experiments python scripts/experiments/parity_h1.py

# RDS target (PostgreSQL 16.14, db.t4g.micro, restricted security group)
docker run --rm -v "$PWD:/src" -w /src \
  -e DATABASE_URL="postgresql+psycopg://<user>:<password>@<endpoint>:5432/postgres?sslmode=require" \
  -e TARGET_LABEL=rds-16.14 \
  ai-platform-experiments python scripts/experiments/parity_h1.py
```

### `live_receipt.py` — live provider evidence

Runs the three acceptance fixtures through **real** embeddings and **real** generation, and records
provider, model, token counts, cost and a transcript hash. This is the artefact behind
`evidence/live-provider.md` and the answer to "where is the AI".

Requires an API key and a pgvector database:

```sh
docker run --rm --network experiments -v "$PWD:/src" -w /src \
  -e DATABASE_URL="postgresql+psycopg://ai:ai@experiments-pg:5432/aiplatform" \
  -e EMBEDDING_MODE=openai \
  -e OPENAI_API_KEY="$OPENAI_API_KEY" \
  -e OPENAI_BASE_URL="https://api.openai.com/v1" \
  -e LLM_MODEL=gpt-4o-mini \
  -e TARGET_LABEL=openai-both \
  ai-platform-experiments python scripts/experiments/live_receipt.py \
    --input-rate 0.15 --output-rate 0.60
```

To generate with a different provider, set `LLM_API_KEY`, `LLM_BASE_URL` and `LLM_MODEL`; embeddings
stay on OpenAI. Without `--input-rate`/`--output-rate` the receipt records tokens and leaves cost
null — an invented price is worse than no price.

The key is read from the environment and never printed or written into the receipt.

### `ragas_eval.py` — RAGAS framework evaluation

Scores live answers with the **RAGAS library** (`ragas==0.4.4`): faithfulness, answer relevancy,
context precision and recall, and answer correctness. Requires a running service in provider mode; it
refuses to score deterministic fallback output, because scores over a template describe the template.

```sh
docker run --rm --network ragaseval --entrypoint python -v "$PWD:/src" -w /src \
  -e RAG_API_URL=http://ragas-api:8000 \
  -e OPENAI_API_KEY="$OPENAI_API_KEY" -e OPENAI_BASE_URL=https://api.openai.com/v1 \
  ai-platform-proof-evaluator scripts/experiments/ragas_eval.py --label judge-gpt4o-mini
```

Evaluation set: `services/evaluator/datasets/ragas_set.jsonl` — three acceptance fixtures and three
held-out cases. Results and interpretation: `evidence/ragas-eval.md`.

Two things about 0.4.4 worth knowing before editing this script:

- `evaluate()` accepts only the **legacy** metric singletons; the modern `ragas.metrics.collections`
  classes are a different base type and are rejected.
- The legacy path needs the deprecated LangChain wrappers. Both are pinned deliberately.

## Limitations

- Random embeddings test the retrieval SQL, not semantic retrieval quality
- One seed, one query, `k = 4` as configured; no concurrency
- `parity_h1.py` destroys and recreates the `documents` table — **do not point it at a database
  holding data you want to keep**
- The `gate` subcommand injects a fake transport; it exercises the real predicates and policy but
  not a running application
- Cloud targets are billable. Tag them, cap them, and delete them in the same session
