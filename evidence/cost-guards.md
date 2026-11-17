# Cost guard evidence

**Date:** 17 November 2026
**Source revision:** `2210501` plus the guard code in this change
**Grade:** E4 — dated run against a live provider, with controlled failures

Token spend was measurable but unbounded. A runaway run was visible afterwards, not prevented. Three
guards now stop it before the money is spent.

---

## The guards

| Guard | Setting | Failure mode it stops |
|---|---|---|
| Completion cap | `llm_max_completion_tokens` = 512 | One call producing a runaway answer |
| Prompt ceiling | `llm_max_prompt_tokens` = 8,000 | One enormous context costing more than the whole run |
| Model allowlist | `llm_model_allowlist` = `gpt-4o-mini,deepseek-flash` | An accidental expensive model |
| Priced-only | `token_require_priced_model` = true | Spend that cannot be attributed — unbudgetable by definition |
| Per-run budget | `--max-cost-usd` = $0.05 | A loop or large dataset |

**Reject, never truncate.** An oversized prompt is refused rather than trimmed. A trimmed prompt
produces an answer that looks fine and is not — the same failure class as the scoring threshold that
would have silently emptied retrieval.

**Guards raise.** They do not return a fallback answer. A fallback still produces text, and a client
that ignores `fallback: true` would ship it. `GenerationBlocked` propagates to the HTTP layer: `400`
for client-correctable input, `503` for configuration.

## Thresholds, and where the numbers come from

| Setting | Value | Basis |
|---|---|---|
| `llm_max_completion_tokens` | 512 | Largest completion observed is 131 tokens (DeepSeek). 512 is ~4× that |
| `llm_max_prompt_tokens` | 8,000 | Observed prompt is ~80–120 per call; 8k covers ~8 documents of 1k tokens |
| Per-run budget | $0.05 | ~900× a normal live run ($0.00005446), ~6.5× a RAGAS run ($0.0077) |

Worst case per call at the caps on `gpt-4o-mini`:

```
8,000 × $0.15/1M  +  512 × $0.60/1M  =  $0.0015
```

Twelve calls at maximum = **$0.018**, under the $0.05 run budget. The two limits are deliberately
consistent: a run budget below the sum of per-call caps would abort legitimate work.

---

## Validation

Each guard was exercised against a running service, not only unit-tested.

### Prompt ceiling

Service started with `LLM_MAX_PROMPT_TOKENS=1`:

```
POST /rag  →  HTTP 400  {"detail": "prompt_too_large"}
```

No provider call was made, so the refusal cost nothing.

### Truncation

Service started with `LLM_MAX_COMPLETION_TOKENS=5`:

```
answer:      "The refund window is"
truncated:   true
mode:        provider
```

The cap works, the answer is visibly partial, and `finish_reason == "length"` is surfaced rather than
inferred from a short string.

**And the gate rejects it.** The live acceptance gate added a `not_truncated` predicate; run against
the truncating service:

```
gate_passed:  false
pass_ratio:   0.0
cases:        refund, software, meals — all failing not_truncated
```

A truncated answer cannot be promoted. That is the difference between a bounded call and a bounded
*and* enforced one.

### Per-run budget

`live_receipt.py --max-cost-usd 0.0000005`:

```
token.budget warning: $0.000022 of $0.000000 used
aborted: {reason: budget_exceeded, spent_usd: 2.157e-05,
          limit_usd: 5e-07, after_case: refund}
exit code: 2
```

The run stops after the case that breaches the ceiling, writes a partial receipt, and exits non-zero.
The next call never happens — which is the point of a ceiling.

The LangChain judge path is bounded the same way, from inside the callback: `BudgetExceeded` is raised
mid-run, so an evaluation cannot overspend before being checked. That is only possible because the
callback sees every judge call.

---

## Two defects found while validating

Neither was the subject of the change; both surfaced because the guards were exercised against real
containers rather than only unit tests.

### Concurrent startup races on `CREATE EXTENSION`

Starting two instances against a fresh database:

```
sqlalchemy.exc.IntegrityError: duplicate key value violates unique constraint "pg_extension_name_index"
DETAIL: Key (extname)=(vector) already exists.
[SQL: CREATE EXTENSION IF NOT EXISTS vector]
```

`CREATE EXTENSION IF NOT EXISTS` is **not atomic** against concurrent DDL. Startup now takes an
xact-scoped advisory lock first, so ordering is explicit rather than lucky. Re-running the same
concurrent start succeeds.

### The gate was not re-runnable

Second gate run against a database that already held the fixtures:

```
urllib.error.HTTPError: HTTP Error 409: Conflict
```

Ingestion treats an existing fixture as an error, so a gate could only run once per database. It now
tolerates `409` and continues, because a gate that cannot be re-run is hard to trust and awkward to
demonstrate.

---

## What this establishes

- Three guards refuse before the provider call, so a breach spends nothing
- Completion is capped by the provider (`max_tokens`), not by trimming afterwards
- Truncation is detected, surfaced, and rejected by the acceptance gate
- A per-run budget aborts mid-run with a partial receipt and a non-zero exit
- The judge path is bounded from inside the LangChain callback

## What it does not establish

- **Invoice accuracy.** Cost is attributed from a rate table, not billed figures
- **Hard currency limits.** The budget is enforced per run by the tool that runs it; a client calling
  the service directly is bounded per call but not per session
- **Prompt estimation accuracy.** `len(prompt) // 4` is a heuristic. It is deliberately conservative
  for English prose and will misjudge code or non-Latin text
- **Nothing about answer quality.** These are cost controls; they say nothing about whether spending
  produced something good

---

## Reproduce

```sh
docker run -d --name cg-pg --network cgnet \
  -e POSTGRES_USER=ai -e POSTGRES_PASSWORD=ai -e POSTGRES_DB=aiplatform \
  pgvector/pgvector:0.8.2-pg16@sha256:00ba258a66dac104fd5171074a0084462a64a1369d8513f3d0a634e2f24d15bc

# prompt ceiling
docker run -d --name cg-tiny --network cgnet -p 8094:8000 \
  -e DATABASE_URL=postgresql+psycopg://ai:ai@cg-pg:5432/aiplatform -e EMBEDDING_MODE=openai \
  -e OPENAI_API_KEY="$OPENAI_API_KEY" -e LLM_MODEL=gpt-4o-mini -e LLM_MAX_PROMPT_TOKENS=1 \
  ai-platform-proof-rag-api

# truncation, then gate the result
docker run -d --name cg-trunc --network cgnet -p 8095:8000 \
  -e DATABASE_URL=postgresql+psycopg://ai:ai@cg-pg:5432/aiplatform -e EMBEDDING_MODE=openai \
  -e OPENAI_API_KEY="$OPENAI_API_KEY" -e LLM_MODEL=gpt-4o-mini -e LLM_MAX_COMPLETION_TOKENS=5 \
  ai-platform-proof-rag-api

python scripts/experiments/live_gate_check.py --rag-url http://localhost:8095 --label truncation-guard
```
