# Token economics evidence

**Date:** 17 November 2026
**Source revision:** `8987afa` plus the attribution code in this change
**Grade:** E4 — dated run against a live provider, with attributed spend
**Rate table:** `services/rag-api/app/core/token_rates.json`, version `2026-11-17`

The cloud analogue is FinOps tagging: every instance carries cost-centre, workload and owner so spend
can be attributed and argued about. Model calls consume tokens instead of instance-hours, and until
now this project recorded tokens **per run** — enough to price an experiment, not enough to answer
which endpoint caused the spend.

This makes model spend attributable to the resource that caused it.

---

## The record

Every provider call emits one `token.usage` event and appends it to a ledger. A real entry:

```json
{
  "event": "token.usage",
  "ts_utc": "2026-11-17T01:29:40.389476+00:00",
  "trace_id": "85004b36cb9c4fdf80e25669f22feb23",
  "workload": "ai-platform-proof",
  "environment": "local-dev",
  "component": "rag-api",
  "operation": "embedding.embed",
  "endpoint": "/ingest",
  "model": "text-embedding-3-small",
  "provider": "openai",
  "mode": "provider",
  "prompt_tokens": 11,
  "completion_tokens": 0,
  "total_tokens": 11,
  "rate_version": "2026-11-17",
  "input_per_million": 0.02,
  "output_per_million": 0.0,
  "cost_usd": 2.2e-07,
  "unpriced_reason": null
}
```

Three groups of fields, mirroring how a cost-allocation tag set is structured:

| Group | Fields |
|---|---|
| **Attribution** | workload, environment, component, operation, endpoint, model, provider, mode |
| **Measurement** | prompt, completion, total tokens |
| **Pricing** | rate version, input rate, output rate, computed cost |

`trace_id` joins a spend record to the request telemetry, so a cost line can be traced back to the
call that produced it.

## Two design decisions worth defending

**The rate is stored with the record.** A ledger that recomputes cost from today's prices rewrites
history every time a provider changes them. Storing `rate_version` and the per-million rates means
last month's entries still report what they actually cost. This is the same reason FinOps bills store
the price list, not just the usage.

**An unpriced call is loud, not silent.** An unknown model is recorded with `cost_usd: null` and a
reason, and every rollup reports `unpriced_calls` separately. A total that quietly excludes what it
could not price looks complete and is not.

---

## The measurement

One live run: three documents ingested, three searches, three answers. Twelve provider calls.

### By operation

| Operation | Calls | Prompt | Completion | Total tokens | Cost |
|---|---:|---:|---:|---:|---:|
| `rag.answer` | 3 | 238 | 29 | 267 | $0.0000531 |
| `embedding.embed` | 9 | 68 | 0 | 68 | $0.00000136 |
| **Total** | **12** | **306** | **29** | **335** | **$0.00005446** |

### By endpoint

| Endpoint | Calls | Total tokens | Cost | Share |
|---|---:|---:|---:|---:|
| `/rag` | 6 | 287 | $0.0000535 | **98.2%** |
| `/ingest` | 3 | 28 | $0.00000056 | 1.0% |
| `/search` | 3 | 20 | $0.0000004 | 0.7% |

### By model and provider

| Key | Calls | Cost | Share |
|---|---:|---:|---:|
| `gpt-4o-mini` | 3 | $0.0000531 | 97.5% |
| `text-embedding-3-small` | 9 | $0.00000136 | 2.5% |
| `openai` (provider) | 12 | $0.00005446 | 100% |

### Unit economics this enables

| Measure | Value |
|---|---|
| Cost per `/rag` request | **$0.0000178** |
| Cost per generation call | $0.0000177 |
| Cost per embedding call | $0.00000015 |
| Embedding share of total spend | 2.5% |
| Unpriced calls | 0 |

The useful finding is the shape, not the amounts: **nine embedding calls cost less than one generation
call.** Optimising the retrieval path in this configuration is pointless; the generation call is where
essentially all spend sits. That is the kind of statement a cost ledger is for, and it could not be
made from per-run totals.

---

## Judge spend through LangChain — closing the black hole

RAGAS drives its judge through LangChain, so those calls never passed through the service's HTTP client
and never reached the ledger. Their cost existed only as one aggregate at the end of a run:
measurable afterwards, unattributable during it.

A `BaseCallbackHandler` is now registered on the judge model
(`services/evaluator/evaluator/langchain_usage.py`). Every completion LangChain performs becomes a
`token.usage` record with `component: evaluator`, `operation: eval.judge`, the run label and the model
— same ledger, same schema as the service.

