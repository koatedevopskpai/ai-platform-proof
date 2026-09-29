# Architecture

## Goals

1. Demonstrate production-grade MLOps and GenAI platform engineering in public code.
2. Close the explicit skill gaps in two contract specs:
   - **AI Platform Engineer – MLOps/GenAI** (.NET/C#, MLOps, orchestration, vector stores, cloud-native)
   - **Senior AI-Enabled Software/DevOps Engineer** (TypeScript, Python, Kubernetes/Helm, Terraform, API-first)
3. Be fully runnable locally with **zero external API keys** (deterministic mock mode).

## Services

### gateway (TypeScript / Fastify) — `services/gateway`
Edge gateway. Aggregates `/health` across back-ends, routes `/ingest` to dotnet-ingest and `/rag`
to rag-api, and enforces a simple token-bucket rate limit. Demonstrates modern TS/Node service
patterns: typed config, structured logging, graceful shutdown.

### rag-api (Python / FastAPI) — `services/rag-api`
The core AI service:
- `POST /ingest` — embed + persist a document (pgvector).
- `POST /search` — hybrid (dense + keyword) retrieval.
- `POST /rag` — retrieval-augmented answer with guardrails, PII redaction hook, and deterministic
  fallback when the model is unavailable.
- Embeddings via an OpenAI-compatible client, defaulting to a deterministic mock.

### dotnet-ingest (C# / .NET 8) — `services/dotnet-ingest`
C# backend engineering on the same AI stack:
- `POST /ingest` — accepts a document, requests an embedding (from rag-api or a local mock) and
  persists the vector in `pgvector` through Npgsql.
- `GET /vectors/{id}` — read back a stored vector.
- `GET /health` — liveness + vector-store connectivity.
Structured with interfaces (`IEmbeddingClient`, `IVectorStore`) so it is unit-testable; includes a
unit-test project.

### evaluator (Python) — `services/evaluator`
Off-the-request-path MLOps plane:
- RAGAS-style metrics implemented directly (faithfulness, answer relevancy via LLM-as-judge) plus
  lexical metrics (ROUGE-L, MAP@k, nDCG).
- Deterministic mock judge so evaluation runs in CI with no LLM cost.
- Emits a JSON report; exits non-zero below the quality threshold → used as the **CI evaluation gate**.

## Data plane

Single PostgreSQL + `pgvector` store shared by rag-api and dotnet-ingest.

## MLOps / DevOps plane

- **CI** (`ci.yml`): build + unit test all three languages.
- **Evaluation gate** (`eval-gate.yml`): run evaluator; fail PR if quality regresses.
- **Deploy** (`deploy.yml`): build images, push to ECR, `helm upgrade` to EKS.
- **Observability** (`mlops/monitoring`): Prometheus alerting rules (RAG latency/error/eval SLIs),
  Grafana dashboard, drift detection against a reference window.
- **Model registry** (`mlops/model-registry`): versioned model + evaluation metadata.

## Infrastructure

Terraform provisions AWS VPC, EKS (managed node group, Karpenter-ready), and RDS PostgreSQL with
the `pgvector` extension. Helm umbrella chart deploys all services with env-based config, resource
limits, probes and (for back-ends) HorizontalPodAutoscalers.

## Security posture

- Secrets only via environment/Secrets Manager; `.gitignore` excludes `.env` and `*.tfvars`.
- PII-redaction and prompt-injection guardrail hooks in the RAG path.
- Least-privilege IAM roles in Terraform; network policies at the service layer.