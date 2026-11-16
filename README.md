# AI Platform Proof

Start with the [evidence index](EVIDENCE.md) for source, tests, dated results and limits, and the
[architecture document](docs/architecture.md) for how the system is actually built.

Public reference implementation: TypeScript gateway, Python RAG API and .NET ingestion service
backed by PostgreSQL/pgvector. Synthetic mock embeddings are the default. This repository does not
establish commercial production operation.

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

The reference PostgreSQL image is pinned to `pgvector/pgvector:0.8.2-pg16` — PostgreSQL 16.14 with
pgvector 0.8.2 — to match the RDS target. Parity is verified rather than assumed: identical engine
and extension versions, schema fingerprint, index definitions, result sets, plan shapes and scores
against Amazon RDS in `eu-west-2`. See [evidence/parity-h1.md](evidence/parity-h1.md).

The operator must trust the build runner, manifest and transport. Checksums do not authenticate an
untrusted publisher. Base images are pinned by tag rather than digest, and most Python/.NET
dependencies remain mutable at build time, so image IDs identify one actual build rather than a
promise of bit-identical rebuilds. Each task receipt describes one run, not current uptime.

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

Retrieval has three defects, all reproduced in `scripts/experiments/`: the keyword branch returns
`k` unranked rows, the fusion weights compare incommensurable scales so a lexical-only document
cannot reach top-k, and `plainto_tsquery` ANDs lexemes so a natural-language question matches
nothing. The existing GIN index is the receipt of the match predicate that was never written.

The acceptance gate establishes provenance binding, image identity and fail-closed promotion. It
does not establish answer quality: a wrong-document answer scored `faithfulness = 1.0` and was
stopped only by a hand-curated required-phrase list.

Neither limitation is a surprise or a regression — both are stated in
[the architecture document](docs/architecture.md) with numbers and reproduction steps.

## Experiments

`scripts/experiments/` reproduces the measurements behind the parity receipt and the limitations
above: schema generation from the ORM metadata, keyword-contribution measurement with `EXPLAIN`
before and after, four gate scenarios, and a two-target parity fingerprint. They are not part of
the release path and are not run by CI. Outputs go to `artifacts/experiments/` and are git-ignored.
See [scripts/experiments/README.md](scripts/experiments/README.md).

## Verification

```sh
python -m unittest discover -s tests -v
python -m pytest services/rag-api/tests -q
```

The offline suites check release integrity, fail-closed behaviour, gate contracts and indicator
boundaries. Existing service CI remains separate. Generated release and test artifacts are ignored;
dated evidence and limitations live under `docs/` and `evidence/`.

## Limits

No uptime, production readiness, semantic answer quality, broad evaluation quality, external alert
delivery or live EKS deployment is claimed anywhere in this repository. Each of those is either
untested or explicitly excluded. Where a claim is bounded, the bound is stated next to the claim
rather than in a footnote.
