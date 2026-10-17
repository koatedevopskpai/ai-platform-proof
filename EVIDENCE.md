# Evidence index — AI Platform Proof

Five-minute route: this file → [release design](docs/O01.md) →
[candidate gate](scripts/release/candidate_gate.py) →
[incident test](tests/integration_o04.py) → [dated results](evidence/O04.md).

## What it is

A public reference build: TypeScript gateway, Python RAG API, .NET ingestion and
PostgreSQL/pgvector. O01–O04 exercise immutable Compose releases, actual-response
acceptance checks, operator-led recovery and local incident telemetry. These are
synthetic workloads with mock embeddings/deterministic generation. No customer,
commercial production operation or live LLM quality is established.

## Inspectable engineering contribution

| Area | Source and decision |
|---|---|
| Immutable images, bundle checksums, source/config binding, fail-closed health | [release.py](scripts/release/release.py), [O01](docs/O01.md) |
| Fresh candidate responses before promotion; E04 gate reuse | [candidate_gate.py](scripts/release/candidate_gate.py), [upstream provenance](scripts/release/UPSTREAM.md), [O02](docs/O02.md) |
| Controlled bad image, rollback and provider-error fallback | [O03 drill](tests/integration_o03.py), [O03 decisions](docs/O03.md) |
| Correlated request/dependency/tool spans | [gateway](services/gateway/src/index.ts), [Python telemetry](services/rag-api/app/core/telemetry.py) |
| Actual acceptance indicators and local alert transitions | [incident_metrics.py](scripts/release/incident_metrics.py), [runbook](docs/incident-runbook.md) |

This maps code that can be inspected and defended; it does not assert commercial
delivery, independent authorship without tools, or operating tenure.

## Strongest dated results — 17 October 2026

| Check | Observed result | Provenance |
|---|---|---|
| Candidate quality/functional gate | Three real gateway/application cases accepted; deliberately wrong image and missing dataset each blocked promotion; active IDs unchanged | [O02](evidence/O02.md) |
| Application incident | Detected in 9.734s; accepted image restoration in 14.902s | [O03](evidence/O03.md) |
| Local HTTP 503 provider simulator | Explicit deterministic fallback accepted all three cases; known-good mock config restored in 15.856s | [O03](docs/O03.md) |
| Database outage and recovery | 12/12 healthy accepted, 6/6 outage requests failed, 6/6 recovered accepted; correlated pgvector error; alert clear → firing → clear | [O04](evidence/O04.md) |
| Healthy-window latency | p50 8.223ms, p95 23.104ms, n=12, concurrency at most 2 | [O04](docs/O04.md) |

Incident timings include configuration/release checks and come from single runs.
The latency sample is tiny and uses deterministic generation. Neither is a
production SLO, scale result or live model latency measurement. The quicker failed
requests during an outage are not a performance improvement.

Clean Linux checks at O04: 11 release/indicator contracts, three gateway tests and
nine RAG tests passed. One existing dependency deprecation warning remains visible.
Each evidence document records its tested source and later documentation handoff;
do not describe documentation commits as fresh runtime runs.

## Reproduce

Free local Docker/Compose demonstration on Linux, with ports 3002/3003 available:

```sh
python -m unittest discover -s tests -v
pip install -r services/rag-api/requirements-dev.txt
python -m pytest services/rag-api/tests -q
python tests/integration_o02.py
python tests/integration_o03.py
python tests/integration_o04.py
```

The drills build/start real containers and deliberately introduce failures. Run
them sequentially. See each task document for project names, cleanup and exact
dependencies. They make no external model calls. Cloud reproduction incurs costs;
the actual short-lived EC2 hosts/disks/keys/security groups were deleted and
absence verified. A receipt is evidence of its dated run, not current uptime.

## One success trace and one failure

Success: candidate bundle → isolated stage → actual ingest/search/RAG answers →
three-case gate → immutable promotion → post-promotion acceptance and image-ID
verification. The response's trace ID follows gateway → RAG request → embedding /
retrieval / generation. Ingestion tool spans measure the gateway's real HTTP call.

Failure: stop PostgreSQL → retrieval.search records a pgvector OperationalError →
generic HTTP 503 plus trace ID → acceptance-error alert fires. Restarting the
task database restores passing acceptance and clears the local alert. For an
answer regression, restore the saved accepted bundle through the normal gate.
Recovery is operator-led and may interrupt traffic.

## Removed or bounded claims

The old deployment evaluator scored a stored dataset answer rather than the
candidate application's response; it was removed from the release decision.
The current gate checks fresh actual answers, but uses binary fact/source fixture
predicates, not a semantic judge. EKS/Helm remains unsupported design material and
its deployment workflow is explicitly disabled. Existing monitoring configuration
does not establish that Prometheus/Grafana or an external notification service ran.

## What this does not establish

- Commercial operation, customer adoption, production scale, uptime or financial correctness.
- Semantic answer quality, model-provider superiority or a held-out benchmark; B01 is deferred.
- Automatic/zero-downtime rollback or live-provider failback. The outage simulator is local.
- W3C traceparent/OTLP collector integration, internal .NET spans, continuous monitoring or delivered pages. Traces and alerts are local JSON.
- Reproducible rebuild bytes from mutable base/dependency tags. Image IDs identify the actual build; checksums do not authenticate an untrusted publisher.
- Security certification or a guarantee about every third-party log. Span metadata and HTTP errors are bounded/tested; this is not an all-systems audit.

Architecture/trade-offs: [architecture](docs/architecture.md), [O01](docs/O01.md),
[O02](docs/O02.md), [O03](docs/O03.md), [O04](docs/O04.md).
