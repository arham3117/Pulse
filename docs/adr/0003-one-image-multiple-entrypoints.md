# 0003: One container image with multiple entrypoints

- Status: Proposed (draft — rewrite in your own words before marking Accepted)
- Date: 2026-11-22

## Context

Pulse has five processes: `pulse-api`, `pulse-scheduler`, `pulse-worker`, `pulse-target` and
`pulse-maintenance`, plus the migration job (`alembic upgrade head`). They share most of their code
(`core/`: config, DB models, queue client, URL safety, metrics, logging) and the same Python
dependencies.

Every image has a cost: a build, a Trivy scan, an SBOM, ECR storage, a tag to track per environment,
and a version to promote.

## Options considered

1. **One image, command selects the process**
   - Pros: one build, one scan, one SBOM, one tag per commit; impossible for the API and worker to run
     different versions of shared code by accident; simple promotion (one value per environment).
   - Cons: every process carries every dependency (for example the worker image includes Jinja2
     templates it never uses); a vulnerability in any dependency affects all processes; cannot
     release one component without the others.
2. **One image per process**
   - Pros: smaller images; independent release cadence; least-privilege contents.
   - Cons: five builds, scans and tags; promotion must keep them consistent; shared-code changes
     need coordinated releases.
3. **Base image plus thin per-process images**
   - Pros: shared layers, smaller differences.
   - Cons: still several tags to promote; more Dockerfile complexity for little gain at this size.

## Decision

Build one image from a multi-stage Dockerfile (non-root uid 10001, base pinned by digest). Console
scripts `pulse-api`, `pulse-scheduler`, `pulse-worker`, `pulse-target`, `pulse-maintenance` are on
`PATH`; the Helm chart sets the container `command` per Deployment, Job and CronJob.

## Consequences

- Easier: CI builds and scans once; `envs/<env>/values.yaml` has a single `image.tag`; rollbacks
  revert one value.
- Harder: the image is larger than any single process needs; a dependency change rebuilds and
  redeploys everything; resource tuning is per Deployment, not per image.
- All processes must read only their own settings and tolerate unused ones (pydantic-settings
  defaults).
- At larger scale, or if components get separate owners or very different dependencies (for example
  a browser-based check runner), split the image.
