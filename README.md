# AI Platform Proof

Start with the [evidence index](EVIDENCE.md) for source, tests, dated results and limits, and the
[architecture document](docs/architecture.md) for how the system is actually built.

Public reference implementation: TypeScript gateway, Python RAG API and .NET ingestion service
backed by PostgreSQL/pgvector. Synthetic mock embeddings are the default. This repository does not
establish commercial production operation.

## How to read this repository

| If you want to… | Start here |
|---|---|
| Judge the engineering | [EVIDENCE.md](EVIDENCE.md) — dated results, then its "what this does not establish" section |
| Inspect the design | [docs/architecture.md](docs/architecture.md) — as built, including what is unsupported |
| Reproduce the checks | `python scripts/verify.py` — seven suites, about four minutes |
| See where the work went wrong | [docs/architecture.md](docs/architecture.md) §5 retrieval, §8 gate scope, §13 the verification tool |
| Deploy it | The supported release path below |

The claim is not that this is production software. It is that every statement here resolves to a
dated receipt or is explicitly marked unproven. Where measurement later contradicted the
documentation, the contradiction and the repair are recorded rather than tidied away.

## Supported release path

EC2 + Docker Compose with immutable image IDs and a checksummed image archive. The release receipt
records the source revision, configuration hash, expected and running image IDs and aggregated
backend health. See [O01](docs/O01.md). The manual `deploy-cheap.yml` builds once and transfers
that bundle over verified SSH; it does not pull source or build on the deployment host. Configure a
`compose-demo` environment and CHEAP_HOST, CHEAP_SSH_KEY, CHEAP_KNOWN_HOSTS secrets. The gateway
listens only on host loopback; use an SSH tunnel for this demo.

```sh
python scripts/release/release.py build --out /tmp/release
python scripts/release/release.py deploy --bundle /tmp/release --expected-source <40-char-commit> --receipt /tmp/receipt.json
```

The reference PostgreSQL image is pinned to `pgvector/pgvector:0.8.2-pg16@sha256:00ba258a…` —
PostgreSQL 16.14 with pgvector 0.8.2 — by tag and digest, to match the RDS target. Parity is
verified rather than assumed: identical engine and extension versions, schema fingerprint, index
definitions, result sets, plan shapes and scores against Amazon RDS in `eu-west-2`. See
[evidence/parity-h1.md](evidence/parity-h1.md).

The operator must trust the build runner, manifest and transport. Checksums do not authenticate an
untrusted publisher. Every base image is pinned by digest and Python dependencies are pinned to exact
versions; NuGet resolves against committed lock files that CI and `verify` restore in locked mode.

What pinning does **not** give you: Python pins carry no hashes, so artefact integrity is not
verified; NuGet packages are pinned by version rather than signature; and the build is not
bit-identical. Image IDs identify one actual build. Each task receipt describes one run, not current
uptime.

EKS/Helm remains unsupported design material, retained as a labelled section in
[the architecture document](docs/architecture.md). `deploy.yml` exists only to fail loudly if the
unsupported path is invoked.

## Local development

`docker compose up --build` uses the development compose file and mock embedding mode. This is
separate from the immutable release path. After startup, gateway is at localhost:3002, Python docs
at localhost:8010/docs and ingestion at localhost:8080. Credentials in the demo are synthetic and
unsuitable for real data.

## Promotion and drills

A three-case synthetic HTTP candidate gate runs before promotion, using the actual candidate
gateway and application responses. A wrong-answer image and a missing dataset both blocked
promotion while the active images stayed unchanged. See [O02](docs/O02.md).

A dated rollback and provider-failure drill restored the last accepted images and verified
deterministic fallback against the same three cases. See [O03](docs/O03.md) for timings and limits:
operator-led recovery, simulated provider outage, no automatic live-provider failback.

A bounded incident drill emitted correlated request, retrieval, model and tool spans, identified a
pgvector failure and fired then cleared a local acceptance-error alert. See [O04](docs/O04.md) and
the [incident runbook](docs/incident-runbook.md). This is local JSON tracing, not a deployed
monitoring backend.

## Known limitations, measured

Retrieval was rebuilt after measurement exposed three defects: an unranked keyword branch returning
arbitrary rows, a weighted fusion that could not express a lexical win, and `plainto_tsquery` AND
semantics that matched nothing for natural-language input. It now uses reciprocal rank fusion over
an OR-joined `websearch_to_tsquery`, and `scripts/experiments/rrf_acceptance.py` requires a
dense-unreachable document to surface through the keyword channel.

The acceptance gate establishes provenance binding, image identity and fail-closed promotion. It
does not establish answer quality: a wrong-document answer scored `faithfulness = 1.0` and was
stopped only by a hand-curated required-phrase list.

Both are stated with numbers in [the architecture document](docs/architecture.md).

## Experiments

`scripts/experiments/` reproduces the measurements behind the parity receipt and the limitations
above: schema generation from the ORM metadata, keyword-contribution measurement with `EXPLAIN`
before and after, four gate scenarios, and a two-target parity fingerprint. They are not part of
the release path and are not run by CI. Outputs go to `artifacts/experiments/` and are git-ignored.
See [scripts/experiments/README.md](scripts/experiments/README.md).

## Verification

One entry point runs every offline suite:

```sh
python scripts/verify.py            # all suites in Docker; authoritative
python scripts/verify.py --local    # host toolchains, skipping what is unavailable
python scripts/verify.py --with-db  # adds the retrieval acceptance test
python scripts/verify.py --only rag-api,ddl
python scripts/verify.py --list     # print the step table and exit
```

It mirrors `.github/workflows/ci.yml` and adds two checks CI does not run: schema-generation
determinism and retrieval acceptance. A step is skipped only when its probe genuinely fails, and the
summary names what was skipped and why — a skip is never reported as a pass.

The tool has its own tests in `tests/test_verify.py`. They exist because it reported the wrong thing
four times while being built; each incident is now a named case. See §13 of the architecture
document for why that layer matters more than it looks.

| Step | Checks |
|---|---|
| `contracts` | release integrity, fail-closed behaviour, gate contracts, indicator boundaries |
| `rag-api` | RAG service unit tests |
| `evaluator` | evaluator unit tests |
| `dotnet` | .NET build and tests |
| `node` | TypeScript typecheck, build and tests |
| `ddl` | schema generation produces a stable hash across runs |
| `retrieval` | a dense-unreachable document surfaces through the keyword channel |

Generated release and test artifacts are ignored; dated evidence and limitations live under
`docs/` and `evidence/`.

## Limits

No uptime, production readiness, semantic answer quality, broad evaluation quality, external alert
delivery or live EKS deployment is claimed anywhere in this repository. Each of those is either
untested or explicitly excluded. Where a claim is bounded, the bound is stated next to the claim
rather than in a footnote.

Live generation is exercised against two providers with recorded token counts and cost —
[evidence/live-provider.md](evidence/live-provider.md). Live answers are gated structurally — provider
answer, usage present, exact citations, fixture phrases — and a controlled run blocked all three cases
when the service answered from the deterministic fallback:
[evidence/gate-live.md](evidence/gate-live.md). The release CLI still builds mock-only bundles, because
provider credentials must not enter a manifest.

Offline evaluation uses the RAGAS library — [evidence/ragas-eval.md](evidence/ragas-eval.md). Its
scores are reported, never used to gate, because measurement showed them disagreeing with obvious
ground truth: a source-identical answer scored 0.33 faithful.
