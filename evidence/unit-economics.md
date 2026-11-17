# Unit economics

**Date:** 17 November 2026
**Source revision:** `d6e61a7` plus the reporting code in this change
**Grade:** E4 — dated run, recorded artefact, reproducible from the ledger

Three measures, each with a denominator that has to be stated or the number is meaningless:

| Measure | Numerator | Denominator | Why the denominator matters |
|---|---|---|---|
| **Cost per accepted answer** | spend attributed to a run | cases that run accepted | Undefined when nothing was accepted — an infinite cost is not printable |
| **Cost per 1k tokens** | spend | tokens ÷ 1000 | Reported per model: a blended figure across different rates describes neither |
| **Spend share** | model or endpoint spend | total spend | Unpriced calls are counted separately, so the shares cannot silently sum to 100% on partial data |

Usage and outcomes are separate event types in one ledger, joined on `label`. That join is why
`x-run-label` exists.

---

## The example run

21 usage entries, 1 outcome, **$0.00011932** total.

### Cost per accepted answer

| Run | Calls | Tokens | Spend | Accepted | Cost / accepted | Cost / 1k tokens |
|---|---:|---:|---:|---:|---:|---:|
| `gate-run` | 12 | 371 | $0.00005986 | **3/3** | **$0.000019953** | $0.000161348 |

One gate run over three fixtures: twelve provider calls — three ingests, three searches, three answers,
three generations — at two hundredths of a cent per accepted answer.

### Spend share by model

| Model | Calls | Tokens | Spend | Share |
|---|---:|---:|---:|---:|
| `gpt-4o-mini` | 6 | 606 | $0.000117 | **98.1%** |
| `text-embedding-3-small` | 15 | 116 | $0.0000023 | 1.9% |

Six generation calls account for 98% of spend; fifteen embedding calls for under 2%. Same conclusion the
per-endpoint view gave earlier, now with an acceptance denominator attached.

### Spend share by endpoint

| Endpoint | Calls | Tokens | Spend | Share |
|---|---:|---:|---:|---:|
| `(none)` | 9 | 351 | $0.00005946 | 49.8% |
| `/rag` | 6 | 323 | $0.0000589 | 49.4% |
| `/ingest` | 3 | 28 | $0.00000056 | 0.5% |
| `/search` | 3 | 20 | $0.0000004 | 0.3% |

### Spend with no recorded outcome

| Label | Calls | Tokens | Spend |
|---|---:|---:|---:|
| `receipt-run` | 9 | 351 | $0.00005946 |

---

## Reading it honestly

**The `(none)` endpoint row is a limitation, not a bug.** Those nine calls came from
`live_receipt.py`, which calls the services in-process rather than over HTTP, so there is no route to
attribute them to. The report shows them rather than guessing.

**The unjoined run is the important row.** `receipt-run` spent real money and produced no gate outcome, so
it contributes nothing to cost-per-accepted. It is listed separately instead of being folded into the
total, because a cost-per-accepted figure that quietly includes unaccepted work is wrong in the flattering
direction.

**One rejected case would move the headline by a third.** With three fixtures, `3/3` and `2/3` differ by
50% in cost-per-accepted. This is a demonstration of the metric, not a workload measurement.

**`3/3` is a fixture result.** It says the gate accepted three synthetic cases, not that the answers were
good — the gate is structural, and the judge that could assess quality scored kappa 0.51
(`evidence/judge-calibration.md`).

---

## What this establishes

- The three unit measures are computable from committed tooling, not spreadsheet estimates
- Spend joins to acceptance outcomes on a run label, so `cost per accepted answer` has a real denominator
- Unattributed and unjoined spend stays visible in the report instead of disappearing
- The arithmetic is unit-tested, including the degenerate cases: zero accepted, zero tokens, unpriced
  calls, outcomes without spend

## What it does not establish

- **Invoice accuracy.** Cost comes from a rate table, not a bill
- **A production cost profile.** Three fixtures, one provider, one run
- **Chargeback.** Labels are client-supplied and unauthenticated
- **Durability.** The ledger is local JSONL with no rotation or retention; the report is reproducible
  from it, and lost if the file is lost

---

## Reproduce

```sh
python scripts/experiments/unit_economics.py \
  --ledger artifacts/token-ledger-econ.jsonl \
  --json-out artifacts/experiments/unit-economics.json \
  --markdown-out artifacts/experiments/unit-economics.md
```

The generator writes JSON and markdown. The ledger itself is git-ignored; this file is the committed
artefact, and the JSON beside it is regenerable.
