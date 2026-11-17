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
extension and schema are created on service **startup**; there are no migration files.

**Known limitation, not a design choice.** Every replica attempts the same DDL. `CREATE EXTENSION IF
NOT EXISTS` is not atomic against concurrent DDL — it checks then creates inside the transaction, so two
instances starting against a fresh database both proceed and one fails on the unique index over
`pg_extension`. That was observed, not theorised.

Startup now takes a `pg_advisory_xact_lock` before the DDL, which serialises it. **That is a
mitigation.** The durable fix is a single-writer migration step: one process owns the schema and the
application only asserts that it is present. Alembic would go further, with versioned and reversible
migrations, but it changes the release contract — `release.py` builds an immutable bundle and starts it
with `--no-build --pull never`, so a migration phase would alter the deployment shape the O02 receipts
describe. Schema migration is therefore a follow-on project, not adopted here.

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

Embedding width is **384 in both modes**. Mock embeddings are deterministic hashed character
trigrams, and the Python and .NET implementations must produce byte-identical vectors so both
services can share one store.

In provider mode the service requests `dimensions: 384` from the OpenAI-compatible endpoint —
`text-embedding-3-*` supports the parameter. That keeps `vector(384)`, the HNSW index and the RDS
parity receipt valid without a migration. The response width is checked before use, so a provider
returning a different width fails at the provider boundary rather than later as an opaque PostgreSQL
type error at insert. Batch items are ordered by `index` rather than response order, so input text and
stored vector cannot drift apart.

---

## 5. Retrieval

Hybrid retrieval combining dense cosine similarity with PostgreSQL full-text ranking, fused by
**reciprocal rank fusion**:

```
score = 1 / (60 + rank_dense) + 1 / (60 + rank_keyword)
```

Rank fusion is used rather than a weighted sum because the two channels are not on a common scale.
A weighted `0.7 × dense + 0.3 × keyword` blend was measured on 16 November 2026 and could not
express a lexical win at all: a maximal `ts_rank` of ~0.061 contributed ~0.018 against dense
contributions of ~0.114. Results are ordered by score with `id` as a deterministic tiebreak.

**Keyword channel.** `websearch_to_tsquery` over an OR-joined token set. `websearch_to_tsquery`
ANDs bare terms, so a question passed directly becomes `'zygomorphpolici' & 'provis' & 'cover'`
and requires every term in one document — measured, it matched nothing. OR-joining makes the
channel recall-first, and `ts_rank` orders documents by how much of the query they cover. Stopwords
are removed by the `english` configuration. The GIN index on `to_tsvector('english', content)` is
used by the match predicate; before the predicate existed it was unreachable.

**Score floor.** `rag_min_score` applies to dense cosine similarity only. A document found purely
by keyword has no dense similarity and is not floored — that is the purpose of a second channel.

**Two earlier defects, both fixed.** The keyword branch previously selected `k` rows with no match
predicate and no ordering, returning arbitrary documents (four rows, all `ts_rank = 0.0`, sequential
scan truncated at `LIMIT 4`). The fusion arithmetic is the second, described above.

**Acceptance test.** `scripts/experiments/rrf_acceptance.py` seeds a corpus whose embeddings are
decoupled from its text, sends the same query vector twice — with and without `query_text` — and
requires the target document to appear in the hybrid result and nowhere in the dense-only result.
It calls the real `RetrievalService`, so it fails if the shipped path regresses.

**Limit that remains.** With the mock trigram-hash embedder, lexical and dense signals are
correlated — a distinctive token drives both channels — so channel separation cannot be
demonstrated against the shipped embedder. The acceptance test therefore uses random embeddings to
exercise the fusion logic, not semantic retrieval quality.

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
- **Generation may use a different provider from embeddings.** `LLM_BASE_URL`, `LLM_API_KEY` and
  `LLM_MODEL` fall back to the `OPENAI_*` settings when unset, so single-provider deployments need no
  extra configuration. With them set, embeddings can stay on one vendor while generation runs on
  another — verified in [live provider evidence](../evidence/live-provider.md) with OpenAI and
  DeepSeek.
- Each provider response's `usage` object and model identity are returned with the answer and recorded
  in the `model.generate` span, so token counts and cost are measurable per run.

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

**Live answers are gated structurally.** The gate dispatches on `execution_mode`: mock datasets keep
the binary fixture predicates, live datasets use `live-structural-v1`, which requires a provider
answer, non-zero token usage, an exact citation match and the fixture phrases present. No model judge
is in the gate, because a measured judge scored a source-identical answer 0.333 faithful, so quality
scores report but do not decide.

A controlled run with the same dataset and the same gate code blocked all three cases when the service
answered from the deterministic fallback, and passed them when it answered from a provider. See
[live gate evidence](../evidence/gate-live.md).

**Not wired into the release CLI.** Release bundles stay mock-only, because provider credentials must
not enter a manifest designed to be published and hash-verified. The live gate is a pre-release check
against a running deployment, not part of the bundle.

