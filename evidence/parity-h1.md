# H1 — Environment parity receipt

**Date:** 16 November 2026
**Source revision:** `26811fd`
**Grade:** E3 — local execution with recorded output
**Scope:** PostgreSQL engine, pgvector extension, schema, planner behaviour and result semantics

This receipt records a comparison between the local reference environment and Amazon RDS. It does
**not** claim production similarity. See "What this does not establish" before citing it.

---

## Purpose

The platform's retrieval and evaluation claims depend on PostgreSQL with `pgvector`. Those claims
are made from a local container. If the local container and the deployment target differ in engine
or extension version, results obtained locally do not transfer, and any statement resting on them
is weaker than it appears.

This receipt tests whether they transfer.

---

## Method

The same generated DDL, the same seeded corpus (seed `20261116`, 201 documents, 384-dimension
random unit vectors) and the same five queries were run against both targets:

| Query | Purpose |
|---|---|
| `dense` | cosine ordering over the HNSW index |
| `kw_baseline` | the shipped keyword branch |
| `kw_fixed` | keyword branch with match predicate and ordering |
| `fused_baseline` | the shipped `0.7 × dense + 0.3 × keyword` fusion |
| `fused_fixed` | fusion with the corrected keyword branch |

Captured per query: returned IDs, scores, and `EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT)`.
DDL is generated from `Base.metadata` — never hand-written — and the applied schema is fingerprinted
from `information_schema.columns` and `pg_indexes`.

---

## Pass 1 — unaligned: divergence found

| Dimension | Container `pgvector/pgvector:pg16` | RDS 16.15 |
|---|---|---|
| PostgreSQL | 16.16 | 16.15 |
| pgvector | 0.8.7 | 0.8.2 |

Results matched on every query, but the versions did not. Identical behaviour from different
versions is an observation, not an alignment.

**Cause:** `pgvector/pgvector:pg16` is a floating major tag. It tracked upstream to 16.16 / 0.8.7
while RDS stopped at 16.15 / 0.8.2. RDS offered 16.11–16.15, so neither endpoint could be moved to
the other by choosing a region.

**Conclusion:** the container had to be pinned, not the region chosen more carefully.

---

## Pass 2 — aligned: exact match

Container pinned to `pgvector/pgvector:0.8.2-pg16` (PostgreSQL 16.14 / pgvector 0.8.2);
RDS pinned to PostgreSQL **16.14**, `db.t4g.micro`, Single-AZ, 20 GB gp3, `eu-west-2`.

| Dimension | Local (pinned) | RDS 16.14 | Verdict |
|---|---|---|---|
| PostgreSQL server | 16.14 | 16.14 | Identical |
| pgvector installed | 0.8.2 | 0.8.2 | Identical |
| Table columns | — | — | Identical |
| Index definitions | — | — | Identical |
| Schema signature | `55d53d82…` | `55d53d82…` | Identical |
| Row count | 201 | 201 | Identical |
| All five query result IDs | — | — | Identical |
| All five plan shapes | — | — | Identical |
| `fused_fixed` scores | — | — | Identical to 6 dp |

Applied index definitions on RDS, read back from `pg_indexes`:

```
ix_documents_embedding_hnsw | USING hnsw (embedding vector_cosine_ops) WITH (m='16', ef_construction='64')
ix_documents_content_tsv    | USING gin (to_tsvector('english'::regconfig, content))
documents_pkey              | USING btree (id)
```

---

## Change made

| File | Change |
|---|---|
| `docker-compose.yml` | `pgvector/pgvector:pg16` → `pgvector/pgvector:0.8.2-pg16` |
| `docs/architecture.md` | service table updated |
| `scripts/release/release.py` | new `POSTGRES_IMAGE` constant used by `pull` and `inspect` |

`release.py` previously duplicated the image literal in two calls. The constant removes the
duplication so the reference environment and the release path cannot drift apart.

**Maintenance obligation:** if RDS moves to a newer PostgreSQL or pgvector version, this pin must
move with it and this receipt becomes stale. A stale receipt is worse than an absent one.

---

## What this establishes

The local reference environment is equivalent to the RDS target on engine version, extension
version, schema, index definitions, planner behaviour, result sets and result scores — across five
query shapes, including the three retrieval defects recorded in the harness results.

## What this does not establish

- RDS parameter groups, storage class, IOPS, PITR, backup and restore — not exercised
- Connection pooling, `max_connections`, concurrency — not exercised
- HA, failover, read replicas — not exercised
- One instance class, one AZ, one region
- Timings — not compared; only plans and results
- Production operation of any kind

**Correct phrasing:** "identical engine version, extension version, schema, planner behaviour,
result sets and scores on PostgreSQL 16.14 / pgvector 0.8.2, verified against Amazon RDS in
`eu-west-2`."

**Incorrect phrasing:** "similar to a production environment."

---

## Cost and cleanup

`db.t4g.micro` for approximately 20 minutes; estimated **< $0.02**. Instances deleted with
`--skip-final-snapshot`; subnet group and security group removed; absence verified
(`rds: 0`, `subnetgroups: 0`, `security groups: 0`). Billing reconciliation outstanding —
Cost Explorer lags 24 hours.

---

## Provenance

Harness and raw JSON receipts are held outside this repository:
`portfolio-exemplar-plan/ai-platform-proof/harness/` — `parity_h1.py`, `out/h1-local-pinned.json`,
`out/h1-rds-16.14.json`.