### Measured

One RAGAS run, six samples, five metrics:

| Component | Calls | Prompt | Completion | Total tokens | Cost |
|---|---:|---:|---:|---:|---:|
| `evaluator` | 60 | 42,000 | 3,043 | 45,043 | $0.0081258 |
| `rag-api` (same run) | 18 | 708 | 66 | 774 | $0.00013098 |
| **Combined** | **78** | — | — | **45,817** | **$0.00825678** |

Judge spend is **98.4%** of the run's total cost. Before this change it was invisible in the ledger and
appeared only as a single number after the fact; now each of the 60 calls carries its own attribution
and the run is queryable by label:

```
GET /costs?group_by=label
  langchain-check   60 calls   $0.0081258
  (none)            18 calls   $0.00013098
```

### Two findings worth keeping

**The provider reports the dated snapshot, not the alias.** OpenAI returns
`gpt-4o-mini-2024-07-18`, while the rate table is keyed on `gpt-4o-mini`. Every judge call was recorded
**unpriced** with the reason attached — loud, not silent. Pricing now resolves a trailing
`-YYYY-MM-DD` to its family alias, so attribution keeps the reported name while cost uses the alias
rate. This is precisely the failure the "unpriced is loud" rule exists to surface: without it, sixty
calls would have been counted at zero and the run would have looked cheap.

**Two independent measurements disagree by 6%.** For the same 60 calls and the identical token count
(45,043), the LangChain callback reports **$0.007665** and the ledger reports **$0.008126**. Token counts
match exactly, so the difference is rate application — the callback's figure implies an effective rate
below the published list, most likely cached-input pricing.

Both are recorded. The ledger figure is treated as authoritative because it stores the rate and its
version with each record; the callback figure is retained as a cross-check. A single number would have
hidden the disagreement.

---

## What this establishes

- Every provider call is attributable to workload, environment, component, operation, endpoint, model,
  provider and mode
- Judge spend from RAGAS is captured per call through a LangChain callback, so an evaluation run is
  attributed rather than aggregated
- Cost is computed from a versioned rate table and stored with the record, including for dated model
  snapshots which resolve to their family alias
- Embedding spend — previously excluded from the live receipt — is now captured and priced
- Rollups answer "which endpoint, operation, model, provider or label spent this" via `GET /costs`
- Unpriced calls are counted separately rather than dropped, which is how the snapshot-name problem was
  found

## What it does not establish

- **A production store.** The ledger is an append-only local JSONL file. There is no rotation,
  retention policy, cross-process locking guarantee or query index. It answers questions at
  project scale, not fleet scale
- **Budget enforcement.** Cost is measured, not capped. A runaway run is visible afterwards, not
  prevented — this is the C6 gap and it is unchanged. Note that the LangChain handler does observe every
  judge call *during* a run, so a per-run budget could now abort mid-run rather than only inspect
  afterwards; that is the design C6 should take
- **Agreement between measurement methods.** The callback and the ledger differ by 6% on the same run.
  The ledger is treated as authoritative because it stores the rate used, but neither is an invoice
- **Tenant or end-user attribution.** The dimensions stop at endpoint. Charging back to a consumer
  would need one more tag
- **Currency or tax handling.** USD, per provider, before any discount, credit or regional uplift
- **That mock calls are free.** They are excluded by design; the ledger is a cost ledger, and
  zero-cost rows would make every average harder to read

---

## Reproduce

```sh
docker network create tokenecon
docker run -d --name te-pg --network tokenecon \
  -e POSTGRES_USER=ai -e POSTGRES_PASSWORD=ai -e POSTGRES_DB=aiplatform \
  pgvector/pgvector:0.8.2-pg16@sha256:00ba258a66dac104fd5171074a0084462a64a1369d8513f3d0a634e2f24d15bc

docker run -d --name te-api --network tokenecon -p 8098:8000 \
  -v "$PWD:/src" -e TOKEN_LEDGER_PATH=/src/artifacts/token-ledger.jsonl \
  -e DATABASE_URL=postgresql+psycopg://ai:ai@te-pg:5432/aiplatform \
  -e EMBEDDING_MODE=openai -e OPENAI_API_KEY="$OPENAI_API_KEY" \
  -e OPENAI_BASE_URL=https://api.openai.com/v1 -e LLM_MODEL=gpt-4o-mini \
  ai-platform-proof-rag-api

curl "http://localhost:8098/costs?group_by=endpoint"
curl "http://localhost:8098/costs?group_by=model"
```

The ledger lands in `artifacts/token-ledger.jsonl`, which is git-ignored.