**Offline evaluation uses the RAGAS library**, pinned at `0.4.4` in `services/evaluator`. It scores
live answers on faithfulness, answer relevancy, context precision and recall, and answer correctness.
It sits deliberately **outside** the promotion gate: on six samples it scored a source-identical
answer 0.333 faithful and a correct answer 0.0 relevant, reproducing across runs. A metric that
disagrees with ground truth that plainly cannot decide promotion. See
[RAGAS evaluation evidence](../evidence/ragas-eval.md).

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

### 10.1 Dependency pinning

Every base image is pinned by tag and manifest-list digest, so a tag moving upstream cannot change a
build:

| Image | Used by |
|---|---|
| `python:3.12-slim@sha256:5e2bdb1b…` | `rag-api`, `evaluator`, and the `verify` steps |
| `node:22-alpine@sha256:1314fb30…` | `gateway`, and the `verify` node step |
| `mcr.microsoft.com/dotnet/sdk:8.0@sha256:1b955f17…` | `dotnet-ingest` build stage |
| `mcr.microsoft.com/dotnet/aspnet:8.0@sha256:dccacae9…` | `dotnet-ingest` runtime stage |
| `pgvector/pgvector:0.8.2-pg16@sha256:00ba258a…` | Compose, `release.py`, `verify` |

Language dependencies:

| Ecosystem | Pinning | Enforced by |
|---|---|---|
| Python | exact `==` versions in `requirements*.txt` | image build and `verify` install those files |
| .NET | `packages.lock.json` for both projects | `RestorePackagesWithLockFile` in `Directory.Build.props`; `--locked-mode` in CI and `verify` |
| Node | `package-lock.json` | `npm ci` in CI and `verify` |

**Regenerate the .NET lock files after changing a package reference:**

```sh
docker run --rm -v "$PWD:/src" -w /src/services/dotnet-ingest \
  mcr.microsoft.com/dotnet/sdk:8.0@sha256:1b955f179b02df4f0eb5fadedaa898cbe44d620a64aab52eec72c8e91d1355b5 \
  sh -c "dotnet restore src/IngestApi/IngestApi.csproj --use-lock-file && \
         dotnet restore tests/IngestApi.Tests/IngestApi.Tests.csproj --use-lock-file"
```

Without that step, `--locked-mode` fails with `NU1004` naming the package whose version the lock file
does not contain. That failure is deliberate: it is the difference between a dependency change and a
dependency drift.

**What pinning does not establish.** Python pins are version constraints without hashes, so artefact
integrity is not verified. NuGet packages are pinned by version, not signature. Base digests prevent
tag movement; they do not prove provenance. The build is reproducible in *inputs*, not bit-identical
in *outputs*.

### 10.2 Token economics

Model calls consume tokens rather than instance-hours, so they need the same treatment cloud resources
get from FinOps tagging: every call records what it spent and which resource caused it.

Every provider call emits one `token.usage` record carrying three groups of fields:

| Group | Fields |
|---|---|
| Attribution | `workload`, `environment`, `component`, `operation`, `endpoint`, `model`, `provider`, `mode` |
| Measurement | `prompt_tokens`, `completion_tokens`, `total_tokens` |
| Pricing | `rate_version`, `input_per_million`, `output_per_million`, `cost_usd` |

`endpoint` is taken from the HTTP route handling the request, so the cost of answering `/rag` is
distinguishable from the cost of serving `/ingest`. `trace_id` joins a spend record to request
telemetry.

`label` attributes spend to a **named run**: a client sends `x-run-label` and every model call in that
request records it. That is what makes cost-per-accepted-answer computable — usage and gate outcomes join
on the label instead of being matched by timestamp. A request without the header records `label: null`,
so unattributed spend stays visible rather than folding silently into a total.

Labels are validated against an identifier alphabet and dropped if malformed, so a header cannot inject
arbitrary content into the ledger. They are client-supplied and unauthenticated: a client can misattribute
its own spend, which is acceptable for attribution and not for billing.

**The rate is stored with the record.** Recomputing cost from today's prices would rewrite history
every time a provider changed them; storing the rate and its table version means past entries keep
reporting what they actually cost.

**Unpriced calls are loud.** A model absent from `token_rates.json` is recorded with `cost_usd: null`
and a reason, and every rollup reports `unpriced_calls` separately. A total that silently excludes what
it could not price looks complete and is not.

Cost is queryable through `GET /costs?group_by=operation|endpoint|model|provider|mode|component|…`.
Mock calls are excluded by design: the ledger is a cost ledger, and zero-cost rows make every average
harder to read.

Measured on one live run — twelve calls, $0.00005446 — `/rag` accounted for **98.2%** of spend while
all nine embedding calls together cost 2.5%. See
[token economics evidence](../evidence/token-economics.md).

**Judge spend is included.** RAGAS drives its judge through LangChain, which bypassed the service's HTTP
client entirely; a registered callback handler now emits one record per judge call, tagged
`component: evaluator`, `operation: eval.judge` and the run label. In a six-sample evaluation the 60
judge calls were 98.4% of the run's cost, and the run became queryable by label rather than reported as
a single total.

Two independent measurements exist for that run — the callback's own cost total and the ledger — and
they disagree by 6% on identical token counts. Both are recorded; the ledger is authoritative because it
stores the rate used. A single number would have hidden the disagreement.

