# FinOps Tagging & Cost Allocation

This document defines the FinOps tagging strategy applied to every resource in the
AI Platform Proof project, and explains how it enables cost allocation, showback /
chargeback, budgeting and optimisation.

## Why tag?

Untagged resources are invisible to finance. Tags give you:

- **Cost allocation** — attribute every £/$ to a workload, team and budget line.
- **Showback / chargeback** — internal teams see the cost of what they run.
- **Budgets & alerts** — AWS Budgets + AWS Cost Explorer can alert per tag.
- **Optimisation** — find idle/over-provisioned tagged workloads (e.g. EKS node groups).
- **Governance** — prove least-privilege and ownership in audits.

## The taxonomy

| Tag key | Purpose | Example value |
|---|---|---|
| `Project` | Logical project grouping | `ai-platform` |
| `Environment` | Lifecycle stage | `dev` / `staging` / `prod` |
| `ManagedBy` | Tool that created it | `terraform` / `helm` |
| `CostCenter` | FinOps cost centre code | `cc-ai-mlops` |
| `BusinessUnit` | Owning business unit | `data-platform` |
| `Owner` | Accountable team/individual | `koate.kpai@outlook.com` |
| `BudgetOwner` | Person/team accountable for budget | `platform-leads` |
| `Workload` | Cost grouping for a system | `ai-platform-proof` |
| `CostCategory` | Chargeback/showback bucket | `engineering-rnd` |
| `DataClassification` | Sensitivity | `public-reference` |

> Keep the key set small and consistent — a FinOps tagging standard that is too
> large is ignored. This set covers allocation, ownership and classification.

## Where tags are applied

### 1. AWS (Terraform) — `infra/terraform/environments/dev/main.tf`

Applied at the **provider level** via `default_tags`, so **every** AWS resource the
provider creates inherits the full taxonomy automatically:

```hcl
provider "aws" {
  region = var.region
  default_tags {
    tags = {
      Project            = "ai-platform"
      Environment        = var.environment
      ManagedBy          = "terraform"
      CostCenter         = var.cost_center
      BusinessUnit       = var.business_unit
      Owner              = var.tag_owner
      BudgetOwner        = var.budget_owner
      Workload           = var.workload
      CostCategory       = var.cost_category
      DataClassification = var.data_classification
    }
  }
}
```

Resource-level tags **augment** (never replace) the defaults for cost granularity:

- **EKS node groups** → `CostCenter`, `BudgetOwner`, `Workload` so EC2/instance cost
  is attributable to the workload.
- **RDS Postgres** → `CostCenter`, `Workload` (storage + IOPS cost attribution).
- **Postgres security group** → `CostCenter`, `Workload`.

Values are driven by variables (see `variables.tf`) and per-environment
`terraform.tfvars` — never hard-code a cost centre in multiple places.

> The S3 state bucket and DynamoDB lock table are created outside this root module
> (backend config). Tag them too when you create them (`CostCenter`, `Owner`,
> `Environment`).

### 2. Kubernetes (Helm) — `infra/helm/ai-platform`

Kubernetes has no "tags"; **labels** serve the same purpose and are read by
Kubecost, Cloudability, and AWS EKS cost monitoring.

Each subchart (`gateway`, `rag-api`, `dotnet-ingest`, `evaluator`) defines a label
helper (`templates/_helpers.tpl`) that emits the standard `app.kubernetes.io/*`
labels **plus** the FinOps taxonomy from `global.finops`:

```yaml
finops.io/cost-center: {{ .Values.global.finops.costCenter | default "unassigned" }}
finops.io/budget-owner: {{ .Values.global.finops.budgetOwner | default "unassigned" }}
finops.io/workload: {{ .Values.global.finops.workload | default "ai-platform" }}
finops.io/environment: {{ .Values.global.environment | default "dev" }}
```

Set them once in the umbrella chart's `global`:

```yaml
global:
  finops:
    costCenter: cc-ai-mlops
    budgetOwner: platform-leads
    workload: ai-platform-proof
```

The labels are attached to the **Deployment** (metadata + pod template) and the
**CronJob**, so pod-level cost allocation works. The deploy pipeline can override
them per environment via `--set global.finops.*` (see `.github/workflows/deploy.yml`
using GitHub `vars`).

### 3. Local development (docker-compose)

Containers get `labels` for consistency with the cloud story:

