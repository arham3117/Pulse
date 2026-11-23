# 0008: Build once, promote the same image tag from dev to prod

- Status: Proposed (draft — rewrite in your own words before marking Accepted)
- Date: 2026-11-22

## Context

Pulse has two environments, dev and prod, in one AWS account. If prod gets a different build from
dev (rebuilt from the same commit, or built from a different branch), then what was tested is not
what ships: dependency resolution, base image updates or build tooling can differ between builds.

## Options considered

1. **Build once per commit, promote the same tag**
   - Pros: the prod artifact is byte-for-byte the one that ran in dev; one scan result per artifact;
     promotion is a config change.
   - Cons: images must be environment-agnostic; ECR must be reachable from every environment.
2. **Rebuild per environment** (for example on a release branch or tag)
   - Pros: simple pipelines per branch.
   - Cons: two different artifacts; a passing dev test proves little about the prod build.
3. **Branch per environment in the config repo**
   - Pros: common in older GitOps setups.
   - Cons: merge drift between branches; harder to see what differs between environments.

## Decision

CI builds one image per commit on `main` and pushes it to the shared ECR repository `pulse` as
`sha-<7-char sha>`. ECR tags are immutable and `latest` is never used. `promote-dev` writes that tag to
`pulse-gitops/envs/dev/values.yaml`. Promotion to prod is the manual `promote-prod.yml` workflow: it
verifies the tag exists in ECR and has no unresolved CRITICAL findings, waits for approval on the
`prod` environment, then writes the same tag to `envs/prod/values.yaml`.

Environment differences live only in values files (`values.yaml`, `infra-values.yaml`) and secrets.

## Consequences

- Easier: "what's in prod" is one tag in git; rollback is reverting that tag; scan results and SBOM
  apply to exactly what runs.
- Harder: nothing environment-specific can be baked into the image; database migrations must be
  backward compatible (expand/contract) because the same image runs against each environment's schema
  during rollout.
- Immutable tags mean a re-run of CI for the same commit cannot overwrite an image; the pipeline must
  tolerate "tag already exists".
- A tag is a pointer; pinning by digest (`image@sha256:...`) in values would make the guarantee
  cryptographic. Image signing with cosign is a stretch goal.
- With separate AWS accounts per environment, this needs cross-account ECR pull permissions or
  replication.
