# Architecture — as built

**Last verified:** 16 November 2026 against commit `26811fd`.
**Status:** describes the system that exists in this repository. The previously published version
of this file described an EKS/Helm deployment that was never built; that material is preserved in
§11 as unsupported design history.

This is a demonstration platform. It is self-funded, synthetic, and not operated commercially.
See `EVIDENCE.md` for what each exercise proves and does not prove.

---

## 1. Scope

| Path | Status |
|---|---|
| EC2/Linux + Docker Compose, immutable image bundle | **Supported release path** — see [O01](O01.md) |
| Local Docker Compose development | **Supported** |
| Kubernetes / Helm / EKS | **Unsupported design material** — see §11 |
| GitHub Actions cloud deployment | **Not used.** `deploy-cheap.yml` transfers a prebuilt bundle; `deploy.yml` is a deliberate tripwire |

---

## 2. Services

| Component | Language / runtime | Container port | Host port | Entry point |
|---|---|---|---|---|
| `gateway` | TypeScript / Fastify | 3000 | 3002 | `services/gateway/src/index.ts` |
| `rag-api` | Python / FastAPI | 8000 | 8010 | `services/rag-api/app/main.py` |
| `dotnet-ingest` | C# / .NET 8 | 8080 | 8080 | `services/dotnet-ingest/src/IngestApi/Program.cs` |
| `evaluator` | Python (batch) | none published | none | `services/evaluator/evaluator/main.py` |
| `postgres` | `pgvector/pgvector:0.8.2-pg16` | 5432 | 5432 | Compose service — pinned to match RDS 16.14 / pgvector 0.8.2 |
| `prometheus` | Prometheus | 9090 | 9090 | `monitoring` profile only |
| `grafana` | Grafana | 3000 | 3001 | `monitoring` profile only |

### gateway

Edge gateway. Aggregates `/health` across both backends, proxies `/ingest` to `dotnet-ingest`
and `/search` and `/rag` to `rag-api`, applies a token-bucket rate limit, propagates a validated
`x-trace-id` and emits one structured span per request and per upstream call.

### rag-api

The core AI service: embed and persist documents, retrieve, and generate a grounded answer with
guardrails, PII redaction on the query, and an explicit deterministic fallback.

### dotnet-ingest

C# implementation of the same ingest path over the shared store, structured behind
`IEmbeddingClient` and `IVectorStore` interfaces, with its own unit-test project.

### evaluator

Off-the-request-path MLOps component: RAGAS-style and lexical metrics with a deterministic mock
judge. **It is not part of the release decision.** The release gate evaluates fresh responses from
the candidate application instead (O02). The evaluator remains in the Compose stack and is not
invoked by any workflow.

---

## 3. Request paths

**`GET /health`** — fans out to both backends in parallel with a 2 s timeout and requires each
backend to report `status: "ok"`. Returns `ok` only when both are healthy, otherwise `degraded`.
An HTTP 200 alone is not sufficient.

**`POST /ingest`** → `dotnet-ingest` → embedding client → `PgVectorStore` → PostgreSQL.

**`POST /search`** → `rag-api` → `RetrievalService.search` → PostgreSQL.

**`POST /rag`** → `rag-api` → retrieval → `RAGService.answer` → PostgreSQL.

Every proxied request carries `x-trace-id` and `x-parent-span-id`. Each route and each upstream
call emits a structured span containing component, operation, identifiers, duration, status and a
safe error type. Span metadata is allowlisted; prompts, documents, vectors and provider error
bodies are not recorded.

---

## 4. Data plane

A single PostgreSQL instance with `pgvector`, shared by `rag-api` and `dotnet-ingest`. The
extension and schema are created on service startup; there are no migration files.

```sql
documents(
  id          varchar(64) primary key,
  content     text        not null,
  created_at  timestamptz not null default now(),
  embedding   vector(384)          -- null until embedded
)
```

Indexes:

- `ix_documents_embedding_hnsw` — HNSW on `embedding`, `vector_cosine_ops`, `m = 16`,
  `ef_construction = 64`
- `ix_documents_content_tsv` — GIN on `to_tsvector('english', content)`

Embedding width is 384 in mock mode. Mock embeddings are deterministic hashed character
trigrams, and the Python and .NET implementations must produce byte-identical vectors so both
services can share one store.

---

## 5. Retrieval

Documented behaviour: hybrid retrieval combining dense cosine similarity with PostgreSQL
full-text ranking, fused as `0.7 × dense + 0.3 × keyword`, filtered by a minimum score.

**Known limitations, measured 16 November 2026** (`scripts/experiments/README.md`):

1. The keyword branch selects `k` rows with no `tsquery` match predicate and no ordering, so it
   returns arbitrary documents. In a 201-document run all four returned rows had `ts_rank = 0.0`,
   and `EXPLAIN` shows a sequential scan truncated at `LIMIT 4`.
2. The `0.7 × dense + 0.3 × keyword` fusion blends incommensurable scales. A maximal `ts_rank` of
   ~0.061 contributes ~0.018 against dense contributions of ~0.114, so a document found only
   lexically cannot reach top-k even after the first defect is fixed.
3. `plainto_tsquery` ANDs every lexeme, so a natural-language question becomes
   `'term1' & 'term2' & 'term3'` and matches nothing. The straightforward fix for the first defect
   therefore removes keyword retrieval entirely.

