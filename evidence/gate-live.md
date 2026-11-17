# Live-mode gate evidence

**Date:** 17 November 2026
**Source revision:** `eb7a556` plus the gate code in this change
**Grade:** E4 — dated run against a live provider, with a controlled negative
**Policy:** `live-structural-v1` (`scripts/release/gate-policy-live.json`)

Two runs, identical dataset and identical gate code. The only difference is the mode the service runs
in. One passes; one is blocked. That is the evidence that the gate discriminates.

---

## Why live mode needs different predicates

Mock mode gates on binary fixture predicates, including token containment: the deterministic answer
is a truncation of its contexts, so every token of it is found in them. **A model answer is not a
substring of its contexts**, so the same test simply fails correct answers.

Rather than weaken the mock predicate, live mode gets its own set — the dual-predicate decision
recorded as C1. Live mode gates on properties that are deterministic:

| Check | What it establishes |
|---|---|
| `provider_answer` | The answer came from a provider, not the deterministic fallback |
| `not_fallback` | `fallback` is false and the answer is not blocked |
| `usage_present` | The provider returned token usage, proving the call was real |
| `sources_valid` | Cited sources equal the retrieved set exactly, and the expected source is cited |
| `required_phrases` | Fixture phrase present, answer non-empty |
| `forbidden_phrases` | Forbidden phrase absent |

**No model judge is in the gate.** Measured the same day, a judge scored an answer character-identical
to its source 0.333 faithful (`evidence/ragas-eval.md`). A gate built on that would block correct work.

`min_pass_ratio` is `1.0`: every case must satisfy every required check, or the gate fails closed.

---

## Run A — service in provider mode

```
policy        live-structural-v1
gate_passed   true
pass_ratio    1.0
hard_failures []
cases         refund=provider, software=provider, meals=provider
```

All three acceptance cases passed every structural check against real provider answers.

## Run B — service in mock mode (controlled negative)

Same dataset. Same gate code. The service was started with `EMBEDDING_MODE=mock`, so answers come from
the deterministic fallback.

```
gate_passed   false
pass_ratio    0.0
failures      refund, software, meals
failed checks provider_answer, not_fallback, usage_present
```

All three cases blocked. Exit code non-zero.

This is the important half. A gate that only ever passes has not been shown to gate anything — the
same argument as `NU1004` for the dependency lock and the negative candidate in O02.

---

## What this establishes

- The gate dispatches on `execution_mode`: mock datasets keep binary fixture predicates, live datasets
  use the structural set
- A live candidate that answers from a real provider passes; one that silently answers from the
  deterministic fallback is blocked and the reason is named
- The predicates are deterministic, so a gate means the same thing across runs

## What it does not establish

- **Answer quality.** Nothing here tests whether the answer is correct, only that it came from a
  provider, cites its sources, and satisfies fixture phrases
- **A release-path integration.** The release CLI still builds mock-only bundles. Live gating runs
  against a running service, not inside a bundle, because **provider credentials must not enter a
  release manifest** — a manifest is designed to be published and hash-verified. Running the live gate
  as a pre-release check against a staging deployment is the correct pattern, and it is not wired up
- **Load, concurrency or cost ceilings** at gate time
- **That a hostile candidate cannot satisfy the predicates.** They are structural; a candidate that
  echoes fixture phrases would pass. The gate is a control, not an adversary

---

## Reproduce

```sh
docker network create gatecheck
docker run -d --name gate-pg --network gatecheck \
  -e POSTGRES_USER=ai -e POSTGRES_PASSWORD=ai -e POSTGRES_DB=aiplatform \
  pgvector/pgvector:0.8.2-pg16@sha256:00ba258a66dac104fd5171074a0084462a64a1369d8513f3d0a634e2f24d15bc

# provider mode -> expect pass
docker run -d --name gate-live --network gatecheck \
  -e DATABASE_URL=postgresql+psycopg://ai:ai@gate-pg:5432/aiplatform \
  -e EMBEDDING_MODE=openai -e OPENAI_API_KEY="$OPENAI_API_KEY" \
  -e OPENAI_BASE_URL=https://api.openai.com/v1 -e LLM_MODEL=gpt-4o-mini \
  ai-platform-proof-rag-api

docker run --rm --network gatecheck -v "$PWD:/src" -w /src \
  -e RAG_API_URL=http://gate-live:8000 python:3.12-slim \
  python scripts/experiments/live_gate_check.py --label provider-mode

# mock mode -> expect failure on provider_answer, not_fallback, usage_present
```

The gate needs no third-party libraries: it is standard-library Python plus the request injection
point, which is why it runs in a bare `python:3.12-slim`.
