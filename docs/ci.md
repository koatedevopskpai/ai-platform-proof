# CI: feedback latency and failure attribution

The workflow is four jobs plus the offline contract gate. This file records the measurements behind its
shape, so each optimisation is explicit and each rejected option says why.

Cost is deliberately not the driver: this repository is **public**, so Actions minutes are free. The
metric that matters is how quickly a failure is reported and whether it names the right component.

---

## Baseline — measured 17 November 2026

Run `44006402610`, one `Python build + test` job:

| Job | Wall | Breakdown |
|---|---:|---|
| `Python build + test` | **59s** | checkout 4s · setup 1s · **rag-api deps 14s** · rag-api tests 2s · **evaluator deps 32s** · evaluator tests 3s |
| `.NET build + test` | 16s | restore 4s · build 5s · test 2s |
| `TypeScript build + test` | 10s | `npm ci` 1s · typecheck 2s · build 1s |
| `release-contract` | 6s | — |

Workflow wall time is the slowest job: **59s**. Two facts:

- **78% of the Python job is dependency installation** (46s of 59s). Tests are 5s.
- **ragas is 32s on its own** — 54% of the job.

---

## O1 — Split `python` into `rag-api` and `evaluator` — **adopted**

| | Before | After (typical) | After (observed range) |
|---|---:|---:|---:|
| `Python — rag-api` | (shared) | **14s** | **14–20s** |
| `Python — evaluator` | (shared) | **53s** | **38–66s** |
| Workflow wall | 59s | **53s** | **38–66s** |

**Primary reason is attribution, not speed.** A ragas installation failure and a rag-api test failure
previously produced the same red check called "Python build + test". After the split, the failing
component is in the job name.

**The latency claim has to be careful.** The rag-api job is consistently fast — 14s and 19s across runs
— so **service-test feedback moved from 59s to roughly 15s**, which is real and repeatable. The
evaluator job is not: identical inputs produced 38s, 53s and 66s. Wall time therefore varies with it, and
no claim about a 6-second improvement survives that variance. See "Variance" below.

**Cost:** one extra job. Free on a public repository. If this were private, the split would still be
worth its ~15% increase in minutes for the attribution gain.

**Note for later:** check names changed from `Python build + test` to `Python — rag-api` and
`Python — evaluator`. Any branch protection rule or status badge referring to the old name must be
updated.

## O2 — Cache pip downloads — **adopted, measured, removed**

Dependency installation dominates, so caching looked obvious. It did not work, and the reason is worth
recording.

| Run | Cache state | Evaluator install |
|---|---|---:|
| No cache | — | 32s (baseline) / 43s (post-split) |
| Cache enabled, populate | miss | 28s |
| Cache enabled, re-run | **hit** (`Cache restored successfully`) | **43s** |

The cache **engaged** — the log confirms `Cache restored from key: setup-python-…-pip-f65056cd…` — and
the install time did not improve. The ranges overlap completely (28–43s with cache, 32–43s without).

**Cause:** caching pip avoids the *download*, but the install cost here is dominated by unpacking large
scientific wheels — scipy, pyarrow, pandas, numpy. The network was never the bottleneck.

**Decision:** removed. An optimisation that cannot be shown to help does not belong in the workflow; it
is one more thing to maintain and one more false belief about why CI is slow.

## O3 — Pre-built image with dependencies baked in — **rejected**

Would cut installation to near zero, and was rejected for a specific reason: the Dockerfiles install from
the same pinned requirement files, and CI is currently proving that **those pins resolve**. Baking an
image moves that proof to image-build time and hides dependency drift behind an image tag.

## O4 — `uv` instead of `pip` — **rejected**

`uv` resolves and installs considerably faster and would plausibly beat the pip path. Rejected because it
diverges from the install path the Dockerfiles and `verify.py` use: CI would then be testing a resolution
that production does not perform. The reliability of the claim matters more than the wall clock here.

Reconsider if `uv` is adopted across the images, at which point CI should follow rather than lead.

## O5 — Cache NuGet packages — **deferred**

`dotnet restore` is 4–8s. A second cache key for a few seconds is poor value, and `.NET` is not on the
critical path. Revisit only if restore time grows.

`TypeScript` already caches npm through `actions/setup-node` — that is why `npm ci` takes 1s.

---

## Result

| Metric | Before | After |
|---|---:|---:|
| Workflow wall time | 59s | 38–66s, driven by evaluator install variance |
| rag-api test feedback | 59s | **14–20s** |
| Failing component identifiable from the check name | no | **yes** |
| Cache keys to maintain | 1 (npm) | 1 (npm) |

The large number here is not seconds saved. It is that a service-test failure is now reported in around
15 seconds, and that the check name says which component broke.

## Variance — why most of these numbers are not precise

Identical inputs, same runner image, same commit tree, produced these `Install evaluator deps` durations:
**28s, 43s, 66s**. That is a 38-second spread with nothing changed but the runner's mood.

Consequences, which apply to any future CI optimisation here:

- A difference under roughly **15 seconds** cannot be attributed from a single run.
- Median of three runs is the least one can defensibly quote, and even then the range should be published.
- Any optimisation whose mechanism does not explain the saving is probably variance. The pip cache is the
  worked example: it engaged, install stayed in the same range, so it was removed.

The split's benefit is **structural** — a faster job exists and the failure is named — and that does not
depend on timing at all.

## How to measure next time

```sh
# job and per-step durations for a completed run
gh api "repos/<owner>/<repo>/actions/runs/<run-id>/jobs?per_page=20" \
  --jq '.jobs[] | {name, conclusion, started_at, completed_at, steps: [.steps[] | {name, started_at, completed_at}]}'
```

Compare like with like: runner network speed varies enough that a single run can be 15s out. A change of
under ~5s is not measurable this way and should not be claimed.
