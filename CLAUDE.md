# Pulse: project guide for Claude Code

## What this is
Uptime monitoring platform on AWS (portfolio project). Full plan: plan/plan.md.
Current phase: Phases 1–2 built and tested locally; Phases 3–7 code written but never applied to AWS.
AWS region: us-east-2 (change in infra/envs/*/*.tfvars and here if you pick another)

## Repositories
- pulse (this repo): app/, infra/, scripts/, loadtest/, docs/, .github/
- pulse-gitops (subfolder ./pulse-gitops, its own git repo, ignored by this repo): charts/pulse, envs/, argocd/
- Shared names (queues, secrets, service accounts, metrics): docs/CONTRACT.md

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
