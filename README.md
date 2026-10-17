# AI Platform Proof

Start with the [evidence index](EVIDENCE.md) for source, tests, dated results and limits.

Public reference implementation: TypeScript gateway, Python RAG API and .NET
ingestion service backed by PostgreSQL/pgvector. Synthetic mock embeddings are
the default. This repository does not establish commercial production operation.

## Supported release path

EC2 + Docker Compose with immutable image IDs and a checksummed image archive.
The release receipt records the source revision, configuration hash, expected
and running image IDs and aggregated backend health. See [O01](docs/O01.md).
The manual `deploy-cheap.yml` builds once and transfers that bundle over verified
SSH; it does not pull source or build on the deployment host. Configure a
`compose-demo` environment and CHEAP_HOST, CHEAP_SSH_KEY, CHEAP_KNOWN_HOSTS secrets.
The gateway listens only on host loopback; use an SSH tunnel for this demo.

```sh
python scripts/release/release.py build --out /tmp/release
python scripts/release/release.py deploy --bundle /tmp/release --expected-source <40-char-commit> --receipt /tmp/receipt.json
```

The operator must trust the build runner, manifest and transport. Checksums do
not authenticate an untrusted publisher. Docker base tags and most Python/.NET
dependencies remain mutable at build time; image IDs identify one actual build,
not a promise of bit-identical rebuilds. Each task receipt describes one run,
not current uptime. EKS/Helm is unsupported design material; its old deployment
workflow is manually disabled with a clear failing result.

## Local development

`docker compose up --build` uses the development compose file and mock embedding
mode. This is separate from the immutable release path. After startup, gateway
is at localhost:3002, Python docs at localhost:8010/docs and ingestion at
localhost:8080. Credentials in the demo are synthetic and unsuitable for real data.

A three-case synthetic HTTP candidate gate now runs before promotion, using the
actual candidate gateway/application responses and E04 hard checks. A wrong-answer
image and a missing dataset both blocked promotion while active images stayed
unchanged. These binary fixture predicates do not establish semantic or live LLM
quality. See [O02](docs/O02.md). A dated rollback/provider-failure drill restored the last accepted images and
verified deterministic fallback against the same three cases. See [O03](docs/O03.md)
for timings and limits: operator-led recovery, simulated provider outage, no
automatic live-provider failback or uptime guarantee. A bounded incident drill emitted correlated request/retrieval/model/tool spans,
identified a pgvector failure and fired/cleared a local acceptance-error alert.
See [O04](docs/O04.md) and the [incident runbook](docs/incident-runbook.md).
This is custom local JSON tracing/alert evidence, not a deployed monitoring backend
or continuous production observability. Existing evaluator and monitoring files
are not proof that those operational behaviours work. No uptime, production
readiness, broad evaluation quality or live EKS deployment is claimed.

## Verification

`python -m unittest discover -s tests -v` checks release integrity and fail-closed
behaviour. Existing service CI remains separate. Generated release/test artifacts
are ignored; dated evidence and limitations live under docs/ and evidence/.
