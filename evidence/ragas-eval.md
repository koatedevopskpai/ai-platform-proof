# RAGAS evaluation evidence

**Date:** 17 November 2026
**Source revision:** `eb7a556` plus the evaluation code in this change
**Grade:** E4 — dated run against external providers, with recorded output and cost
**Framework:** `ragas==0.4.4`, judge `gpt-4o-mini`, embeddings `text-embedding-3-small`

Six samples: the three acceptance fixtures plus three held-out cases that were not used to design
the promotion gate. Answers came from the **running service in provider mode**, not from a stored
dataset — scoring stored answers is the trap O02 documented, and the runner refuses to score
deterministic fallback output for the same reason.

---

## Result

| Metric | Aggregate |
|---|---|
| Faithfulness | **0.8889** |
| Answer relevancy | **0.7763** |
| Context precision | **1.0** |
| Context recall | **1.0** |
| Answer correctness | **0.8106** |

Per sample:

| Case | Split | Faithfulness | Answer relevancy | Answer correctness |
|---|---|---|---|---|
| `o02-refund` | acceptance | 1.0 | 0.8939 | **0.5058** |
| `o02-software` | acceptance | 1.0 | 0.9381 | 1.0 |
| `o02-meals` | acceptance | 1.0 | 1.0 | 1.0 |
| `heldout-travel` | heldout | 1.0 | **0.0** | 0.8956 |
| `heldout-hardware` | heldout | 1.0 | 1.0 | 0.9038 |
| `heldout-retention` | heldout | **0.3333** | 0.8259 | **0.5584** |

Cost: **60 judge requests, 45,091 tokens, $0.0077**, 86.5 seconds. Captured from the LangChain
callback because `evaluate()` does not return token usage.

Retrieval, with six documents and `top_k: 3`: the expected document ranked **first in all six
cases**. Unlike the earlier three-document run, presence was not guaranteed here, so this is a
meaningful result rather than a tautology.

---

## The scores disagree with the answers — three times

This is the part worth reading. The aggregates look acceptable. The per-sample data does not.

### 1. Faithfulness 0.3333 for a verbatim copy

```
document: Customer data must be deleted within 30 days of contract termination.
answer:   Customer data must be deleted within 30 days of contract termination.
score:    0.3333
```

The answer is character-identical to its retrieved context and RAGAS scores it one third faithful.

### 2. Answer relevancy 0.0 for a correct answer

```
question: Which travel class may I book for a three-hour flight?
answer:   You may book economy class for a three-hour flight.
score:    0.0
```

### 3. Answer correctness 0.5058 for a correct answer

```
reference: Full refunds are available within 30 days of purchase.
answer:    The refund window is 30 days from the date of purchase.
score:     0.5058
```

All three reproduced across two independent runs, so they are systematic judge behaviour rather than
noise. Two runs also produced **identical** scores for faithfulness, context precision, context
recall and answer correctness, and answer relevancy moved by only 0.006 in aggregate.

**Consequence:** these metrics cannot be used as a promotion gate. A gate that scores a correct,
source-identical answer at 0.33 would block good work; a gate tuned to pass it would pass almost
anything. This is the empirical case for the C1 decision — binary, mode-specific predicates — rather
than a preference.

It also means aggregate scores are misleading in isolation: reporting "faithfulness 0.89" without the
per-sample table would conceal a sample at 0.33.

---

## Methodological caveats found while building this

### `answer_relevancy` was running with one generation, not three

RAGAS asks the judge for `strictness` (default 3) candidate questions and averages. The LangChain
wrapper returns a single generation, so ragas warns *"LLM returned 1 generations instead of requested
3"* and silently scores from one sample. `strictness` is now set to 1 explicitly, so the code states
what happens rather than implying an average that was never computed. This makes the metric noisier —
another reason it is not a gate.

### The library is mid-migration, and the two APIs do not interoperate

`ragas==0.4.4` ships two metric systems:

| Import | Type | Accepted by `evaluate()`? |
|---|---|---|
| `ragas.metrics.collections.Faithfulness` | `BaseMetric` | **No** — "All metrics must be initialised metric objects" |
| `ragas.metrics.faithfulness` (legacy singleton) | `Metric` | Yes |

The collections metrics are the documented future and the legacy ones are deprecated for v1.0, but
`evaluate()` only accepts the legacy ones. Those in turn require the deprecated `LangchainLLMWrapper`
and `LangchainEmbeddingsWrapper`. The working combination on 0.4.4 is therefore legacy metrics plus
deprecated wrappers.

Both are pinned. Migrating to the collections API should be one deliberate change against a later
version, not a mixture. Code written from current tutorials or from the 0.2/0.3 era will not run
unmodified — it must be checked against the installed version, which is what happened here.

---

## What this establishes

- The RAGAS library is integrated, pinned, and produces reproducible scores on live answers
- Five metrics run end to end through a judge and embedding model with a custom `base_url`
- Judge cost is measured, not estimated: $0.0077 for 60 requests
- Retrieval rank-1 is correct across six documents

## What it does not establish

- **That the scores are right.** Three demonstrated false negatives
- **Judge calibration.** No human labels, no agreement metric, no bias checks. This is the next real
  gap, and the current UK postings ask for it explicitly (Cohen's kappa, position and verbosity bias)
- **Benchmark validity.** Six synthetic samples, one judge model, one point in time
- **A gate.** These scores do not control promotion and should not until they are calibrated
- **Provider portability of the judge.** Only OpenAI was tested; another provider's structured-output
  support is unverified

---

## Reproduce

```sh
# service in provider mode
docker run -d --name ragas-pg --network ragaseval \
  -e POSTGRES_USER=ai -e POSTGRES_PASSWORD=ai -e POSTGRES_DB=aiplatform \
  pgvector/pgvector:0.8.2-pg16@sha256:00ba258a66dac104fd5171074a0084462a64a1369d8513f3d0a634e2f24d15bc
docker run -d --name ragas-api --network ragaseval \
  -e DATABASE_URL=postgresql+psycopg://ai:ai@ragas-pg:5432/aiplatform \
  -e EMBEDDING_MODE=openai -e OPENAI_API_KEY="$OPENAI_API_KEY" \
  -e OPENAI_BASE_URL=https://api.openai.com/v1 -e LLM_MODEL=gpt-4o-mini \
  ai-platform-proof-rag-api

# evaluation
docker run --rm --network ragaseval --entrypoint python \
  -v "$PWD:/src" -w /src \
  -e RAG_API_URL=http://ragas-api:8000 \
  -e OPENAI_API_KEY="$OPENAI_API_KEY" -e OPENAI_BASE_URL=https://api.openai.com/v1 \
  ai-platform-proof-evaluator scripts/experiments/ragas_eval.py --label judge-gpt4o-mini
```

Expect approximately 60 judge requests, about $0.008, and roughly 90 seconds. Expect the three false
negatives above to reproduce.
