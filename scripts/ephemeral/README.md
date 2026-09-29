# Ephemeral AWS workflow (local-first, £20-compatible)

The full stack (EKS + RDS + VPC) is ~£200/month always-on — **way over the £20 cap**.
This workflow makes it genuinely £20-compatible by keeping the stack **down unless a
demo needs it**, and enforcing teardown so nothing runs away:

> **£0/month when down · ~£2.50 per 8-hour demo day** (see `estimate-cost.ps1`)

## The lifecycle

```
.\up.ps1            provision infra (Terraform apply) + write a lease
.\status.ps1        show uptime, est. cost so far, lease expiry
.\guard.ps1         warn when the lease is overdue (scheduled)
.\guard.ps1 -AutoDestroy   tear down overdue leases automatically
.\down.ps1          terraform destroy + record actual cost
.\estimate-cost.ps1 print the cost model and £20-budget fit
```

## Recommended operating cadence

1. **Demo day:** run `.\up.ps1`, demo, then `.\down.ps1` the same day.
2. **Safety net:** the AWS Budget (`infra/terraform/environments/dev/budgets.tf`)
   auto-stops RDS and emails at 80%/100% of the £20 cap.
3. **Discipline layer:** schedule `guard.ps1 -AutoDestroy` hourly so a forgotten
   cluster is destroyed automatically once its lease expires.
   Windows Task Scheduler action:
   ```
   powershell -NoProfile -ExecutionPolicy Bypass -File C:\...\scripts\ephemeral\guard.ps1 -AutoDestroy
   ```

## Deploying the app (optional)

`up.ps1` provisions infra and configures kubectl. To also deploy the Helm chart,
first push the container images (the `deploy.yml` GitHub Action does this to ECR),
then:

```
.\up.ps1 -DeployHelm -Registry <ECR_REGISTRY_URL> -ImageTag <sha-or-latest>
```

## Prerequisites

- AWS credentials configured (`aws configure` or SSO); the scripts run
  `aws sts get-caller-identity` as a pre-flight check.
- `terraform`, `aws`, `kubectl` on PATH. `helm` only if you use `-DeployHelm`.
- The `Workload` cost-allocation tag activated in AWS Billing (budget filter).

## What the lease file is

`.ephemeral-lease.json` (gitignored, at the repo root) records when the stack went
up, its max lease length, and the estimated cost rate. `status.ps1` and `guard.ps1`
read it; `down.ps1` records the outcome. It is the "don't forget me" memory of the
workflow.

## Cost model

| Scenario | Est. cost |
|---|---|
| Always-on | ~£200/mo |
| One 8h demo day | ~£2.50 |
| Two demo days/month | ~£5 |
| Fully down | £0/mo |

See `estimate-cost.ps1` for the component breakdown and how this stays under the
£20 AWS Budget.