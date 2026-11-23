# Pulse: Uptime Monitoring Platform on AWS

> **Working name:** Pulse. Rename it if you like (update `CLAUDE.md`, the Helm chart name, and the Terraform `project` variable).
>
> **What this file is:** the master build plan for a portfolio project, written so you can work through it phase by phase with Claude Code. Tick the checkboxes as you go.

---

## Table of contents

1. [What we're building](#1-what-were-building)
2. [How to use this plan with Claude Code](#2-how-to-use-this-plan-with-claude-code)
3. [Architecture](#3-architecture)
4. [Tech stack](#4-tech-stack)
5. [Repository layout](#5-repository-layout)
6. [Conventions](#6-conventions)
7. [Cost guardrails (read before Phase 3)](#7-cost-guardrails-read-before-phase-3)
8. [Timeline](#8-timeline)
9. [Phase 0: Setup](#phase-0-setup)
10. [Phase 1: Application](#phase-1-application)
11. [Phase 2: Containers and local stack](#phase-2-containers-and-local-stack)
12. [Phase 3: Infrastructure with Terraform](#phase-3-infrastructure-with-terraform)
13. [Phase 4: CI with GitHub Actions](#phase-4-ci-with-github-actions)
14. [Phase 5: Kubernetes, Helm and GitOps](#phase-5-kubernetes-helm-and-gitops)
15. [Phase 6: Observability](#phase-6-observability)
16. [Phase 7: Security, autoscaling and load testing](#phase-7-security-autoscaling-and-load-testing)
17. [Phase 8: Chaos drills and postmortems](#phase-8-chaos-drills-and-postmortems)
18. [Phase 9: Documentation and presentation](#phase-9-documentation-and-presentation)
19. [Phase 10: Stretch goals](#phase-10-stretch-goals)
20. [Interview preparation](#interview-preparation)
21. [Appendices: templates](#appendices-templates)

---

## 1. What we're building

Pulse is an uptime monitoring service. You register URLs; Pulse checks them every minute, records whether they're up and how fast they respond, tracks SSL certificate expiry, opens incidents after repeated failures, sends Slack/Discord notifications, and serves a public status page.

The app is deliberately small. **The real product is the platform around it:** infrastructure as code, CI/CD, GitOps, autoscaling, observability, security, and documented incident response.

The project has **two layers of monitoring**, and you should be able to explain both:

| Layer | Type | What it watches | Implemented by |
|---|---|---|---|
| 1. Product | Black-box (synthetic) monitoring | External URLs that users register | Pulse workers |
| 2. Platform | White-box monitoring | Pulse itself (API, workers, queue, pods, DB) | Prometheus, Grafana, Alertmanager, Loki |

### Goals

- Demonstrate end-to-end ownership: code → container → IaC → CI → GitOps → observability → incident response.
- Produce artifacts recruiters and interviewers can read: README, architecture diagram, ADRs, runbooks, postmortems, load-test results, cost analysis.
- Understand every line well enough to explain it in an interview.

### Non-goals (keep scope under control)

- User accounts, sign-up, multi-tenancy (a single admin API key is enough).
- A fancy frontend (server-rendered HTML is fine).
- Payments, mobile apps, AI features.

---

## 2. How to use this plan with Claude Code

1. Put this file in the root of the `pulse` repo as `plan.md`.
2. Create `CLAUDE.md` from [Appendix A](#appendix-a-claudemd-template). Claude Code reads `CLAUDE.md` automatically, so your conventions and safety rules apply in every session. Update its "Current phase" line as you progress.
3. Work **one task group at a time** (for example, 1.1 to 1.3). Good prompts:
   - `Read plan.md and CLAUDE.md. We're starting Phase 1, tasks 1.1–1.3. Propose your approach and file list before writing code.`
   - `Implement task 1.6 (worker). Write the tests first, then the code. Run make test and show me the results.`
   - `Review what we built in Phase 1 against the Definition of Done. What's missing?`
   - `Explain src/pulse/worker/checker.py to me as if I'll be interviewed on it tomorrow.`
   - `Quiz me with 5 interview questions about this phase.`
4. After every task: run it yourself, read the diff, commit with a clear message, tick the checkbox here.
5. Claude Code docs: https://docs.claude.com/en/docs/claude-code/overview

### Rules for you (important)

- **You must understand everything you merge.** If you can't explain a line, ask Claude Code to explain it or simplify it. Interviewers will probe.
- **You run anything that costs money or changes AWS** (`terraform apply`, `terraform destroy`, AWS CLI writes) after reading the plan output. Never let any tool apply infrastructure unattended.
- **Write ADRs and postmortems in your own words.** Claude Code can review and improve them, but these documents are your voice.
- **Never paste AWS keys or secrets** into chat, files, or commits. Use `aws sso login` profiles locally and OIDC in CI.

---

## 3. Architecture

```
                         ┌─────────────────── GitHub: pulse repo ────────────────────┐
   git push ───────────▶ │ Actions: lint → test → build → Trivy scan → push to ECR    │
                         │          → bump image tag in pulse-gitops (envs/dev)        │
                         └──────────────────────────────┬─────────────────────────────┘
                                                        │ commit
                         ┌──────────── GitHub: pulse-gitops repo ─────────────┐
                         │ Helm chart + per-env values + Argo CD apps          │
                         └──────────────────────────────┬─────────────────────┘
                                                        │ Argo CD pulls (GitOps)
┌──────────────────────────────── AWS account, VPC across 2 AZs ─────────────────────────────────┐
│  Public subnets:   ALB (created by AWS Load Balancer Controller) · NAT gateway                  │
│  Private subnets:  EKS managed node group                                                       │
│     ┌──────────────────────────────── EKS cluster ─────────────────────────────────────┐        │
│     │  namespace pulse:     pulse-api (2+, HPA)   pulse-scheduler (2)                   │        │
│     │                       pulse-worker (1..N, KEDA on SQS depth)   pulse-target       │        │
│     │  platform namespaces: Argo CD · kube-prometheus-stack · Loki + Alloy · KEDA       │        │
│     │                       External Secrets Operator · AWS LB Controller · metrics-server│      │
│     └───────────────────────────────────────────────────────────────────────────────────┘        │
│  Database subnets: RDS PostgreSQL (private, encrypted, SSL enforced)                            │
│  Regional services: SQS (checks queue + DLQ) · Secrets Manager · ECR · CloudWatch               │
└─────────────────────────────────────────────────────────────────────────────────────────────────┘
```

### Data flow

1. Admin creates a monitor: `POST /api/v1/monitors`.
2. **Scheduler** finds due monitors in Postgres, sends one SQS message per check, and advances `next_check_at`.
3. **Workers** long-poll SQS, run the HTTP check, store the result, update monitor state, open/resolve incidents, and send notifications.
4. **API** serves the status page and JSON stats from Postgres.
5. **Prometheus** scrapes `/metrics` from the API, scheduler, and workers; **Alertmanager** sends platform alerts to Slack/Discord.
6. **pulse-target** is an in-cluster "chaos target" with endpoints that can be slow, flaky, or down on demand, used for demos, alert tests, and load tests.

### Key design decisions (each becomes an ADR in `docs/adr/`)

| ADR | Decision | Why |
|---|---|---|
| 0001 | SQS instead of Redis/RabbitMQ | Fully managed, built-in DLQ, KEDA scaler support, nothing to operate |
| 0002 | Scheduler uses `SELECT … FOR UPDATE SKIP LOCKED` | Multiple scheduler replicas run safely without leader election |
| 0003 | One container image, multiple entrypoints | One build, one scan, one version to promote |
| 0004 | Terraform owns AWS; Argo CD owns everything inside Kubernetes | Clear ownership boundary; avoids Terraform/Kubernetes provider chicken-and-egg problems |
| 0005 | Separate config repo (`pulse-gitops`) updated by CI | Classic GitOps pattern; CI never needs cluster credentials; every deploy is a git commit |
| 0006 | EKS managed node groups (not Fargate/Auto Mode) | Learn node-level concerns: capacity, IMDS, spot, draining |
| 0007 | SSRF protection on user-supplied URLs | Workers fetch arbitrary URLs from inside the VPC; must not reach private IPs or instance metadata |
| 0008 | Build once, promote the same image tag dev → prod | What you tested is exactly what you ship |

---

## 4. Tech stack

| Area | Choice | Notes |
|---|---|---|
| Language | Python 3.12+ | FastAPI, Pydantic v2, pydantic-settings, SQLAlchemy 2.x (async) + asyncpg, Alembic, httpx, Jinja2, structlog, prometheus-client, aioboto3 |
| Packaging | uv | Lockfile committed |
| Quality | ruff (lint + format), mypy (optional), pytest, pytest-asyncio, pytest-cov | |
| Queue | Amazon SQS | Local: ElasticMQ (SQS-compatible, open source) |
| Database | Amazon RDS for PostgreSQL | Latest major version RDS supports at build time |
| Containers | Docker, multi-stage, non-root | Base image pinned by digest |
| IaC | Terraform ≥ 1.11 | S3 backend with native lockfile locking; `terraform-aws-modules` for VPC/EKS/RDS; tflint; Checkov |
| Kubernetes | Amazon EKS, Helm, Argo CD | KEDA, External Secrets Operator, AWS Load Balancer Controller, metrics-server, Cluster Autoscaler |
| Observability | kube-prometheus-stack | Prometheus, Alertmanager, Grafana; Loki + Grafana Alloy for logs (Promtail is deprecated; don't use it) |
| CI | GitHub Actions | OIDC to AWS, Trivy, Dependabot, actionlint |
| Load testing | k6 | |
| Notifications | Slack or Discord incoming webhooks | Free |

> Version policy: when Claude Code adds a dependency, chart, Terraform module, or provider, pin it to the latest stable release at that time and record it. Don't use `latest` anywhere.

---

## 5. Repository layout

Two repositories: one for code and infrastructure, one for deployment config (watched by Argo CD).

```
pulse/                                  # code + infra repo
├── CLAUDE.md
├── plan.md
├── README.md
├── Makefile
├── Dockerfile
├── docker-compose.yml
├── .pre-commit-config.yaml
├── .github/
│   ├── dependabot.yml
│   └── workflows/
│       ├── app-ci.yml                  # lint, test, build, scan, push, promote to dev
│       ├── infra-ci.yml                # fmt, validate, tflint, checkov, plan → PR comment
│       ├── infra-apply.yml             # manual apply per environment (approval for prod)
│       ├── promote-prod.yml            # manual promotion of a tested image tag to prod
│       └── workflow-lint.yml           # actionlint (+ zizmor optional)
├── app/
│   ├── pyproject.toml
│   ├── uv.lock
│   ├── alembic.ini
│   ├── migrations/
│   ├── src/pulse/
│   │   ├── core/          # config.py, db.py, models.py, queue.py, metrics.py, logging.py,
│   │   │                  # url_safety.py, notifier.py, health_server.py
│   │   ├── api/           # main.py, deps.py, schemas.py, routes/, templates/, static/
│   │   ├── scheduler/     # main.py
│   │   ├── worker/        # main.py, checker.py, state.py (incident state machine)
│   │   ├── target/        # main.py (chaos target)
│   │   └── maintenance/   # main.py (retention cleanup)
│   └── tests/
│       ├── unit/
│       └── integration/
├── local/
│   ├── elasticmq.conf
│   ├── prometheus.yml
│   └── seed.py
├── infra/
│   ├── bootstrap/                      # state bucket, GitHub OIDC, CI roles, ECR, budgets
│   ├── modules/
│   │   ├── network/
│   │   ├── eks/
│   │   ├── rds/
│   │   ├── sqs/
│   │   ├── workload-iam/
│   │   └── secrets/
│   └── envs/
│       ├── dev/
│       └── prod/
├── scripts/
│   ├── argocd-bootstrap.sh
│   ├── pre-destroy-cleanup.sh
│   └── render-infra-values.sh
├── loadtest/
│   ├── api_read.js
│   ├── rollout_under_load.js
│   └── seed_monitors.py
└── docs/
    ├── architecture.md
    ├── slo.md
    ├── security.md
    ├── cost.md
    ├── load-test-results.md
    ├── adr/
    ├── runbooks/
    ├── postmortems/
    └── images/

pulse-gitops/                           # config repo watched by Argo CD
├── charts/pulse/
│   ├── Chart.yaml
│   ├── values.yaml
│   ├── dashboards/                     # Grafana dashboards as JSON
│   └── templates/
├── envs/
│   ├── dev/
│   │   ├── values.yaml                 # image tag, replicas, feature toggles
│   │   └── infra-values.yaml           # generated from terraform outputs (no secrets)
│   └── prod/
│       ├── values.yaml
│       └── infra-values.yaml
├── argocd/
│   ├── dev/                            # root app + platform apps (app-of-apps)
│   └── prod/
└── .github/workflows/chart-ci.yml      # helm lint + kubeconform
```

---

## 6. Conventions

### Git

- `main` is protected in the `pulse` repo: PRs required, status checks required, squash merge.
- Branch names: `feat/…`, `fix/…`, `infra/…`, `docs/…`.
- Commit messages: Conventional Commits (`feat:`, `fix:`, `infra:`, `docs:`, `chore:`, `test:`).

### Application

- Configuration only through environment variables prefixed `PULSE_` (12-factor).
- Logs: JSON to stdout via structlog. Every line includes `service`, `level`, `event`, and where relevant `request_id` or `monitor_id`. **Redact query strings from URLs in logs** (they may contain tokens).
- All timestamps UTC, stored as `timestamptz`.
- Every process exposes `/healthz` (alive), `/readyz` (dependencies reachable), and `/metrics`. Worker and scheduler expose these on port `9100` via a tiny HTTP server; the API serves them on port `8000`; the chaos target serves everything on port `8080`.
- Prometheus metric names prefixed `pulse_`. **Never** use `monitor_id`, URL, or other unbounded values as labels (cardinality).

### Environment variables

| Variable | Used by | Example / default | Notes |
|---|---|---|---|
| `PULSE_ENV` | all | `local` / `dev` / `prod` | |
| `PULSE_LOG_LEVEL` | all | `INFO` | |
| `PULSE_DB_HOST` / `PULSE_DB_PORT` / `PULSE_DB_NAME` | api, scheduler, worker, maintenance | `localhost` / `5432` / `pulse` | |
| `PULSE_DB_USER` / `PULSE_DB_PASSWORD` | same | from secret | Never in git |
| `PULSE_DB_SSLMODE` | same | `disable` locally, `require` on AWS | |
| `PULSE_AWS_REGION` | scheduler, worker | your region | |
| `PULSE_SQS_QUEUE_URL` | scheduler, worker | | |
| `PULSE_SQS_DLQ_URL` | scheduler | | For DLQ depth metric |
| `PULSE_SQS_ENDPOINT_URL` | scheduler, worker | `http://elasticmq:9324` | **Local only**; unset on AWS |
| `PULSE_ADMIN_API_KEY` | api | from secret | Protects write endpoints |
| `PULSE_ALERT_WEBHOOK_URL` | worker | from secret | Slack or Discord webhook |
| `PULSE_ALLOW_PRIVATE_HOSTS` | api, worker | `pulse-target` | Hostnames exempt from SSRF blocking (chaos target only) |
| `PULSE_WORKER_CONCURRENCY` | worker | `50` | Max in-flight checks per pod |
| `PULSE_SCHEDULER_INTERVAL_SECONDS` | scheduler | `5` | Loop interval |
| `PULSE_SCHEDULER_BATCH_SIZE` | scheduler | `500` | Max monitors per loop |
| `PULSE_RETENTION_DAYS` | maintenance | `30` | |
| `PULSE_CHAOS_BREAK_READINESS` | api | `false` | Used in Phase 8 drill only |

### AWS

- Tags on everything: `Project=pulse`, `Environment=dev|prod`, `ManagedBy=terraform`, `Owner=<your-name>`.
- Naming: `pulse-<env>-<resource>` (for example `pulse-dev-checks`).
- One region for everything; pick one close to you and record it in `CLAUDE.md`.

### Makefile targets (build them up over the phases)

| Target | Phase | Does |
|---|---|---|
| `make fmt` / `make lint` / `make test` | 1 | Format, lint, run tests |
| `make up` / `make down` / `make logs` / `make seed` | 2 | Local stack via docker compose |
| `make infra-plan ENV=dev` | 3 | `terraform plan` for an environment |
| `make infra-up ENV=dev` | 3 | Apply bootstrap-independent infra for an environment |
| `make kubeconfig ENV=dev` | 3 | `aws eks update-kubeconfig` |
| `make argocd-bootstrap ENV=dev` | 5 | Install Argo CD and the root app |
| `make infra-down ENV=dev` | 5 | Pre-destroy cleanup, then `terraform destroy` |
| `make grafana` / `make argocd-ui` / `make prometheus` | 5–6 | Port-forward UIs |
| `make loadtest-read` / `make loadtest-storm` | 7 | k6 scenarios |

---

## 7. Cost guardrails (read before Phase 3)

These AWS resources charge **by the hour even when idle**: EKS control plane, NAT gateway, EC2 worker nodes, RDS instance, Application Load Balancer, EBS volumes, and public IPv4 addresses. Smaller ongoing charges: Secrets Manager (per secret per month), CloudWatch Logs ingestion and storage.

Rules:

- [ ] **Day 1:** AWS Budgets alert (50%, 80%, 100% of your monthly limit) emailed to you. Optionally enable Cost Anomaly Detection.
- [ ] **Phases 1–2 cost $0.** Build and test everything locally first. Test the Helm chart on a local `kind` cluster before EKS (Phase 5.0).
- [ ] **Dev runs only while you're working.** End every session with `make infra-down ENV=dev`. Being able to rebuild everything in about 25 minutes is itself a talking point.
- [ ] **Infrastructure apply is manual only** (never automatic on merge), so CI can't recreate an environment you destroyed and surprise you with a bill.
- [ ] Dev uses: single NAT gateway, spot nodes, smallest RDS class, short log and metric retention.
- [ ] Check Cost Explorer weekly; filter by the `Project=pulse` tag (activate it as a cost allocation tag).
- [ ] `prod` exists only to prove it works: bring it up for a demo or screenshots, then destroy it.
- [ ] Produce `docs/cost.md` using the AWS Pricing Calculator (24/7 cost vs. "on only while working") plus your actual spend from Cost Explorer. Don't guess numbers.

---

## 8. Timeline

Part-time pace (about 10–15 hours/week). Adjust freely.

| Phase | Focus | Weeks | AWS cost |
|---|---|---|---|
| 0 | Setup | 0.5 | ~$0 |
| 1 | Application | 1.5 | $0 |
| 2 | Containers + local stack | 0.5 | $0 |
| 3 | Terraform | 1.5 | Low (only while testing) |
| 4 | CI | 1 | ~$0 (ECR storage) |
| 5 | Kubernetes + GitOps | 1.5 | Medium (cluster up while working) |
| 6 | Observability | 1 | Medium |
| 7 | Security, scaling, load tests | 1 | Medium |
| 8 | Chaos drills + postmortems | 1 | Medium |
| 9 | Docs + presentation | 1 | Low |
| **Total** | | **~10 weeks** | |

Push to GitHub after every phase so your profile shows steady progress.

---

## Phase 0: Setup

### 0.1 AWS account hygiene

- [ ] Enable MFA on the root user. Stop using root after this step.
- [ ] Enable IAM Identity Center; create a user for yourself with `AdministratorAccess` (for learning; CI will use narrow roles).
- [ ] Configure the CLI: `aws configure sso` → profile `pulse`. Verify with `aws sts get-caller-identity --profile pulse`.
- [ ] Choose one region and note it in `CLAUDE.md`.
- [ ] Create the AWS Budgets alert (see section 7).

### 0.2 Local tools

- [ ] git, GitHub CLI (`gh`), Docker (Desktop or Engine with compose v2)
- [ ] Python 3.12+, `uv`
- [ ] Terraform ≥ 1.11, `tflint`, `checkov`
- [ ] AWS CLI v2, `kubectl`, `helm`, `kind`, `k9s` (optional), `yq`, `jq`
- [ ] `k6`, `pre-commit`, `trivy` (for local image scans)

### 0.3 GitHub

- [ ] Create public repos `pulse` and `pulse-gitops`.
- [ ] Protect `main` in `pulse` (require PR, require status checks once CI exists).
- [ ] Create GitHub Environments in `pulse`: `dev` (no approval) and `prod` (required reviewer: you).

### 0.4 Notifications

- [ ] Create a Slack workspace or Discord server with two channels: `#pulse-alerts` (product alerts from Pulse) and `#pulse-platform` (Alertmanager alerts). Create an incoming webhook for each. Keep the URLs out of git.

### 0.5 Repo hygiene

- [ ] `.gitignore` (Python, Terraform: `.terraform/`, `*.tfstate*`, `*.tfvars` containing anything sensitive, `.env`).
- [ ] `.pre-commit-config.yaml`: ruff, ruff-format, terraform fmt, tflint, end-of-file-fixer, trailing-whitespace, detect-private-key, gitleaks.
- [ ] `CLAUDE.md` from Appendix A.

**Definition of Done:** `aws sts get-caller-identity` works with SSO; budget alert exists; both repos exist; pre-commit runs on commit.

---

## Phase 1: Application

Everything in this phase runs locally against Postgres and ElasticMQ (via docker compose services you can start early).

### 1.1 Scaffold

- [ ] `uv init` in `app/` with package `pulse` under `src/`.
- [ ] Console entrypoints in `pyproject.toml`: `pulse-api`, `pulse-scheduler`, `pulse-worker`, `pulse-target`, `pulse-maintenance`.
- [ ] `core/config.py`: a pydantic-settings `Settings` class for every variable in section 6, with validation and sensible defaults. Builds the async DB URL from the `PULSE_DB_*` parts.
- [ ] `core/logging.py`: structlog JSON config, with a processor that redacts URL query strings.
- [ ] `core/health_server.py`: minimal async HTTP server for `/healthz`, `/readyz`, `/metrics` on port 9100 (used by non-API processes).
- [ ] Makefile targets: `fmt`, `lint`, `test`.

### 1.2 Data model and migrations

**`monitors`**

| Column | Type | Notes |
|---|---|---|
| `id` | uuid PK | `gen_random_uuid()` |
| `name` | text | 1–100 chars |
| `url` | text | http/https only, max 2048 chars |
| `method` | text | `GET` or `HEAD`, default `GET` |
| `interval_seconds` | int | default 60, check 30–3600 |
| `timeout_ms` | int | default 5000, check 1000–30000 |
| `expected_status_min` / `expected_status_max` | int | default 200 / 399 |
| `keyword` | text null | optional text that must appear in the body |
| `failure_threshold` | int | default 3, check 1–10 |
| `is_active` | bool | default true (false = paused) |
| `is_public` | bool | default true (shown on status page) |
| `status` | text | `up` / `down` / `unknown` / `paused`, default `unknown` |
| `consecutive_failures` | int | default 0 |
| `last_checked_at` | timestamptz null | |
| `next_check_at` | timestamptz | default `now()` |
| `tls_expires_at` / `tls_checked_at` | timestamptz null | |
| `created_at` / `updated_at` | timestamptz | |

Index: `(next_check_at) WHERE is_active` (partial index for the scheduler query).

**`check_results`**

| Column | Type | Notes |
|---|---|---|
| `id` | bigint identity PK | |
| `monitor_id` | uuid FK → monitors, `ON DELETE CASCADE` | |
| `scheduled_for` | timestamptz | from the SQS message |
| `checked_at` | timestamptz | |
| `success` | bool | |
| `status_code` | int null | |
| `response_time_ms` | int null | |
| `error_type` | text null | `timeout`, `dns`, `connection`, `tls`, `status`, `keyword`, `blocked`, `other` |
| `error_message` | text null | truncated to 500 chars |
| `worker_id` | text | pod name |

Constraints and indexes: `UNIQUE (monitor_id, scheduled_for)` (idempotency: a duplicate message can't create a duplicate result); index `(monitor_id, checked_at DESC)`.

**`incidents`**

| Column | Type | Notes |
|---|---|---|
| `id` | uuid PK | |
| `monitor_id` | uuid FK | |
| `started_at` | timestamptz | time of first failed check in the streak |
| `resolved_at` | timestamptz null | |
| `first_error` | text | |
| `notified_open_at` / `notified_resolved_at` | timestamptz null | |

Partial unique index: one open incident per monitor, `UNIQUE (monitor_id) WHERE resolved_at IS NULL`.

- [ ] SQLAlchemy 2.x models in `core/models.py`, async session factory in `core/db.py`.
- [ ] Alembic configured for async; initial migration creates all of the above.
- [ ] `alembic upgrade head` works against local Postgres.

### 1.3 URL safety (SSRF protection)

Workers fetch user-supplied URLs from inside your VPC. Without protection, someone could make Pulse request `http://169.254.169.254/` (instance metadata) or internal services.

- [ ] `core/url_safety.py` with `validate_url(url) -> ValidatedTarget` that:
  - allows only `http` and `https`;
  - resolves the hostname (all A/AAAA records);
  - rejects if **any** resolved IP is loopback, private (10/8, 172.16/12, 192.168/16), link-local (169.254/16, includes the metadata endpoint), carrier-grade NAT (100.64/10), multicast, reserved/unspecified, IPv6 loopback, unique-local (fc00::/7), or link-local (fe80::/10);
  - exempts hostnames listed in `PULSE_ALLOW_PRIVATE_HOSTS` (only the chaos target).
- [ ] Validate at **creation** (API returns 400) **and at check time** (DNS can change between the two; this defends against DNS rebinding).
- [ ] Redirects: don't let httpx follow them automatically. Follow manually, max 3 hops, validating every hop.
- [ ] Table-driven unit tests covering every blocked range plus allowed public URLs.
- [ ] Document residual risk in ADR 0007 (for example, the gap between resolving and connecting).

### 1.4 API (FastAPI)

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/healthz` | none | Process alive (always 200) |
| GET | `/readyz` | none | DB reachable (`SELECT 1`, 1s timeout) else 503 |
| GET | `/metrics` | none | Prometheus metrics |
| POST | `/api/v1/monitors` | key | Create (runs URL safety check) |
| GET | `/api/v1/monitors` | none | List public monitors (all, if key provided) |
| GET | `/api/v1/monitors/{id}` | none | Detail |
| PATCH | `/api/v1/monitors/{id}` | key | Update |
| DELETE | `/api/v1/monitors/{id}` | key | Delete |
| POST | `/api/v1/monitors/{id}/pause` and `/resume` | key | Pause/resume |
| GET | `/api/v1/monitors/{id}/results?limit=100&before=…` | none | Recent check results (cursor pagination) |
| GET | `/api/v1/monitors/{id}/stats` | none | Uptime % for 24h/7d/30d, avg and p95 latency (24h) |
| GET | `/api/v1/incidents?open=true` | none | Incidents |
| GET | `/` | none | Status page (HTML) |
| GET | `/monitors/{id}` | none | Monitor detail page (HTML) |

- [ ] Auth: `X-API-Key` header compared with `hmac.compare_digest` against `PULSE_ADMIN_API_KEY`.
- [ ] Pydantic request/response schemas; consistent error shape `{"error": {"code": "...", "message": "..."}}`.
- [ ] Middleware: request ID (`X-Request-ID`, generated if absent), JSON access log, metrics.
- [ ] Metrics: `pulse_http_requests_total{method,route,status}` and `pulse_http_request_duration_seconds{method,route}` (histogram). Use the **route template** (`/api/v1/monitors/{id}`), never the raw path.
- [ ] Stats in SQL (use `percentile_cont(0.95)` for p95).
- [ ] Status page (Jinja2, no JS framework): overall banner ("All systems operational" / "Partial outage" / "Major outage"), and for each monitor: status dot, uptime 24h/7d/30d, last response time, a bar of the last 90 checks, TLS days remaining. Detail page: 24h latency chart (inline SVG or Chart.js from a CDN) and incident history. Show a "data may be stale" banner if the newest check result is older than 3 minutes (you'll appreciate this in Phase 8).
- [ ] `PULSE_CHAOS_BREAK_READINESS=true` makes `/readyz` always return 503 (Phase 8 drill).

### 1.5 Scheduler

Loop every `PULSE_SCHEDULER_INTERVAL_SECONDS`:

```text
BEGIN;
  SELECT id, interval_seconds, next_check_at
    FROM monitors
   WHERE is_active AND next_check_at <= now()
   ORDER BY next_check_at
   LIMIT :batch_size
   FOR UPDATE SKIP LOCKED;          -- other scheduler replicas skip these rows

  send SQS messages in batches of 10 (SendMessageBatch):
     {"monitor_id": "...", "scheduled_for": "<next_check_at ISO8601>"}

  for each row:
     next = next_check_at + interval
     if next <= now(): next = now() + interval   -- don't "catch up" with a burst after downtime
     UPDATE monitors SET next_check_at = next WHERE id = ...;
COMMIT;
```

If SQS succeeds but the commit fails, the next loop re-sends; the `UNIQUE (monitor_id, scheduled_for)` constraint makes the duplicate harmless. Explain this in ADR 0002.

- [ ] Every 15 seconds, also read `ApproximateNumberOfMessages` and `ApproximateNumberOfMessagesNotVisible` for the queue and DLQ, and count active monitors.
- [ ] Metrics: `pulse_scheduler_checks_enqueued_total`, `pulse_scheduler_loop_duration_seconds` (histogram), `pulse_scheduler_due_monitors` (gauge), `pulse_scheduler_lag_seconds` (gauge: how overdue the oldest due monitor is), `pulse_queue_messages{queue="checks|dlq",state="visible|inflight"}` (gauge), `pulse_active_monitors` (gauge).
- [ ] Graceful shutdown on SIGTERM (finish the current loop, then exit).
- [ ] Health server on :9100; `/readyz` checks the DB.
- [ ] Integration test: run two schedulers against the same DB for 60 seconds; assert no `(monitor_id, scheduled_for)` was enqueued twice.

### 1.6 Worker

- [ ] Long-poll SQS with aioboto3: `WaitTimeSeconds=20`, `MaxNumberOfMessages=10`.
- [ ] Concurrency limited by an `asyncio.Semaphore(PULSE_WORKER_CONCURRENCY)`.
- [ ] Shared `httpx.AsyncClient` with sensible connection limits and `User-Agent: PulseMonitor/1.0 (+<your repo URL>)`.

Check procedure for each message:

1. Load the monitor. If missing or inactive, delete the message and stop.
2. Run URL safety validation. If blocked, record `error_type=blocked`.
3. Send the request with the monitor's timeout; follow redirects manually (max 3, validate each hop). Measure `response_time_ms` with a monotonic clock.
4. Classify: success if the status is within `[expected_status_min, expected_status_max]` and, if `keyword` is set, it appears in the body (read at most 1 MB).
5. TLS: for `https` URLs, if `tls_checked_at` is older than 6 hours, open a TLS connection, read the certificate's expiry, update `tls_expires_at`/`tls_checked_at`.
6. In **one transaction**:
   - `INSERT` the check result with `ON CONFLICT (monitor_id, scheduled_for) DO NOTHING`. If nothing was inserted, it's a duplicate: skip to step 8.
   - Lock the monitor row (`SELECT … FOR UPDATE`) and update `status`, `consecutive_failures`, `last_checked_at`.
   - Run the incident state machine (`worker/state.py`): open an incident when `consecutive_failures` reaches `failure_threshold` and none is open; resolve the open incident on the first success.
7. After commit, send notifications (incident opened / resolved / TLS expiring in < 14 days, at most once per day per monitor). Retry the webhook 3 times with exponential backoff; record success/failure in metrics and set `notified_*_at`.
8. Delete the SQS message **only after** the DB commit. On an unhandled exception, don't delete it: SQS redelivers after the visibility timeout, and after 3 receives it moves to the DLQ.

- [ ] Graceful shutdown: on SIGTERM stop receiving, wait up to 25 seconds for in-flight checks, then exit (pod `terminationGracePeriodSeconds: 30`).
- [ ] Metrics: `pulse_checks_total{result="success|failure",error_type}`, `pulse_check_duration_seconds` (histogram), `pulse_check_schedule_lag_seconds` (histogram of `checked_at − scheduled_for`), `pulse_worker_inflight_checks` (gauge), `pulse_notifications_total{kind,result}`, `pulse_incidents_opened_total`, `pulse_worker_message_errors_total`.
- [ ] Unit tests for classification and the incident state machine (table-driven sequences like `S S F F F S` → expected status and incident events).

### 1.7 Notifier

- [ ] `core/notifier.py` with a `Notifier` interface and implementations: `SlackWebhookNotifier`, `DiscordWebhookNotifier` (choose by webhook URL host), and `LogNotifier` (local default when no webhook is set).
- [ ] Event types: `incident_opened`, `incident_resolved` (includes downtime duration), `tls_expiring`.
- [ ] Example message: `🔴 DOWN: Marketing site (https://example.com) — timeout after 5000 ms. 3 consecutive failures since 14:02 UTC.` and `✅ RECOVERED: Marketing site — down for 7m 12s.`

### 1.8 Chaos target (`pulse-target`)

A tiny FastAPI app used for demos, alert tests, and load tests. It is never exposed publicly (ClusterIP only).

| Endpoint | Behavior |
|---|---|
| `GET /ok` | 200 |
| `GET /slow?ms=2000` | Sleeps, then 200 (cap at 30000) |
| `GET /flaky?fail_rate=0.3` | Random 500s at the given rate |
| `GET /status/{code}` | Returns that status code |
| `GET /switch` | 200 with body `PULSE_OK` when "up", 503 when "down" |
| `POST /switch/up` and `POST /switch/down` | Flip the switch |

- [ ] Serves everything (including `/healthz`) on port 8080.
- [ ] State is in memory (fine for a demo; note this in its docstring).

### 1.9 Data retention

- [ ] `pulse-maintenance` deletes `check_results` older than `PULSE_RETENTION_DAYS` in batches of 10,000 rows (avoid one giant delete). Runs daily as a Kubernetes CronJob in Phase 5.

### 1.10 Tests

- [ ] Unit: URL safety, classification, next-check computation, incident state machine, config validation.
- [ ] Integration (Postgres + ElasticMQ from compose): create monitor via API → scheduler enqueues → worker checks `pulse-target` → result stored → switch target down → incident opens after 3 failures → `LogNotifier` emits event → switch up → incident resolves.
- [ ] Coverage report; aim for ≥ 80% on `core/`, `worker/`, `scheduler/`.

**Definition of Done:** `make lint` and `make test` pass. Running all processes locally, a monitor on `pulse-target/switch` flips to `down` within about 3 minutes of switching the target down, a notification is logged, and the status page shows it.

---

## Phase 2: Containers and local stack

### 2.1 Dockerfile

- [ ] Multi-stage: builder stage installs dependencies with uv into a virtualenv; runtime stage based on a slim Python image **pinned by digest**.
- [ ] Runs as non-root (uid `10001`), no build tools in the final image, `PYTHONUNBUFFERED=1`, `PYTHONDONTWRITEBYTECODE=1`.
- [ ] Works with a read-only root filesystem (only `/tmp` writable).
- [ ] One image; the command selects the process (`pulse-api`, `pulse-worker`, …).
- [ ] `.dockerignore` excludes tests, docs, `.git`, caches.
- [ ] Target size under 200 MB; record the actual size in the README.

### 2.2 docker-compose

- [ ] Services: `postgres`, `elasticmq` (with `local/elasticmq.conf` defining `pulse-local-checks` and its DLQ with `maxReceiveCount=3`), `migrate` (one-shot `alembic upgrade head`), `api` (port 8000), `scheduler`, `worker` (2 replicas), `target` (set `PULSE_ALLOW_PRIVATE_HOSTS=target` on api and worker), `prometheus` (scrapes the services, port 9090), `grafana` (optional, port 3000).
- [ ] Health checks and `depends_on: condition: service_healthy` so startup order is reliable.
- [ ] `local/seed.py` (via `make seed`): creates monitors for `http://target:8080/ok`, `/slow?ms=800`, `/flaky?fail_rate=0.2`, `/switch`, plus one real public site such as `https://example.com`.
- [ ] Makefile: `up`, `down` (removes volumes), `logs`, `seed`, `target-down`, `target-up`.

### 2.3 Local scan

- [ ] `trivy image pulse:local`; fix all HIGH/CRITICAL findings that have a fix available.

**Definition of Done:** fresh clone → `make up && make seed` → status page at `http://localhost:8000` shows live data within 2 minutes; `make target-down` turns the `/switch` monitor red within about 3 minutes.

---

## Phase 3: Infrastructure with Terraform

> Read section 7 (cost guardrails) first. Review every `terraform plan` yourself before applying.

### 3.1 Bootstrap (`infra/bootstrap/`, applied once by you)

This root module creates the things everything else depends on.

- [ ] **State bucket** `pulse-tfstate-<account-id>`: versioning on, server-side encryption, all public access blocked, bucket policy denying non-TLS requests.
- [ ] **GitHub OIDC identity provider** for `token.actions.githubusercontent.com`.
- [ ] **ECR repository** `pulse`: immutable tags, scan on push, lifecycle policy (keep the last 30 tagged images, expire untagged after 1 day). Shared by dev and prod (build once, promote the same image).
- [ ] **CI IAM roles**, each trusting the OIDC provider with `aud = sts.amazonaws.com` and a tightly scoped `sub` (never wildcard the repo):

| Role | Trust (`sub` condition) | Permissions |
|---|---|---|
| `pulse-ci-ecr-push` | `repo:<you>/pulse:ref:refs/heads/main` | Push/pull and `DescribeImages`/`DescribeImageScanFindings` on the `pulse` ECR repo only, plus `ecr:GetAuthorizationToken` |
| `pulse-ci-tf-plan` | `repo:<you>/pulse:pull_request` and `repo:<you>/pulse:ref:refs/heads/main` (for the plan job in `infra-apply.yml`) | `ReadOnlyAccess` + read/write on the state bucket (plan needs to write the lock file) |
| `pulse-ci-tf-apply` | `repo:<you>/pulse:environment:dev` and `…:environment:prod` | Permissions to manage the project's resources (start broad, then tighten in Phase 7 using IAM Access Analyzer) |

- [ ] **AWS Budget** (if you didn't create it manually in Phase 0) and optional Cost Anomaly Detection monitor.
- [ ] Outputs: role ARNs, ECR repo URL, state bucket name. Store the ARNs as GitHub **repository variables** (they aren't secrets).
- [ ] After the first apply, migrate bootstrap's own state into the bucket (`terraform init -migrate-state` with a `bootstrap/terraform.tfstate` key) so it isn't lost on your laptop.

### 3.2 Backend and environment layout

Each environment (`infra/envs/dev`, `infra/envs/prod`) is a root module calling shared modules with different variables.

```hcl
terraform {
  required_version = ">= 1.11"
  backend "s3" {
    bucket       = "pulse-tfstate-<account-id>"
    key          = "envs/dev/terraform.tfstate"
    region       = "<your-region>"
    encrypt      = true
    use_lockfile = true # S3-native state locking; no DynamoDB table needed
  }
}
```

- [ ] Pin provider and module versions in `versions.tf`; commit `.terraform.lock.hcl`.
- [ ] `default_tags` in the AWS provider block for the tags in section 6.
- [ ] `dev.tfvars` / `prod.tfvars` hold the differences (sizes, NAT count, Multi-AZ, retention).

### 3.3 Modules

#### `network`

- [ ] VPC `10.20.0.0/16` across 2 AZs (prod could use 3; document the trade-off).
- [ ] Subnets: public `/24`s (ALB, NAT), private `/20`s (nodes and pods; the VPC CNI gives pods VPC IPs, so leave room), database `/24`s (RDS only, no route to the internet).
- [ ] NAT: `single_nat_gateway = true` in dev (cost), one per AZ in prod (high availability). Make it a variable and explain it in an ADR.
- [ ] S3 gateway endpoint (free; ECR image layers come from S3, so this reduces NAT data charges).
- [ ] Subnet tags for the AWS Load Balancer Controller: `kubernetes.io/role/elb = 1` (public), `kubernetes.io/role/internal-elb = 1` (private).
- [ ] VPC flow logs to CloudWatch with short retention (optional in dev).

#### `eks`

- [ ] Use the `terraform-aws-modules/eks/aws` module; Kubernetes version = latest in EKS standard support at build time.
- [ ] Cluster access via **EKS access entries** (not the legacy `aws-auth` ConfigMap). Your SSO admin role gets cluster-admin.
- [ ] API endpoint: public access restricted to your IP (`/32`) plus private access. CI never talks to the cluster (Argo CD pulls), so no CI CIDRs are needed. Mention this in interviews.
- [ ] Managed node group:
  - dev: 2–3 spot nodes, multiple instance types for spot availability (for example `t3.large`, `t3a.large`, `m5.large`);
  - prod: on-demand, spread across AZs;
  - launch template with **IMDSv2 required and hop limit 1** (pods can't reach the node's instance role credentials), encrypted gp3 root volume.
- [ ] EKS add-ons: `vpc-cni` (enable prefix delegation so small nodes can run more pods), `coredns`, `kube-proxy`, `eks-pod-identity-agent`, `aws-ebs-csi-driver` (Prometheus and Loki need volumes).
- [ ] Control plane logging: `api`, `audit`, `authenticator` with short retention in dev.
- [ ] Envelope encryption of Kubernetes secrets with a KMS key.

#### `rds`

- [ ] PostgreSQL (latest major RDS supports), `db.t4g.micro` in dev; a slightly larger class with **Multi-AZ** in prod.
- [ ] Database subnets, `publicly_accessible = false`, security group allowing 5432 **only** from the EKS node/cluster security group.
- [ ] `manage_master_user_password = true`: RDS stores and rotates the password in Secrets Manager, so it never appears in Terraform state or code.
- [ ] Storage encrypted; backup retention 1 day (dev) / 7 days (prod); `deletion_protection` false in dev, true in prod; `skip_final_snapshot` true in dev only.
- [ ] Parameter group: `rds.force_ssl = 1`, `log_min_duration_statement = 500` (log slow queries).

#### `sqs`

- [ ] `pulse-<env>-checks` with DLQ `pulse-<env>-checks-dlq`; redrive `maxReceiveCount = 3`.
- [ ] Visibility timeout 60s (longer than the maximum check time).
- [ ] Message retention **1 hour** on the main queue (a check that's an hour late is useless; say this in interviews), 4 days on the DLQ (time to investigate).
- [ ] SSE with SQS-managed keys.

#### `workload-iam`

One IAM role per workload, attached with **EKS Pod Identity** associations (if an add-on's documentation only covers IRSA, use IRSA for that add-on and note it in an ADR).

| Kubernetes service account | Permissions |
|---|---|
| `pulse/pulse-scheduler` | `sqs:SendMessage`, `sqs:GetQueueAttributes` on the checks queue; `sqs:GetQueueAttributes` on the DLQ |
| `pulse/pulse-worker` | `sqs:ReceiveMessage`, `sqs:DeleteMessage`, `sqs:ChangeMessageVisibility`, `sqs:GetQueueAttributes` on the checks queue |
| `external-secrets/external-secrets` | `secretsmanager:GetSecretValue`, `DescribeSecret` on `pulse/<env>/*` and the RDS-managed secret |
| `keda/keda-operator` | `sqs:GetQueueAttributes` on the checks queue |
| `kube-system/aws-load-balancer-controller` | The official controller policy JSON (vendored into the repo with its version noted) |
| `kube-system/ebs-csi-controller-sa` | AWS-managed `AmazonEBSCSIDriverPolicy` |
| `kube-system/cluster-autoscaler` | Autoscaling permissions scoped to this cluster's node group tags |

#### `secrets`

- [ ] Create the Secrets Manager secret `pulse/<env>/app` (keys: `admin_api_key`, `alert_webhook_url`) and `pulse/<env>/grafana` (key: `admin_password`) **without values in Terraform**.
- [ ] Set the values yourself with `aws secretsmanager put-secret-value …` so secret values never enter Terraform state. Document the commands in `docs/runbooks/set-secrets.md`.

### 3.4 Outputs → Helm values

- [ ] `scripts/render-infra-values.sh ENV`: reads `terraform output -json` and writes `../pulse-gitops/envs/<env>/infra-values.yaml` (queue URLs, region, RDS endpoint, RDS secret ARN, cluster name, ACM certificate ARN if any). No secret values, so it's safe to commit.

### 3.5 Quality gates

- [ ] `terraform fmt -recursive`, `terraform validate` for every root module.
- [ ] `tflint` with the AWS ruleset.
- [ ] `checkov -d infra/`: fix findings or suppress with an inline comment explaining why (for example, "single NAT in dev is a deliberate cost trade-off").

### 3.6 Makefile

- [ ] `infra-plan ENV=…`, `infra-up ENV=…` (plan, show it, ask for confirmation, apply), `kubeconfig ENV=…`. (`infra-down` comes in Phase 5 because it needs a Kubernetes cleanup step first.)

**Definition of Done:** `make infra-up ENV=dev` builds the environment in roughly 20–25 minutes; `make kubeconfig ENV=dev && kubectl get nodes` shows Ready nodes; Checkov and tflint are clean or every suppression is justified. Then destroy it until Phase 5 (a plain `terraform destroy` is fine at this point because nothing has created load balancers yet).

---

## Phase 4: CI with GitHub Actions

### 4.1 Security baseline for every workflow

These rules matter. In March 2026, attackers compromised the popular `aquasecurity/trivy-action` and force-pushed almost all of its version tags to credential-stealing code. Pipelines that referenced the action by tag (for example `@0.28.0`) ran malware; pipelines pinned to a full commit SHA were not affected. The attackers' initial access reportedly came from a misconfigured `pull_request_target` workflow. Mention this incident in interviews; it shows you follow real supply-chain security.

- [ ] **Pin every action to a full 40-character commit SHA** with a version comment: `uses: actions/checkout@<sha> # vX.Y.Z`. Verify SHAs against the action's official releases page. For Trivy specifically, use `trivy-action` v0.35.0 or later.
- [ ] `.github/dependabot.yml` for ecosystems `github-actions`, `pip`/`uv`, `docker`, `terraform` (weekly). Dependabot updates SHA pins and their version comments.
- [ ] Workflow-level `permissions: {}`; grant per job only what it needs (`contents: read`, `id-token: write` only on jobs that assume AWS roles, `pull-requests: write` only for the plan comment, `security-events: write` only for SARIF upload).
- [ ] **Never use `pull_request_target`.** Never echo secrets. Never pass untrusted input (PR titles, branch names) directly into `run:` scripts; use environment variables.
- [ ] No long-lived AWS keys anywhere: OIDC via `aws-actions/configure-aws-credentials`.
- [ ] `concurrency` groups so superseded runs are cancelled and two applies never run at once.
- [ ] `workflow-lint.yml`: `actionlint` on changes to `.github/workflows/**` (optionally also `zizmor`, a security linter for Actions).

### 4.2 `app-ci.yml`

Triggers: `pull_request` and `push` to `main`, paths `app/**`, `Dockerfile`, `.github/workflows/app-ci.yml`.

| Job | When | Steps |
|---|---|---|
| `lint` | always | `uv sync`, `ruff check`, `ruff format --check`, (mypy) |
| `test` | always | Service containers: Postgres + ElasticMQ; `alembic upgrade head`; `pytest --cov`; write coverage summary to `$GITHUB_STEP_SUMMARY` |
| `build-scan` | always | Buildx build with GitHub Actions cache; Trivy image scan (fail on fixable HIGH/CRITICAL); upload SARIF to code scanning (shows in the Security tab); generate an SBOM (CycloneDX) as an artifact |
| `push` | `main` only, needs all above | Assume `pulse-ci-ecr-push` via OIDC; log in to ECR; push tag `sha-<short-sha>` (immutable; **never** `latest`) |
| `promote-dev` | `main` only, needs `push` | Check out `pulse-gitops`, set `.image.tag` in `envs/dev/values.yaml` with `yq`, commit `chore(dev): deploy sha-xxxxxxx`, push |

Pushing to `pulse-gitops` needs a credential scoped to that repo only. Options (record the choice in ADR 0005):

- **Recommended:** a GitHub App installed only on `pulse-gitops`, used via `actions/create-github-app-token` (short-lived tokens).
- **Simpler:** an SSH deploy key with write access to `pulse-gitops` only, stored as secret `GITOPS_DEPLOY_KEY` in `pulse`.

### 4.3 `infra-ci.yml` (pull requests touching `infra/**`)

- [ ] `terraform fmt -check -recursive`.
- [ ] For each root module: `terraform init -backend=false && terraform validate`.
- [ ] `tflint`; `checkov` with SARIF upload.
- [ ] Plan for dev (and prod) using `pulse-ci-tf-plan` via OIDC; post the plan as a **single PR comment that is updated on each push** (truncate long plans and link the full output as an artifact).

### 4.4 `infra-apply.yml` (manual only)

- [ ] Trigger: `workflow_dispatch` with input `environment` (`dev` | `prod`). **Never on push** (see section 7).
- [ ] Job `plan` (uses `pulse-ci-tf-plan`): produce a plan file, show it in the job summary, upload as artifact.
- [ ] Job `apply` (uses `pulse-ci-tf-apply`): `needs: plan`, `environment: <input>` (prod requires your approval), downloads and applies **exactly that plan file**.
- [ ] Concurrency group per environment.

### 4.5 `promote-prod.yml` (manual)

- [ ] Trigger: `workflow_dispatch` with input `tag` (default: the tag currently in `envs/dev/values.yaml`).
- [ ] Job `verify` (no environment; assumes `pulse-ci-ecr-push`, which can describe images): confirms the tag exists in ECR and has no unresolved CRITICAL findings.
- [ ] Job `promote` (`needs: verify`, `environment: prod` so it waits for your approval; no AWS access needed): updates `envs/prod/values.yaml` in `pulse-gitops`.

### 4.6 `pulse-gitops` CI

- [ ] `chart-ci.yml`: `helm lint charts/pulse`, `helm template` with each env's values piped into `kubeconform` (strict, with CRD schemas for KEDA, ExternalSecret, ServiceMonitor, PrometheusRule).

**Definition of Done:** a PR shows green lint/test/scan checks and (for infra changes) a plan comment; merging to `main` produces an image `sha-xxxxxxx` in ECR and a commit in `pulse-gitops` within about 10 minutes; the Security tab shows Trivy results; no workflow references an action by tag.

---

## Phase 5: Kubernetes, Helm and GitOps

### 5.0 Rehearse locally on kind (free)

- [ ] Create a `kind` cluster; install Argo CD; deploy the chart with a `local` values file: Postgres and ElasticMQ as simple in-cluster Deployments, a plain Kubernetes Secret instead of External Secrets, `keda.enabled=false` (fixed worker replicas), `ingress.enabled=false` (port-forward instead).
- [ ] Fix chart problems here, where mistakes cost nothing.

### 5.1 Helm chart (`pulse-gitops/charts/pulse`)

| Template | Details |
|---|---|
| `api` Deployment + Service | 2 replicas; rolling update `maxUnavailable: 0`, `maxSurge: 1`; liveness `/healthz`, readiness `/readyz`, startup probe; topology spread across zones |
| `api` Ingress | ALB via AWS Load Balancer Controller: internet-facing, `target-type: ip`, health check path `/readyz`; HTTPS with an ACM certificate if you have a domain, otherwise HTTP on the ALB DNS name in dev |
| `api` HPA | CPU 70%, min 2, max 6 |
| `scheduler` Deployment | 2 replicas (safe thanks to `SKIP LOCKED`) |
| `worker` Deployment | No `replicas` field when KEDA is enabled (KEDA owns it) |
| `worker` ScaledObject (KEDA) | Trigger `aws-sqs-queue` on the checks queue; target ~50 messages per pod; min 1, max 10; authenticate with pod identity via a `TriggerAuthentication` (check KEDA's docs for the current AWS provider setting) |
| `target` Deployment + ClusterIP Service | Name `pulse-target`; toggle with `target.enabled` |
| Migrations Job | Argo CD `PreSync` hook running `alembic upgrade head`; `hook-delete-policy: BeforeHookCreation`. Migrations must be backward compatible (expand/contract) so old pods keep working during rollout |
| Maintenance CronJob | Daily retention cleanup |
| ServiceAccounts | `pulse-api`, `pulse-scheduler`, `pulse-worker` (names match Pod Identity associations) |
| ExternalSecrets | `pulse-app` (admin key, webhook) from `pulse/<env>/app`; `pulse-db` (username, password) from the RDS-managed secret; a `SecretStore`/`ClusterSecretStore` for AWS Secrets Manager |
| ConfigMap | Non-secret config (queue URLs, region, DB host, log level) |
| PodDisruptionBudgets | `minAvailable: 1` for api, scheduler, worker |
| ServiceMonitors / PodMonitors | Scrape api `:8000/metrics`, scheduler and worker `:9100/metrics` |
| PrometheusRule | Alerts from Phase 6 |
| Dashboard ConfigMaps | JSON from `charts/pulse/dashboards/`, labeled for the Grafana sidecar |
| NetworkPolicies (optional) | Requires enabling network policy support in the VPC CNI. Default-deny ingress in `pulse`; allow ALB → api, Prometheus → metrics ports, worker → target |

Every container must have:

- [ ] `securityContext`: `runAsNonRoot: true`, `runAsUser: 10001`, `readOnlyRootFilesystem: true` (mount an `emptyDir` at `/tmp`), `allowPrivilegeEscalation: false`, `capabilities.drop: [ALL]`, `seccompProfile.type: RuntimeDefault`.
- [ ] Resource requests (CPU and memory) and memory limits. Decide whether to set CPU limits and record why in an ADR (this is a common interview discussion).
- [ ] Probes as above.

Namespace `pulse` labeled `pod-security.kubernetes.io/enforce: restricted`.

### 5.2 Platform apps via Argo CD (app-of-apps in `pulse-gitops/argocd/<env>/`)

| App | Namespace | Notes |
|---|---|---|
| aws-load-balancer-controller | kube-system | Pod Identity role from Terraform |
| cluster-autoscaler | kube-system | Lets the node group grow under load (Phase 7) |
| metrics-server | kube-system | Needed by HPA |
| external-secrets | external-secrets | |
| keda | keda | |
| kube-prometheus-stack | monitoring | Prometheus retention 7 days in dev, gp3 PVC; Grafana admin password from an ExternalSecret |
| loki | monitoring | Single-binary mode with filesystem storage in dev (S3 storage is a stretch goal) |
| alloy | monitoring | DaemonSet shipping pod logs to Loki |
| pulse | pulse | The app chart with `envs/<env>/values.yaml` + `infra-values.yaml` |

- [ ] Pin every chart version.
- [ ] Sync waves: operators and CRDs first (wave −2), secret stores (−1), the app (0).
- [ ] Dev: automated sync with `prune` and `selfHeal`. Prod: automated sync too (the approval gate is the promotion workflow).
- [ ] `ignoreDifferences` for replica counts managed by HPA/KEDA.
- [ ] Argo CD, Grafana, and Prometheus UIs are **not** exposed publicly; use `make argocd-ui`, `make grafana`, `make prometheus` (port-forwards).

### 5.3 Bootstrap

- [ ] `scripts/argocd-bootstrap.sh ENV` (`make argocd-bootstrap ENV=dev`): `helm install` Argo CD (pinned version) → `kubectl apply` the root Application → wait until every app is `Synced` and `Healthy` → print the status page URL.

### 5.4 Teardown

Resources created by Kubernetes controllers (the ALB, its security groups and ENIs, EBS volumes from PVCs) are **not in Terraform state**. If they still exist, `terraform destroy` fails on the VPC and they keep costing money.

- [ ] `scripts/pre-destroy-cleanup.sh ENV`: delete the Argo CD root app with cascade, delete Ingresses and any `LoadBalancer` Services, wait until no load balancers tagged for this cluster remain (`aws elbv2 describe-load-balancers` + tags), delete PVCs and wait for the EBS volumes to disappear.
- [ ] `make infra-down ENV=dev` = cleanup script → `terraform destroy`.
- [ ] After the first teardown, check the console manually (EC2 load balancers, volumes, Elastic IPs, network interfaces, NAT gateways) and confirm nothing is left. Write what you check into `docs/runbooks/teardown.md`.

**Definition of Done:** from nothing, `make infra-up ENV=dev && make argocd-bootstrap ENV=dev` produces a working status page at the ALB URL with live checks. Merging an app change to `main` deploys it to dev automatically with zero downtime, verified by running `loadtest/rollout_under_load.js` during the rollout (0 failed requests). `make infra-down ENV=dev` leaves nothing behind.

---

## Phase 6: Observability

### 6.1 Metrics and scraping

- [ ] All Pulse targets are `UP` in Prometheus (Status → Targets).
- [ ] The metrics from Phase 1 appear with the expected labels and no high-cardinality labels.

### 6.2 Dashboards (JSON in `pulse-gitops/charts/pulse/dashboards/`)

**"Pulse / Service overview"** panels:

- API requests/sec, error %, latency p50/p95/p99
- Checks/sec by result, checks by `error_type`
- Schedule lag p95 (are checks running on time?)
- Queue messages (visible / in-flight) and DLQ depth
- Worker in-flight checks and worker replica count (shows KEDA scaling)
- Incidents opened, notifications sent/failed

**"Pulse / SLOs"**: each SLI, the error budget remaining, burn rate.

Example PromQL:

```promql
# API error ratio (5m)
sum(rate(pulse_http_requests_total{status=~"5.."}[5m]))
  / sum(rate(pulse_http_requests_total[5m]))

# API p95 latency
histogram_quantile(0.95, sum by (le) (rate(pulse_http_request_duration_seconds_bucket[5m])))

# Check schedule lag p95
histogram_quantile(0.95, sum by (le) (rate(pulse_check_schedule_lag_seconds_bucket[5m])))

# Checks per second by result
sum by (result) (rate(pulse_checks_total[5m]))
```

### 6.3 SLOs (`docs/slo.md`)

| SLO | SLI | Target | Window |
|---|---|---|---|
| Status API availability | Share of API requests that are not 5xx | 99.5% | 30 days rolling |
| Check freshness | Share of checks executed within 90s of `scheduled_for` | 99% | 30 days rolling |
| Alert latency (stretch) | Share of incidents notified within 3 minutes of the first failed check | 95% | 30 days rolling |

- [ ] Multi-window burn-rate alerts (from the Google SRE Workbook): **fast burn** = burn rate above 14.4× over both 1h and 5m (page); **slow burn** = above 6× over both 6h and 30m (warning). Use Prometheus recording rules for the ratios.

### 6.4 Alerts (PrometheusRule)

| Alert | Condition (sketch) | For | Severity |
|---|---|---|---|
| `PulseAPIHighErrorRate` | 5xx ratio > 5% | 5m | critical |
| `PulseAPIHighLatency` | p95 > 0.5s | 10m | warning |
| `PulseCheckBacklog` | `pulse_queue_messages{queue="checks",state="visible"}` > 500 | 5m | critical |
| `PulseChecksStalled` | `sum(rate(pulse_checks_total[5m])) == 0` and `pulse_active_monitors > 0` | 5m | critical |
| `PulseScheduleLagHigh` | schedule lag p95 > 90s | 10m | warning |
| `PulseDLQNotEmpty` | `pulse_queue_messages{queue="dlq"}` > 0 | 15m | warning |
| `PulseSchedulerDown` | no `up == 1` series for the scheduler job (use `absent()` as well) | 2m | critical |
| `PulsePodCrashLooping` | `increase(kube_pod_container_status_restarts_total{namespace="pulse"}[15m]) > 3` | 0m | warning |
| `PulseSLOFastBurn` / `PulseSLOSlowBurn` | see 6.3 | — | critical / warning |

- [ ] Each alert has annotations `summary`, `description`, and `runbook_url` → `https://github.com/<you>/pulse/blob/main/docs/runbooks/<AlertName>.md`.
- [ ] Write a runbook for **every** alert using Appendix D.
- [ ] Alertmanager: route to `#pulse-platform` via webhook (webhook URL from an ExternalSecret), `group_by: [alertname]`, `repeat_interval: 4h`, send resolved notifications.
- [ ] Test each alert at least once (Phase 8 drills will trigger most of them).

### 6.5 Logs

- [ ] Loki + Alloy collecting logs from the `pulse` namespace.
- [ ] Saved Grafana Explore queries, for example `{namespace="pulse", container="worker"} | json | level="error"`.
- [ ] Show how to follow one request across logs using `request_id`.

### 6.6 "Who monitors the monitor?"

- [ ] Pulse can't reliably detect its own total outage from inside the same cluster. Add an independent external check of the public status page (a free external uptime service, or a second Pulse in another region as a stretch goal). Write this up in `docs/architecture.md`; it's a strong interview discussion.

**Definition of Done:** dashboards show live data; switching the chaos target down produces a product alert in `#pulse-alerts`; scaling workers to zero produces a platform alert in `#pulse-platform` within about 5–7 minutes; every alert has a runbook.

---

## Phase 7: Security, autoscaling and load testing

### 7.1 Security verification (capture evidence: screenshots or command output in `docs/security.md`)

- [ ] No static AWS credentials anywhere: search the repo history (`gitleaks detect`), CI uses OIDC, pods use Pod Identity.
- [ ] From inside a pod, requests to `169.254.169.254` time out (IMDSv2 hop limit 1).
- [ ] SSRF: creating monitors for `http://169.254.169.254/latest/meta-data/`, `http://localhost`, `http://10.20.0.10`, and a hostname that resolves to a private IP all return 400.
- [ ] RDS: not publicly accessible; security group only from the cluster; non-SSL connections rejected.
- [ ] Pods: non-root, read-only root filesystem; the `restricted` Pod Security Standard is enforced (try deploying a privileged pod and show it's rejected).
- [ ] Secrets: none in git, none in Terraform state (`terraform state pull | grep -iE "password|webhook"` finds nothing sensitive).
- [ ] Supply chain: all actions SHA-pinned, Dependabot PRs arriving, Trivy and ECR scans show no fixable CRITICAL vulnerabilities, SBOM generated per build.
- [ ] IAM: review every role; use IAM Access Analyzer to find unused permissions and tighten `pulse-ci-tf-apply` and the workload roles.
- [ ] Checkov clean or justified.
- [ ] `docs/security.md`: a short threat model (SSRF, secret leakage, supply chain, public exposure, privilege escalation) with the control for each.

### 7.2 Autoscaling

- [ ] API: HPA on CPU (already in chart).
- [ ] Workers: KEDA on SQS depth (already in chart).
- [ ] Nodes: Cluster Autoscaler on the managed node group (max 4–5 nodes in dev). Without node autoscaling, new pods sit in `Pending` under load; reproduce that once on purpose and document it.

### 7.3 Load testing (k6, `loadtest/`)

Only ever load-test endpoints you own (Pulse itself and `pulse-target`).

- [ ] **Scenario A, API read load** (`api_read.js`): 50 virtual users hitting `/` and `/api/v1/monitors/{id}/stats` for 10 minutes. Thresholds in the script: p95 < 300 ms, errors < 0.5%. Watch the HPA react.
- [ ] **Scenario B, check storm** (`seed_monitors.py`): create 2,000 monitors pointing at `pulse-target` (mix of `/ok` and `/slow?ms=500`) with 30-second intervals (~67 checks/second). Watch KEDA add workers, the Cluster Autoscaler add nodes if needed, and schedule lag stay under 90 seconds. Delete the monitors afterwards.
- [ ] **Scenario C, rollout under load** (`rollout_under_load.js`): constant traffic while deploying a new version; expect zero failed requests.
- [ ] **Find and fix one bottleneck.** Likely candidates: database connection pool size, single-row inserts of check results, missing indexes, worker concurrency. Change one thing, re-run, and compare. Record before/after numbers and Grafana screenshots in `docs/load-test-results.md`. "I found a bottleneck under load and fixed it" is one of the strongest things you can say in an interview.

**Definition of Done:** `docs/security.md` and `docs/load-test-results.md` exist with evidence; Scenario B shows workers scaling out and back in; one measured improvement is documented.

---

## Phase 8: Chaos drills and postmortems

Run each drill in dev. For each one, write down the **hypothesis** before you start ("I expect alert X within Y minutes and no data loss"), then record what actually happened with timestamps.

| # | Drill | How | What you expect to see | Lesson to look for |
|---|---|---|---|---|
| 1 | Workers die | Add the annotation `autoscaling.keda.sh/paused-replicas: "0"` to the worker ScaledObject (pauses KEDA and holds workers at 0) | Queue grows; `PulseCheckBacklog` and `PulseChecksStalled` fire; status page shows "data may be stale" | Detection time; whether the 1-hour message retention drops stale checks as intended |
| 2 | Bad deploy | Set `PULSE_CHAOS_BREAK_READINESS=true` in dev values and let Argo CD deploy it | New pods never become ready; old pods keep serving (`maxUnavailable: 0`); Argo CD shows `Degraded`; users unaffected | Roll back with `git revert` in `pulse-gitops`; measure time to recover |
| 3 | Database outage | `aws rds reboot-db-instance` (single-AZ dev) | `/readyz` fails → API pods leave the ALB → 503s; workers fail → messages retried; recovery afterwards | Time to recovery; whether any checks were lost; why prod uses Multi-AZ |
| 4 | Node loss | Terminate one worker node in EC2 (or `kubectl drain` it) | Pods reschedule; PDBs keep at least one API pod serving | Spot interruptions are this drill happening for real |
| 5 | Poison message | Send a malformed message to the checks queue with the AWS CLI | Fails 3 times → moves to DLQ → `PulseDLQNotEmpty` fires | Runbook: inspect, fix, redrive from the DLQ |
| 6 | Monitored site outage (product path) | `POST /switch/down` on `pulse-target` | Incident opens after 3 failures; Slack/Discord message; status page red; `POST /switch/up` → recovery message with duration | Measure detection time against the alert-latency SLO |
| 7 | Noisy monitors (optional) | 500 monitors on `/slow?ms=30000` with 30s timeouts | Worker concurrency saturates; schedule lag rises for **all** monitors | Fairness: per-check timeout caps, concurrency tuning, separate queues |

- [ ] Write **at least 3 postmortems** in `docs/postmortems/` using Appendix C (blameless, with a timeline and action items).
- [ ] At least one action item becomes a real change (code, alert, or infrastructure), linked to its PR. Showing the loop from incident → postmortem → improvement is the point.

**Definition of Done:** all drills 1–6 run and recorded; 3+ postmortems written; at least one improvement merged as a result.

---

## Phase 9: Documentation and presentation

### 9.1 README.md (the page recruiters actually see)

- [ ] One-sentence pitch at the top and an architecture diagram image (draw it in Excalidraw or draw.io; export PNG to `docs/images/`).
- [ ] "What this project demonstrates" (5–7 bullets mapping to job-description keywords: Terraform, EKS, GitOps, CI/CD, observability, SRE practices, security).
- [ ] Screenshots: status page, Grafana overview, Argo CD app tree, a Slack alert, the Security tab.
- [ ] Quick start (local) and Deploy to AWS (step by step, with the cost warning).
- [ ] CI/CD flow explanation (with a small diagram).
- [ ] Links: ADRs, runbooks, postmortems, SLOs, security, cost, load-test results.
- [ ] "Lessons learned" and "What I'd do differently at 100× scale".

### 9.2 Docs checklist

- [ ] `docs/architecture.md`
- [ ] `docs/adr/` (at least the 8 ADRs from section 3, using Appendix B)
- [ ] `docs/runbooks/` (one per alert + `set-secrets.md` + `teardown.md`)
- [ ] `docs/postmortems/` (3+)
- [ ] `docs/slo.md`, `docs/security.md`, `docs/cost.md`, `docs/load-test-results.md`

### 9.3 Demo video (3–5 minutes)

1. Architecture overview (30 seconds).
2. Push a small change → CI runs → Argo CD deploys it.
3. Switch the chaos target down → incident + Slack message → switch it up → recovery.
4. Kill the workers → platform alert → recover → show the dashboard.
5. Close with what you learned.

### 9.4 Resume and LinkedIn

Example resume bullets (edit to match what you actually built and measured):

- *Built Pulse, an uptime-monitoring platform on AWS EKS provisioned with Terraform (VPC, EKS, RDS, SQS), deployed via GitHub Actions and Argo CD GitOps with OIDC authentication and SHA-pinned, Trivy-scanned supply chain.*
- *Implemented Prometheus/Grafana/Loki observability with SLO burn-rate alerting and runbooks; ran 6 chaos drills and wrote blameless postmortems that drove 2 reliability fixes.*
- *Load-tested to ~67 checks/second with KEDA-based worker autoscaling; identified and fixed a database bottleneck, reducing p95 schedule lag from X s to Y s.*

- [ ] LinkedIn post with the architecture diagram and one lesson learned (post once after Phase 5 and again after Phase 8).

---

## Phase 10: Stretch goals

Pick **one or two at most**, only after Phases 0–9 are done.

- [ ] Canary deployments with Argo Rollouts and automated analysis on error rate
- [ ] Karpenter instead of Cluster Autoscaler
- [ ] OpenTelemetry tracing with Grafana Tempo
- [ ] Multi-region checking (a worker pool in a second region; also solves "who monitors the monitor")
- [ ] Transactional outbox for notifications (no lost alerts if the webhook call fails after commit)
- [ ] Postgres native partitioning of `check_results` by day (drop partitions instead of deleting rows)
- [ ] Separate least-privilege database user for the app (created by a migration job), instead of the master user
- [ ] Preview environments per pull request (namespace per PR via Argo CD ApplicationSet)
- [ ] Nightly scheduled workflow that tears down dev unless a `KEEP_DEV` repository variable is set
- [ ] Policy as code with Kyverno (require non-root, resource limits, signed images)
- [ ] Image signing with cosign and verification at admission
- [ ] Loki on S3 storage; Prometheus long-term storage
- [ ] OpenCost for per-namespace cost visibility

---

## Interview preparation

Be able to answer each of these in 1–2 minutes, using your own project as the example:

1. Walk me through what happens from `git push` to code running in production.
2. Why SQS and not Redis or Kafka?
3. How do you stop two scheduler replicas from enqueuing the same check?
4. What happens if a worker crashes halfway through a check?
5. How does CI authenticate to AWS without access keys?
6. Why pin GitHub Actions to commit SHAs? (Tell the March 2026 Trivy story.)
7. How do your pods get AWS permissions, and why not node instance roles?
8. What is SSRF and how did you prevent it in Pulse?
9. What's the difference between black-box and white-box monitoring? Which does Pulse do?
10. What are your SLOs, and why alert on burn rate instead of raw error rate?
11. How do you roll back a bad deployment? How long does it take?
12. Tell me about an incident you handled. (Use a postmortem.)
13. How did you keep AWS costs under control?
14. What breaks first at 100× the load, and what would you change?
15. Why GitOps? What does Argo CD give you over `kubectl apply` in CI?
16. Why does the checks queue keep messages for only 1 hour?

Ask Claude Code: `Interview me on Phase N of plan.md. Ask one question at a time, then critique my answer.`

---

## Appendices: templates

### Appendix A: CLAUDE.md template

```markdown
# Pulse: project guide for Claude Code

## What this is
Uptime monitoring platform on AWS (portfolio project). Full plan: plan.md.
Current phase: <update me, e.g. "Phase 1, task 1.4">
AWS region: <your region>

## Repositories
- pulse (this repo): app/, infra/, scripts/, loadtest/, docs/, .github/
- pulse-gitops (sibling directory ../pulse-gitops): charts/pulse, envs/, argocd/

## Commands
- make up / make down / make seed      # local stack
- make fmt / make lint / make test
- make infra-plan ENV=dev              # read-only
- make infra-up / make infra-down      # ONLY when I explicitly ask

## Conventions
- Python 3.12, FastAPI, SQLAlchemy 2 (async), Alembic, httpx, structlog, prometheus-client. uv for deps.
- Config only via PULSE_* env vars (app/src/pulse/core/config.py).
- JSON logs; never log secrets; redact URL query strings.
- Metrics prefixed pulse_; never use monitor_id, URL, or other unbounded values as labels.
- Every feature has tests; run `make lint && make test` before saying a task is done.
- Terraform: modules in infra/modules, roots in infra/envs; pin versions; run fmt, validate, tflint after changes.
- GitHub Actions: pin third-party actions to full commit SHAs with a version comment; least-privilege
  permissions per job; never use pull_request_target.
- Kubernetes: every container has resource requests, memory limits, probes, non-root read-only securityContext.

## Safety rules
- Never run terraform apply/destroy, AWS CLI commands that create/modify/delete resources,
  or kubectl delete without my explicit OK in this session.
- Never write secret values into any file. Use placeholders and Secrets Manager.
- Keep changes small: one task from plan.md at a time. Propose a plan before large changes.
- When a design choice is made, suggest an ADR and draft it for my review.
- Explain non-obvious code so I can defend it in an interview.
```

### Appendix B: ADR template (`docs/adr/NNNN-title.md`)

```markdown
# NNNN: <Decision title>

- Status: Accepted | Superseded by NNNN
- Date: YYYY-MM-DD

## Context
What problem are we solving? What constraints matter (cost, simplicity, learning goals)?

## Options considered
1. Option A: pros / cons
2. Option B: pros / cons
3. Option C: pros / cons

## Decision
What we chose, in one or two sentences.

## Consequences
What gets easier, what gets harder, what we'd revisit at larger scale.
```

### Appendix C: Postmortem template (`docs/postmortems/YYYY-MM-DD-title.md`)

```markdown
# Postmortem: <short title>

- Date:
- Duration: (start → resolved)
- Severity:
- Author:
- Status: Draft | Final

## Summary
Two or three sentences: what happened, impact, how it was resolved.

## Impact
Who/what was affected and how much (failed requests, stale checks, missed alerts).

## Timeline (UTC)
| Time | Event |
|------|-------|
| 14:00 | Change deployed / fault injected |
| 14:04 | Alert X fired |
| 14:06 | Investigation started |
| 14:11 | Mitigation applied |
| 14:13 | Recovered |

## Detection
How was it detected? How long did it take? Could it have been faster?

## Root cause
The technical cause (blameless: focus on systems, not people).

## Resolution
What fixed it.

## What went well
## What went poorly
## Where we got lucky

## Action items
| Action | Type (prevent/detect/mitigate) | Link (PR/issue) | Status |
|--------|-------------------------------|-----------------|--------|
```

### Appendix D: Runbook template (`docs/runbooks/<AlertName>.md`)

```markdown
# <AlertName>

## What it means
One paragraph: what condition fired and why it matters to users.

## Impact
What users/customers experience while this is firing.

## First checks (5 minutes)
1. Grafana: "Pulse / Service overview" → panels X, Y
2. kubectl -n pulse get pods; kubectl -n pulse describe pod <...>
3. Loki: {namespace="pulse", container="..."} | json | level="error"
4. Argo CD: was anything deployed recently?

## Likely causes and fixes
| Cause | How to confirm | Fix |
|-------|----------------|-----|

## Escalation / follow-up
When to roll back; what to record for the postmortem.
```
