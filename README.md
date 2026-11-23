# Pulse

**Uptime monitoring on AWS: register a URL, Pulse checks it every minute, opens incidents, alerts Slack/Discord and serves a public status page. The app is small on purpose; the platform around it (Terraform, EKS, GitOps, observability, SRE practice) is the real product.**

> Architecture diagram: export your Excalidraw/draw.io drawing to `docs/images/architecture.png`. Until then, see [docs/architecture.md](docs/architecture.md).

## What this project demonstrates

- **Infrastructure as code**: Terraform for VPC, EKS, RDS, SQS, IAM, Secrets Manager. Remote state with S3-native locking, pinned providers and modules, and dev and prod environments with different cost/HA trade-offs.
- **CI/CD with supply-chain security**: GitHub Actions with OIDC to AWS (no stored keys), every action pinned to a commit SHA, Trivy image scans with SARIF upload, an SBOM per build, and Dependabot.
- **GitOps**: Argo CD app-of-apps watching a separate config repo. CI never touches the cluster; every deploy is a git commit, and dev → prod promotion reuses the exact same image tag.
- **Kubernetes operations**: Helm chart with restricted Pod Security, read-only root filesystems, PDBs and probes. HPA scales the API, KEDA scales workers on SQS depth, and the Cluster Autoscaler adds nodes. Pods get AWS access through EKS Pod Identity.
- **Observability and SRE**: Prometheus metrics designed for bounded cardinality, Grafana dashboards, SLOs with multi-window burn-rate alerts, a runbook per alert, and Loki logs.
- **Application security**: SSRF protection on user-supplied URLs. Every hop is checked at creation time, at check time, and on each redirect, which blocks private, link-local and metadata IPs (including IPv4-mapped and NAT64 forms).
- **Reliability design**: idempotent queue processing, safe multi-replica scheduling with `FOR UPDATE SKIP LOCKED`, graceful shutdown, a DLQ, and chaos drills with postmortems.

## How it works

```
POST /api/v1/monitors ──▶ Postgres ◀── scheduler (2+ replicas, FOR UPDATE SKIP LOCKED)
                                            │ one SQS message per due check
                                            ▼
           status page ◀── API         SQS checks queue ──(3 failures)──▶ DLQ
                                            │ long-poll
                                            ▼
                                  workers (KEDA-scaled): SSRF check → HTTP check →
                                  store result + incident state machine (1 transaction)
                                  → Slack/Discord → delete message
```

One container image serves every process: `pulse-api`, `pulse-scheduler`, `pulse-worker`, `pulse-target` (an in-cluster chaos target) and `pulse-maintenance` (retention CronJob). Details: [docs/architecture.md](docs/architecture.md). Names shared across components: [docs/CONTRACT.md](docs/CONTRACT.md).

## Project status

| Phase | What exists | Verified how |
|---|---|---|
| 1 Application | FastAPI API + status page, scheduler, worker, notifier, chaos target, retention job, Alembic migration | `make lint` clean; `make test`: **198 tests, 92% coverage** against real Postgres and ElasticMQ, including the full create → check → incident → recover flow and a two-scheduler no-duplicates test |
| 2 Containers | Multi-stage Dockerfile (digest-pinned, uid 10001, read-only FS), docker-compose stack, Prometheus | `make up && make seed` → live status page; `make target-down` turned `/switch` red in **73 s** and recovery was notified |
| 3 Terraform | `infra/bootstrap`, 6 modules, `envs/dev`, `envs/prod` | `terraform validate` + `fmt` clean for all roots. **Never planned or applied** (no AWS account configured yet). tflint/checkov not run locally (not installed) |
| 4 CI | 5 workflows, Dependabot, pre-commit, gitleaks | actionlint + zizmor clean. **Never run on GitHub** |
| 5 Helm/GitOps | `pulse-gitops/` chart, env values, Argo CD apps, bootstrap/teardown scripts | `helm lint --strict`; kubeconform: 168 resources valid against Kubernetes 1.37; chart **installed on a local kind cluster** under restricted Pod Security and checked live. Argo CD and EKS not exercised |
| 6 Observability | Dashboards, PrometheusRule with SLO recording rules, a runbook per alert | Metric names and bucket labels checked against a live local Prometheus; PromQL not run on a cluster |
| 7 Security/scale | k6 scenarios, monitor seeder, threat model | Scripts syntax-checked; no load test run yet |
| 8 Chaos | Drill worksheets, postmortem template | Not run (needs EKS). Write postmortems after the drills |
| 9 Docs | Architecture, 11 ADR **drafts**, SLOs, security, cost, interview prep | ADRs are drafts to rewrite in your own words; cost and load-test numbers are `TODO` and must come from real measurements |

Image size (`pulse:local`, linux/arm64): **89 MB compressed** (what ECR stores and nodes pull), about **289 MB uncompressed**. That is above the plan's 200 MB target: about 140 MB is the virtualenv, mainly botocore, SQLAlchemy and asyncpg, with bytecode precompiled so startup is fast on a read-only filesystem.

## Quick start (local, $0)