```yaml
labels:
  finops.cost-center: cc-ai-mlops
  finops.workload: ai-platform-proof
  finops.environment: local-dev
  finops.owner: koate.kpai@outlook.com
```

(These have no billing impact locally — they keep the standard uniform and are
useful if you ship the compose stack to a hosted engine.)

## Budget guardrails (hard £20/month cap)

`infra/terraform/environments/dev/budgets.tf` makes the cap **enforceable**, not
just aspirational:

- `aws_budgets_budget` — monthly `COST` budget, `limit_amount = 20`, `limit_unit =
  "GBP"`, scoped to this workload via the `Workload` cost-allocation tag. Emails
  `budget_alerts_email` at 80% and 100% of the cap.
- `aws_iam_role` + least-privilege policy — Budgets may only `StopDBInstance` /
  `StartDBInstance` **this** RDS instance.
- `aws_budgets_budget_action` — `RUN_SSM_DOCUMENTS` / `STOP_RDS_INSTANCES`,
  `AUTOMATIC`, threshold = **absolute £20**. When the cap is hit, RDS is stopped
  so spend cannot keep accruing.

Driven by variables: `budget_enabled`, `budget_limit_amount`, `budget_alerts_email`
(see `terraform.tfvars.example`).

> **Pre-requisite:** activate `Workload` as a cost-allocation tag in AWS Billing
> (Cost Explorer → Cost allocation tags) or the budget's cost filter never matches.

### The £20/month reality check

A hard £20 cap **will not fit the default stack** in `main.tf` (EKS control plane
~£55–62/mo, RDS ~£18/mo, NAT gateway ~£26/mo, 2× m5.large nodes ~£110/mo →
~£200+/mo total). The budget + auto-stop action is the safety net; staying under
£20 requires a savings profile:

| Option | Architecture | Est. / month | Trade-off |
|---|---|---|---|
| **A — Single box (always-on)** | **Implemented** in `infra/terraform/environments/cheap/`: one `t4g.small` EC2 (public subnet, no NAT) running Docker Compose + Postgres on the box; user-data clones the repo and brings the stack up | £8–15 always-on | No managed K8s, single point of failure — perfect for a 24/7 "live proof" URL |
| **B — Local-first, ephemeral AWS** | **Implemented** in `scripts/ephemeral/`: full EKS+RDS stack run only for demos, torn down automatically via lease-based `guard.ps1 -AutoDestroy`; budget stays as a backstop | £0 down · ~£2.50 per 8h demo day | Needs the teardown discipline — which the tooling enforces |
| **C — Serverless bursts** | Fargate/AppRunner for short runs, vector store elsewhere | £10–20 | Heavier Python RAG + pgvector don't fit cheaply |

**A and B are complementary.** Run **A** as your always-on proof (a recruiter can click
`http://<eip>:3002/health` any day), and **B** for the occasional full-stack EKS demo.
Combined, a disciplined month stays under the £20 AWS Budget: ~£10 (A) + a few pounds
(B) ≤ £20.

**Deploying A:** `scripts/cheap/up.ps1` provisions the box; `scripts/cheap/deploy.ps1`
(or `.github/workflows/deploy-cheap.yml`) redeploys over SSH. Like B, A is tagged with
the same FinOps taxonomy and protected by its own budget + auto-stop-EC2 action
(`infra/terraform/environments/cheap/budgets.tf`).

## Cost visibility in practice

1. **AWS Cost Explorer** — group by `Workload`, `CostCenter` or `Environment` tags
   to see monthly spend per system. Create an AWS Budget filtered to the workload
   tag with an alert at 80% / 100%.
2. **AWS CUR + Athena/QuickSight** — the Cost & Usage Report includes tags; join on
   `tag:Workload` for finance-grade reporting.
3. **Kubecost** — install in the cluster; it reads the `finops.io/*` and
   `app.kubernetes.io/*` labels to allocate node/pod cost to the right workload.
4. **Grafana** — the AI Platform dashboard can be extended with a cost panel that
   pulls budget-utilisation metrics from AWS.

## Interview talking points

- "Tags are applied at the provider level so the full taxonomy is inherited by
  every resource — one source of truth, no drift."
- "The same taxonomy travels from Terraform (cloud), through Helm labels (K8s), to
  compose (local dev), so cost allocation is uniform across the whole lifecycle."
- "Budget ownership is explicit: `BudgetOwner` is a first-class tag, which makes
  FinOps accountability part of the infrastructure contract."