Dated model snapshots such as `gpt-4o-mini-2024-07-18` resolve to their family alias for pricing, while
the reported name is retained for attribution.

**Judge calibration, measured against 48 blind human labels.** Cohen's kappa is **0.508** (95% CI
0.27–0.75, moderate) — usable for reporting, **not** for gating. That is now the empirical basis for
keeping quality scores out of the promotion decision, rather than a preference.

The shape matters more than the aggregate. The judge agrees perfectly on contradiction — cross-document
answers, negated statements and wrong-context text all scored 1.0 agreement — and fails consistently on
appended claims: qualifications the context does not support were accepted **6 times out of 6**.

Bias probes: position bias was not observed; the verbosity probe saturated at 1.0 for every item and
therefore measured nothing — the calibration run found that effect where the probe could not. Single
annotator, so this is judge-versus-author, not inter-annotator agreement. See
[judge calibration evidence](../evidence/judge-calibration.md).

**Enforced before spending.** Three guards run before the provider call: a completion cap sent to the
provider as `max_tokens`, a prompt ceiling that rejects rather than truncates, and an allowlist that
permits only models present in the rate table. Guards raise rather than returning a fallback answer,
because a fallback still produces text that a client ignoring `fallback: true` would ship — `400` for
correctable input, `503` for configuration.

A truncated answer (`finish_reason == "length"`) is surfaced as `truncated`, and the live acceptance
gate rejects it: a partial answer cannot be promoted.

A per-run budget (`--max-cost-usd`, default $0.05) aborts with a partial receipt and a non-zero exit.
The judge path is bounded from inside the LangChain callback, so an evaluation cannot overspend before
being checked.

Worst case per call at the caps is $0.0015, so a twelve-call run cannot exceed $0.018 — below the run
budget by design. See [cost guard evidence](../evidence/cost-guards.md).

#### Unit economics reporting

The gate harness writes a `run.outcome` entry into the same ledger when a run finishes, carrying
`label`, `policy`, `attempted`, `accepted` and `passed`. Usage and outcomes are therefore two event
types in one file, and rollups filter on `event`, so adding outcomes does not disturb cost queries.

That makes three measures computable by joining on `label` rather than by matching timestamps:

| Measure | Numerator | Denominator | Degenerate case |
|---|---|---|---|
| Cost per accepted answer | run spend | accepted cases | Undefined when nothing was accepted — reported as absent, not infinite |
| Cost per 1k tokens | spend | tokens ÷ 1000 | Reported **per model**: one blended rate describes no model |
| Spend share | model or endpoint spend | total spend | Unpriced calls counted separately so shares do not silently total 100% |

`scripts/experiments/unit_economics.py` produces the report from a ledger. Spend and outcomes that do
not join are listed as separate sections, because spend that cannot be attributed to an accepted run is
a fact a cost report has to show — folding it into the total would bias cost-per-accepted downward, in
the flattering direction.

Measured on one gated run of three fixtures: 12 calls, 371 tokens, **$0.000019953 per accepted answer**
and $0.000161 per 1k tokens, with `gpt-4o-mini` at 98.1% of spend and embedding calls at 1.9%. A
second run recorded spend with no outcome and appears as unjoined. See
[unit economics evidence](../evidence/unit-economics.md).

**A failed run is written too.** A run that spent money and accepted nothing is precisely the row a cost
report needs; omitting it would flatter every average that followed.

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

One entry point runs every offline suite:

```sh
python scripts/verify.py            # all suites in Docker; authoritative
python scripts/verify.py --local    # host toolchains, skipping what is unavailable
python scripts/verify.py --with-db  # adds the retrieval acceptance test
python scripts/verify.py --list     # print the step table and exit
```

It mirrors `.github/workflows/ci.yml` and adds schema-generation determinism and retrieval
acceptance, neither of which CI runs. A step is skipped only when its probe genuinely fails — the
capability is tested, not the presence of a binary name — and skips are reported rather than counted
as passes.

The CI job structure, its measured timings, and the optimisations that were adopted, rejected and
removed are recorded in [ci.md](ci.md).

**Not run by `verify`:** the live-provider receipt (`scripts/experiments/live_receipt.py`). It needs an
API key and spends money, so it is a deliberate manual step whose output is recorded in
[evidence/live-provider.md](../evidence/live-provider.md).

**The tool has its own tests** (`tests/test_verify.py`, 18 cases). While it was being built it
reported the wrong thing four times: a missing tool that was installed, a test failure that was
actually an environment limitation, checks skipped inside an environment that could run them, and a
hang caused by a test invoking the suite that contained it. Each incident is now a named test.

That matters more here than elsewhere. A verification tool that silently skips work produces a false
green, and a false green is never investigated — the failure surfaces later, somewhere less
convenient. Incidents and mitigations are recorded in the briefing document's Part VI-B.

The session drills (`tests/integration_o02.py`, `o03`, `o04`) are separate: they build and start
real containers and deliberately introduce failures. Run them sequentially.

See `EVIDENCE.md` for the full evidence index.