Requirements: Docker, Python 3.12+, [uv](https://docs.astral.sh/uv/).

```bash
make up        # postgres, elasticmq, migrate, api, scheduler, 2 workers, target, prometheus
make seed      # demo monitors (chaos target endpoints + example.com)
```

- Status page: http://localhost:8000 · API docs: http://localhost:8000/docs · Prometheus: http://localhost:9090
- Chaos target on host port **18080**: `make target-down` / `make target-up`
- Local admin key (compose only): `local-dev-key`
- Real Slack/Discord messages: `export PULSE_ALERT_WEBHOOK_URL=...` before `make up`

```bash
make lint && make test   # tests use compose Postgres on host port 5433 and a separate pulse_test DB
make down                # stop and delete volumes
```

## Deploy to AWS

> ⚠️ **Cost**: EKS, NAT gateway, EC2 nodes, RDS and the ALB bill **by the hour, even when idle**. Run dev only while you work and finish every session with `make infra-down ENV=dev`. Read plan section 7 first.

1. **Replace the placeholders** (search the repo): `YOUR_NAME`, `ACCOUNT_ID` (backend bucket), `YOUR_IP/32` (EKS API allow-list), and the budget email variable. The region is `us-east-2`; change it in `infra/envs/*/*.tfvars`, the backend blocks and `CLAUDE.md` if you prefer another.
2. `aws configure sso` → profile `pulse` (the Makefile uses `AWS_PROFILE=pulse` for infra targets only).
3. Apply `infra/bootstrap` once by hand, then migrate its state. See [infra/README.md](infra/README.md).
4. Create the GitHub repository variables, secret, environments and GitHub App listed under [CI setup](#ci-setup) below.
5. `make infra-up ENV=dev` plans, asks you to type `yes`, applies, then writes `pulse-gitops/envs/dev/infra-values.yaml` (and the ALB controller's `vpcId`).
6. Set secret values (never in Terraform): [docs/runbooks/set-secrets.md](docs/runbooks/set-secrets.md).
7. Push `pulse-gitops` to its own GitHub repo, then `make kubeconfig ENV=dev && make argocd-bootstrap ENV=dev`.
8. Tear down: `make infra-down ENV=dev`. It first deletes the Kubernetes-created load balancers and volumes, then runs `terraform destroy`. Checklist: [docs/runbooks/teardown.md](docs/runbooks/teardown.md).

### CI setup

In the `pulse` repo on GitHub:

- **Variables**: `AWS_ROLE_ECR_PUSH`, `AWS_ROLE_TF_PLAN`, `AWS_ROLE_TF_APPLY` (bootstrap outputs), `AWS_REGION`, `ECR_REGISTRY`, `GITOPS_APP_ID`
- **Secret**: `GITOPS_APP_PRIVATE_KEY`
- **Environments**: `dev` (no approval) and `prod` (required reviewer: you)
- **GitHub App**: Contents read/write, installed **only** on `pulse-gitops` (ADR 0005)

## CI/CD flow

```
PR ──▶ app-ci: lint · test (Postgres+ElasticMQ services) · build · Trivy (SARIF) · SBOM
   └─▶ infra-ci: fmt · validate · tflint · checkov · plan → one updated PR comment
main ─▶ app-ci: push sha-xxxxxxx to ECR ─▶ commit image tag to pulse-gitops envs/dev ─▶ Argo CD syncs dev
manual ─▶ promote-prod: verify tag in ECR + no CRITICAL findings ─▶ approval ─▶ envs/prod
manual ─▶ infra-apply: plan file ─▶ (approval for prod) ─▶ apply exactly that plan
```

## Repository layout

```
app/            Python package (src/pulse), tests, Alembic migrations
infra/          Terraform: bootstrap, modules, envs/dev, envs/prod
pulse-gitops/   Separate git repo (ignored here): Helm chart, env values, Argo CD apps
scripts/        render-infra-values, argocd-bootstrap, pre-destroy-cleanup
loadtest/       k6 scenarios + monitor seeder
local/          compose configs (ElasticMQ, Prometheus) and seed script
docs/           architecture, ADRs, runbooks, SLOs, security, cost, drills, postmortems
plan/plan.md    the master build plan
```

## Docs

[Architecture](docs/architecture.md) · [ADRs](docs/adr/README.md) · [Runbooks](docs/runbooks/README.md) · [SLOs](docs/slo.md) · [Security](docs/security.md) · [Cost](docs/cost.md) · [Load tests](docs/load-test-results.md) · [Chaos drills](docs/drills.md) · [Postmortems](docs/postmortems/README.md) · [Interview prep](docs/interview-prep.md) · [Contract](docs/CONTRACT.md)

## Deviations from the plan (and why)

- **`pulse-gitops/` lives inside this folder** (gitignored here, pushed as its own repo) instead of a sibling directory.
- **Extra columns** on `monitors`: `failing_since`/`failing_error` (so the incident records when its failure streak began without scanning results) and `tls_notified_at` (TLS warnings at most once a day).
- **Extra secret** `pulse/<env>/alertmanager` for the platform-alerts webhook.
- **Listen ports are `PULSE_*_LISTEN_PORT`**, and every pod sets `enableServiceLinks: false`. Kubernetes service links otherwise inject `PULSE_API_PORT=tcp://…` and crash the pods (found during the kind rehearsal).
- **Local host ports**: Postgres 5433 and chaos target 18080, so they don't clash with services already on the machine.
- **kube-prometheus-stack syncs at wave −1** (not −2) so its Secrets and gp3 StorageClass exist first.
- **Known limitation**: notifications are sent after the DB commit. If a worker crashes in that gap, the redelivered message is a duplicate and the notification is lost. The fix is the transactional outbox (Phase 10).

## Lessons learned / at 100× scale

_Write these yourself after Phases 5–8; they're the most interview-relevant part of this README._
