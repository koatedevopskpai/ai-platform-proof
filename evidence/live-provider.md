# Live provider evidence

**Date:** 16 November 2026
**Source revision:** `23de0d61bbf33edf5d73f5f3d5bb0fc9a12d34b2`
**Grade:** E4 — dated run against external providers, with recorded output and cost
**Purpose:** the artefact behind the question "where is the AI"

Two runs. Same platform, same fixtures, same embedding provider, different generation provider.

---

## What was run

| Run | Embeddings | Generation |
|---|---|---|
| **A** | `text-embedding-3-small`, 384 dimensions, OpenAI | `gpt-4o-mini`, OpenAI |
| **B** | `text-embedding-3-small`, 384 dimensions, OpenAI | `deepseek-flash`, DeepSeek |

Three synthetic acceptance fixtures, `temperature: 0`, one attempt each. No retries, no prompt
tuning between runs. Harness: `scripts/experiments/live_receipt.py`.

Embeddings stay on OpenAI in both runs because DeepSeek documents no embeddings endpoint. This is a
provider limitation, not a design choice.

---

## Results

### Run A — `gpt-4o-mini`

| Case | Rank-1 retrieved | Answer |
|---|---|---|
| refund | `o02-refund` | "The refund window is 30 days from the date of purchase." |
| software | `o02-software` | "Opened software is not refundable." |
| meals | `o02-meals` | "The daily meal expense limit is GBP 25." |

Tokens: 274 prompt / 29 completion / **303 total**.
Cost: **$0.000058** at $0.15 / $0.60 per 1M.
Transcript SHA-256: `7bbb2de5bc00c2532b28fb26aa0c6f3e…`

### Run B — `deepseek-flash`

| Case | Rank-1 retrieved | Answer |
|---|---|---|
| refund | `o02-refund` | "Within 30 days of purchase." |
| software | `o02-software` | "No. Opened software is not refundable." |
| meals | `o02-meals` | "GBP 25." |

Tokens: 350 prompt / 94 completion / **444 total**.
Cost: **$0.000218** at peak rates ($0.30 / $1.20 per 1M; off-peak is half).
Transcript SHA-256: `6a2195a259b12c01a869d3195f3dee23…`

All six answers contain the required fixture phrase and no forbidden phrase. Rank-1 retrieval was
correct in all six cases.

---

## Three observations worth keeping

### 1. Rank-1 retrieval is the meaningful part, not source presence

With three documents and `top_k: 3`, retrieval necessarily returns all three, so "the expected source
was present" is trivially true and proves nothing. What is non-trivial is that the expected document
ranked **first** in all six cases — that is the reciprocity between embedding and query working.

### 2. The cheaper model was terser, not worse

`deepseek-flash` answered in 7, 27 and 38 characters against `gpt-4o-mini`'s 55, 34 and 39. Both
satisfied the fixtures. With three cases this says nothing about general quality; it does say that
token count is not a proxy for completeness, and that a shorter answer passing the same predicate is
worth noticing rather than dismissing.

### 3. `temperature: 0` does not mean deterministic — measured, not assumed

Three runs of each configuration:

| Run | Transcript hash | Identical to previous? |
|---|---|---|
| A, run 1 | `7bbb2de5…` | — |
| A, run 2 | `7bbb2de5…` | yes |
| A, run 3 | `7bbb2de5…` | yes |
| B, run 1 | `6a2195a2…` | — |
| B, run 2 | `d04f721a…` | **no** |
| B, run 3 | `6a2195a2…` | **no** (differs from run 2) |

OpenAI reproduced the same transcript three times. DeepSeek produced two distinct transcripts across
three runs, differing in completion length (94, 120, 131 tokens).

**Consequence:** a transcript hash proves *what was said in that run*. It is not a reproducibility
guarantee, and no gate should be built on the assumption that a live answer is stable across calls.
This is why the gate decision (Part VI-C) keeps predicates binary and mode-specific rather than
averaging.

---

## Cost

| Run | Tokens | Cost | Rate basis |
|---|---|---|---|
| A | 303 | $0.000058 | OpenAI `gpt-4o-mini` $0.15 in / $0.60 out per 1M |
| B | 444 | $0.000218 | DeepSeek `deepseek-flash` peak $0.30 / $1.20 per 1M |

Rates read from the providers' own pricing pages on 16 November 2026. DeepSeek peak hours are
01:00–04:00 and 06:00–10:00 UTC on weekdays; off-peak is half these rates, so the figure above is the
conservative bound.

**Embedding cost is excluded.** The embedding client does not capture the provider's usage object, so
embedding tokens are not in these totals. At $0.02 per 1M for `text-embedding-3-small` and a few
hundred tokens, the omission is immaterial to the total but it is an omission, and it is recorded
rather than rounded away.

---

## What this establishes

- The generation path produces real answers from two different providers through the same service code
- Provider abstraction works: changing `LLM_BASE_URL`, `LLM_API_KEY` and `LLM_MODEL` moved generation
  from OpenAI to DeepSeek with no application change
- Token usage and model identity are captured per run, so cost is measurable rather than estimated
- Retrieval ranks the expected document first in every fixture

## What it does not establish

- **Semantic quality.** These are binary phrase checks. An answer can contain "30 days" and still be
  misleading; nothing here tests entailment
- **Gate behaviour in live mode.** The acceptance gate has not been run against a live answer. That is
  C5, and until it exists the live path has no promotion control
- **A benchmark.** Three synthetic fixtures, one model per provider, one attempt each
- **Determinism.** See observation 3
- **That DeepSeek can embed.** It cannot, per its documented API
- **Any production property.** No load, no concurrency, no retries, no rate-limit handling

---

## Not recorded, deliberately

No API key appears in this document, in the receipts, or in the harness output. The harness reads keys
from the environment and writes only provider hostnames, model names and token counts.

---

## Reproduce

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

For Run B, add `LLM_API_KEY`, `LLM_BASE_URL=https://api.deepseek.com`, `LLM_MODEL=deepseek-flash` and
the peak rates. Expect a different transcript hash — see observation 3.
