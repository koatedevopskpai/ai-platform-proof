# AI Platform Proof

**Production-grade MLOps / GenAI platform — a public, NDA-proof reference implementation.**

This repo demonstrates the exact engineering capabilities required for senior AI platform and
AI-enabled DevOps contract roles: a multi-service AI platform (RAG + agentic pipeline), built,
containerised, orchestrated, evaluated, monitored and deployed with modern MLOps and
Infrastructure-as-Code practices.

It is deliberately **public and runnable** so that the "live production experience" question can be
answered with a clickable artefact, not a claim.

---

## Why this repo exists

| Capability demonstrated | Closes gap for |
|---|---|
| **.NET 8 / C#** API service with `pgvector` vector store | AI Platform Engineer – MLOps/GenAI (.NET Core/C# requirement) |
| **TypeScript** service (API gateway) | Senior AI-Enabled Software/DevOps Engineer (TypeScript requirement) |
| **MLOps**: CI/CD with **evaluation gates**, model observability, drift detection | Both |
| **Kubernetes (Helm)** packaging + **Terraform** (AWS EKS + RDS pgvector) | Both |
| Microservices, API-first, Docker, GitHub Actions, secure-by-design | Both |

---

## Architecture

```
                         ┌─────────────┐
   clients ─────────────▶│  gateway    │  TypeScript / Fastify   (edge, auth, routing)
                         │  (TS)       │
                         └──┬───────┬──┘
                            │       │
                   ┌────────▼──┐  ┌─▼──────────────┐
                   │  rag-api  │  │  dotnet-ingest │
                   │  Python   │  │  .NET 8 / C#   │  (ingest, embeddings, pgvector)
                   │  FastAPI  │  │                │
                   └────┬──────┘  └───────┬────────┘
                        │                 │
                   ┌────▼────────────────▼────┐
                   │   PostgreSQL + pgvector  │   (single vector store)
                   └──────────────────────────┘

   MLOps plane (off the request path)
   ┌──────────────────────────────────────────────────────────┐
   │  evaluator  — RAGAS-style evals, LLM-as-judge, CI gate   │
   │  monitoring — Prometheus rules, Grafana, drift detect    │
   │  model registry — versioned model/eval metadata          │
   └──────────────────────────────────────────────────────────┘
```

- **gateway** (TypeScript) — edge API gateway: health aggregation, request routing to back-ends, basic rate limiting.
- **rag-api** (Python/FastAPI) — retrieval-augmented generation service: embed, ingest, hybrid search, RAG answer with guardrails and deterministic fallback.
- **dotnet-ingest** (C#/.NET 8) — ingestion + vector persistence API against `pgvector`; demonstrates C# backend engineering on the same AI stack.
- **evaluator** (Python) — evaluation framework (faithfulness, answer relevancy, ROUGE, MAP@k, nDCG), LLM-as-judge with a deterministic mock fallback so it runs offline; used as a CI **evaluation gate**.
- **infra/** — Terraform (AWS EKS + RDS with pgvector) and an umbrella Helm chart.
- **mlops/** — monitoring rules, Grafana dashboard, drift detection, model registry.

---

## Quick start (local, fully offline-capable)

Prerequisites: Docker.

```bash
docker compose up --build
```

- Gateway:      http://localhost:3002/health
- rag-api:      http://localhost:8010/docs
- dotnet-ingest: http://localhost:8080/health

> Host ports 3002/8010/8080 are mapped to avoid clashing with common local dev servers.

Ingest a document and run a RAG query:

```bash
# ingest via C# service
curl -X POST http://localhost:3002/ingest \
  -H "Content-Type: application/json" \
  -d '{"id":"doc-001","content":"Our refund policy allows full refunds within 30 days."}'

# RAG query via Python service
curl -X POST http://localhost:3002/rag \
  -H "Content-Type: application/json" \
  -d '{"query":"What is the refund window?"}'
```

> All embedding calls use a deterministic **mock mode** by default so the stack runs with zero
> API keys. Set `OPENAI_API_KEY` / `OPENAI_BASE_URL` to switch to a real model.

---

## MLOps evaluation gate (the part that proves production readiness)

```bash
docker compose run --rm evaluator \
  python -m evaluator --dataset evaluator/datasets/eval_set.jsonl --threshold 0.80
```

The evaluator produces a JSON report of every metric and exits non-zero if the aggregate score is
below the gate. This same command runs in CI (`eval-gate.yml`) so **no PR ships a model or prompt
change that regresses quality** — model evaluation, not just unit tests.

## Deploy (Infrastructure-as-Code)

```bash
cd infra/terraform/environments/dev
terraform init && terraform plan   # requires AWS credentials
```

```bash
helm install ai-platform infra/helm/ai-platform --values infra/helm/ai-platform/values.yaml
```

## Ephemeral AWS mode (£20/month-compatible)

The full EKS/RDS stack is ~£200/month always-on. `scripts/ephemeral/` keeps it
**£0/month when down** and ~£2.50 per 8h demo day, with enforced teardown:

```powershell
scripts/ephemeral\up.ps1          # provision + deploy + write lease
scripts/ephemeral\status.ps1      # uptime + est. cost so far
scripts/ephemeral\down.ps1        # destroy + record actual cost
scripts/ephemeral\guard.ps1 -AutoDestroy   # scheduled auto-teardown of overdue leases
```

The AWS Budget (`budgets.tf`, hard £20/month + auto-stop RDS) is the backstop; the
lease-based tooling is the discipline. See `scripts/ephemeral/README.md`.

## Option A — always-on single box (~£8–15/month)

The full-stack demo only exists when you spin it up. For a **24/7 live URL** (point a
recruiter at it any day), run one small EC2 (`infra/terraform/environments/cheap/`):

```powershell
scripts\cheap\up.ps1        # provision the box + bootstrap the stack
scripts\cheap\status.ps1    # show URL + health + est. cost
scripts\cheap\deploy.ps1    # SSH redeploy (git pull + docker compose up)
scripts\cheap\down.ps1      # destroy (back to £0/month)
```

Same code, same Dockerfiles — just hosted on a single always-on box. Protected by its
own budget + auto-stop action (`cheap/budgets.tf`). Deploy from CI via
`deploy-cheap.yml` (secrets: `CHEAP_HOST`, `CHEAP_SSH_KEY`).

---

## Repository map

```
.
├── services/
│   ├── gateway/          # TypeScript / Fastify edge gateway
│   ├── rag-api/          # Python / FastAPI RAG service
│   ├── evaluator/        # Python evaluation framework + CI gate
│   └── dotnet-ingest/    # .NET 8 / C# ingestion + pgvector service
├── infra/
│   ├── terraform/        # AWS EKS + RDS (pgvector)
│   └── helm/             # umbrella chart (gateway, rag-api, dotnet-ingest, evaluator)
├── mlops/
│   ├── monitoring/       # Prometheus rules, Grafana dashboard, drift detection
│   └── model-registry/   # versioned model + eval metadata
├── .github/workflows/    # CI, evaluation gate, deploy
├── docs/
│   ├── architecture.md
│   └── finops-tagging.md # cost-allocation taxonomy (AWS tags, Helm labels)
└── docker-compose.yml
```

---

## Design principles

- **Deterministic-first**: every service degrades to deterministic, mockable behaviour so the whole
  stack is reproducible, testable and runnable without external dependencies.
- **Evaluation as a gate**: quality is enforced in CI, not asserted in a README.
- **Secure by design**: PII-redaction hooks, prompt-injection guardrails, least-privilege IAM in
  Terraform, secrets via environment (never committed).
- **Public by construction**: synthetic data only, no client IP.