The GIN index exists and is reachable as soon as a match predicate is added
(`Bitmap Index Scan on ix_documents_content_tsv`); its presence records the intended clause that
was never written.

The intended repair is reciprocal rank fusion over `websearch_to_tsquery`, with an acceptance test
requiring a lexically-only document to reach the fused top-k.

Separately: with the mock trigram-hash embedder a distinctive token drives both channels, so hybrid
quality cannot be measured locally. That is a limit of the fixture, not of the code.

---

## 6. Generation, guardrails and fallback

Order of operations: prompt-injection check → PII redaction of the query → provider call →
deterministic fallback.

- A prompt-injection match returns a blocked response with no sources.
- PII redaction covers the query. Retrieved contexts are not redacted before entering the prompt.
- The provider path activates when an API key is configured. This is independent of the embedding
  mode, so generation can be live while embeddings remain mock.
- On provider HTTP error, transport error, invalid response, or no context, the service returns a
  deterministic source-based answer and a bounded reason code
  (`provider_http_<code>`, `provider_transport_error`, `provider_invalid_response`, `no_context`,
  `mock_generation`). Raw provider error bodies are never returned.

In mock mode the deterministic answer is a truncation of the retrieved contexts. This matters for
evaluation: see §8.

---

## 7. Release and promotion

```
build (immutable image IDs, source labels, manifest, checksums)
  → preflight (reject wrong source, changed bytes, mutable references, ID mismatch)
  → candidate stack on an isolated volume, loopback :3003
  → acceptance gate against actual HTTP responses
  → promote with --no-build --pull never
  → post-promotion acceptance on :3002
  → receipt (source, expected/running IDs, config and manifest hashes, health, timestamps)
```

Receipts cannot be overwritten. A failure after preflight produces a failed receipt and a nonzero
exit; it never reports unconditional success. The operator must trust the build runner, manifest
and transport — checksums do not authenticate an untrusted publisher.

---

## 8. Evaluation

The promotion gate sends three acceptance cases through the candidate's real gateway and checks
that each response comes from the application, cites the expected source, matches the retrieved
source bytes, contains required phrases and excludes forbidden ones, and reports explicit fallback
mode.

**Scope of the gate, measured 16 November 2026.** The predicates are binary fixture checks, not
semantic scoring. Four constructed answers were driven through the real gate
(`scripts/experiments/parity.py gate`):

- A correct echo passes.
- A semantic inversion is blocked — but because the negating word is absent from the corpus, so
  token containment fails on vocabulary rather than on meaning. `answer_relevancy` still scored
  1.0 on two of the three inverted cases.
- An answer drawn from the **wrong document**, with valid source IDs, scored
  `faithfulness = 1.0`. It was stopped only by the per-case required-phrase check.
- A wrong source ID is blocked.

So `faithfulness` is a vocabulary-membership test that cannot distinguish supporting content from
substituted content, and the gate's real discrimination comes from a hand-curated required-phrase
list — a fixture property, not an evaluation method. The gate establishes provenance binding, image
identity and fail-closed promotion. It does not establish answer quality. Semantic evaluation
remains a separate, deferred item.

---

## 9. Observability

Implemented: structured, correlated local spans across gateway and `rag-api`; a bounded-window
indicator script that computes acceptance rate, error rate and nearest-rank percentiles; and a
local alert state machine that cannot report healthy from an empty window.

Not implemented: W3C `traceparent`/OTLP export, a tracing backend, continuous monitoring,
Alertmanager, or any external notification delivery.

`mlops/monitoring/` contains Prometheus rules, a Grafana dashboard definition and a drift script.
These are configuration material. No drill in this repository demonstrates that they ran or that
an alert was delivered.

---

## 10. Infrastructure

| Path | Purpose | Status |
|---|---|---|
| `infra/terraform/environments/cheap` | EC2 host for the Compose release | Supported |
| `infra/terraform/environments/dev` | EKS cluster design | Unsupported |
| `infra/helm/ai-platform` | Umbrella chart | Unsupported |
| `scripts/ephemeral/` | Bounded up/down with TTL guard and cost estimate | Supported |
| `scripts/cheap/` | Manual bundle deploy over verified SSH | Supported |

FinOps tagging is applied at RDS scope. Budget alerts are notifications, not spend caps.

---

## 11. Unsupported design history

`infra/helm/ai-platform` and `infra/terraform/environments/dev` describe an EKS deployment with
Karpenter-ready node groups, Helm-deployed services, probes and autoscalers. **None of this is
exercised by any workflow or drill in this repository**, and the supported release path is
Compose on EC2.

The Helm chart and the EKS environment are retained as design material and are not a deployment
path. `deploy.yml` exists only to fail loudly and point at the supported path. Do not treat any
EKS statement in this repository as validated.

---

## 12. Security posture

- Secrets via environment or Secrets Manager; `.env` and `*.tfvars` are git-ignored
- Demonstration credentials are synthetic and unsuitable for real data
- The generated release profile binds the gateway to loopback and publishes no database port
- Least-privilege IAM in Terraform; the compute guard is restricted to tagged lab instances
- Prompt-injection guardrail and query PII redaction are hooks, not a full security control
- No security certification is claimed

---

## 13. Verification

See `EVIDENCE.md` for the full index. The offline checks are:

```sh
python -m unittest discover -s tests -v
python -m pytest services/rag-api/tests -q
```

The session drills (`tests/integration_o02.py`, `o03`, `o04`) build and start real containers and
deliberately introduce failures. Run them sequentially.
