# Interview preparation

Model-answer outlines for the 16 questions in the plan, grounded in this design. They are notes, not
scripts: rewrite them in your own words, replace anything you did not build or measure, and add real
numbers from your drills, load tests and cost data once you have them. Aim for 1–2 minutes per answer.

## 1. Walk me through what happens from `git push` to code running in production.

- PR to `pulse`: `app-ci.yml` runs lint (ruff), tests (pytest with Postgres + ElasticMQ service
  containers), build, Trivy scan (fails on fixable HIGH/CRITICAL), SARIF upload, SBOM.
- Merge to `main`: `push` job assumes `pulse-ci-ecr-push` via GitHub OIDC, pushes `sha-<7>` to ECR
  (immutable tags, never `latest`).
- `promote-dev` mints a GitHub App token for `pulse-gitops`, updates `envs/dev/values.yaml` with `yq`,
  commits `chore(dev): deploy sha-xxxxxxx`.
- Argo CD in the cluster sees the commit, runs the PreSync migration Job, then rolls Deployments
  (`maxUnavailable: 0`, readiness on `/readyz`).
- Prod: manual `promote-prod.yml` verifies the tag exists in ECR without unresolved CRITICAL findings,
  waits for approval on the `prod` environment, writes the same tag to `envs/prod/values.yaml`.
- CI never touches the cluster. Same image dev → prod (ADR 0008).

## 2. Why SQS and not Redis or Kafka?

- Work queue semantics: per-message acknowledgement, visibility timeout redelivery, built-in DLQ
  with `maxReceiveCount`. Kafka is a log, not a work queue; Redis needs persistence/HA and DLQ logic
  built by hand.
- Nothing to operate, which matters for one person and for an environment torn down daily.
- KEDA has an SQS scaler; ElasticMQ gives a free local equivalent.
- Trade-off: at-least-once delivery and approximate counts, so the worker must be idempotent
  (ADR 0001).

## 3. How do you stop two scheduler replicas from enqueuing the same check?

- `SELECT ... FOR UPDATE SKIP LOCKED` inside a transaction: each replica locks the due rows it gets,
  the other skips locked rows instead of waiting. After commit `next_check_at` is in the future, so
  the rows no longer qualify.
- Failure case: SQS send succeeds, commit fails → rows unchanged → next loop resends the same
  `(monitor_id, scheduled_for)`. `UNIQUE (monitor_id, scheduled_for)` + `ON CONFLICT DO NOTHING`
  makes the duplicate a no-op (no state change, no notification).
- No leader election; both replicas do work; integration test runs two schedulers and asserts no
  duplicates (ADR 0002).

## 4. What happens if a worker crashes halfway through a check?

- The message is deleted only after the DB commit. If the worker dies before that, SQS makes it
  visible again after the 60 s visibility timeout and another worker processes it.
- If it died after commit but before delete, the redelivery hits the unique constraint and is
  skipped.
- After 3 failed receives the message goes to the DLQ (`PulseDLQNotEmpty`).
- Known gap: crash after commit but before the webhook call loses that notification; the fix is a
  transactional outbox (stretch goal).
- SIGTERM handling: stop receiving, wait up to 25 s for in-flight checks, within the 30 s grace period.

## 5. How does CI authenticate to AWS without access keys?

- GitHub's OIDC provider issues a signed token per job (`id-token: write`).
- `aws-actions/configure-aws-credentials` exchanges it via `sts:AssumeRoleWithWebIdentity`.
- Role trust policy checks `aud = sts.amazonaws.com` and a narrow `sub`: for example
  `repo:arham3117/Pulse:ref:refs/heads/main` for ECR push,
  `repo:arham3117/Pulse:environment:prod` for apply. Never a wildcard repo.
- Credentials are short-lived; nothing to leak or rotate. Separate roles for push, plan, apply.

## 6. Why pin GitHub Actions to commit SHAs?

- A tag is a movable pointer; a full SHA is immutable content.
- March 2026: attackers compromised `aquasecurity/trivy-action` and force-pushed almost all version
  tags to credential-stealing code. Pipelines using tags ran it; SHA-pinned pipelines did not. Initial
  access reportedly came from a misconfigured `pull_request_target` workflow.
- Pulse: every action pinned to a 40-char SHA with a version comment, Dependabot updates them,
  `permissions: {}` by default, no `pull_request_target`, untrusted input via env vars, actionlint.

## 7. How do your pods get AWS permissions, and why not node instance roles?

- EKS Pod Identity: one IAM role per service account (`pulse/pulse-worker`, `pulse/pulse-scheduler`,
  ESO, KEDA, LB controller, EBS CSI, Cluster Autoscaler), associations in Terraform (ADR 0011).
- Node role permissions would be shared by every pod on the node: a compromised `pulse-api` could read
  SQS or secrets. `pulse-api` has no AWS role at all.
- IMDSv2 with hop limit 1 stops pods from reaching node credentials as a fallback.
- Why Pod Identity over IRSA: no per-cluster OIDC provider, trust policies survive dev rebuilds.

## 8. What is SSRF and how did you prevent it in Pulse?

- Server-side request forgery: tricking a server into making requests to places the attacker cannot
  reach, like the metadata service or internal services. Pulse's feature is fetching arbitrary URLs
  from inside a VPC, so it is the main threat.
- Validate at creation (400) and at check time: http/https only, resolve all A/AAAA records, reject
  if any is private, loopback, link-local (169.254/16), CGNAT, multicast, reserved, ULA, IPv6
  link-local.
- Manual redirects, max 3, each hop validated. Allow-list only for `pulse-target`.
- Residual: DNS rebinding between resolve and connect. Fixes: pin the resolved IP, or an egress
  proxy. Defence in depth: IMDSv2 hop limit 1 (ADR 0007).

## 9. What's the difference between black-box and white-box monitoring? Which does Pulse do?

- Black-box: test from outside like a user, no knowledge of internals; detects symptoms. Pulse's
  product is black-box monitoring of users' URLs.
- White-box: metrics, logs and internals the system exposes; explains causes and warns early. Pulse's
  platform layer (Prometheus, Alertmanager, Grafana, Loki) is white-box monitoring of Pulse itself.
- Both: plus an external black-box check of Pulse's own status page, because in-cluster monitoring
  shares fate with the cluster ("who monitors the monitor").

## 10. What are your SLOs, and why alert on burn rate instead of raw error rate?

- API availability 99.5% (non-5xx) and check freshness 99% (checks within 90 s of schedule), 30-day
  rolling. 99.5% over 30 days = 3 h 36 m of budget.
- Burn rate = error ratio / budget. Fast burn 14.4× over 1 h and 5 m pages (2% of budget gone in an
  hour); slow burn 6× over 6 h and 30 m warns (5% in six hours).
- Raw thresholds are arbitrary and either noisy or slow. Burn rate ties alerts to user impact and
  budget; the short window makes alerts resolve quickly once the problem stops.
- Caveats: app-side availability misses ALB-generated 503s; freshness misses checks that never
  ran.

## 11. How do you roll back a bad deployment? How long does it take?

- `git revert` the deploy commit in `pulse-gitops`; Argo CD syncs the previous tag. `kubectl rollout
  undo` would be reverted by self-heal, so git is the only rollback path.
- `maxUnavailable: 0` plus readiness probes mean a broken version usually never takes traffic
  (drill 2).
- Time: TODO, measure in drill 2. Dominated by Argo CD's polling interval unless refreshed manually or
  a webhook is configured, plus rollout time.
- Migrations are expand/contract so the old version still works against the new schema.

## 12. Tell me about an incident you handled.

- TODO: pick one postmortem from `docs/postmortems/` after running the drills.
- Structure: what happened, how it was detected and how fast, what you did, root cause, what you
  changed afterwards (link the PR). Be specific with timestamps and numbers from the postmortem.

## 13. How did you keep AWS costs under control?

- Phases 1–2 and Helm chart testing were local (docker compose, kind): $0.
- Dev only runs while working: `make infra-up` / `make infra-down`, about 25 minutes to rebuild.
  Pre-destroy cleanup removes controller-created ALBs and EBS volumes so nothing is left behind.
- Dev: single NAT, spot nodes, smallest RDS class, short retention. Prod only for demos.
- Infra apply is manual only, so CI cannot recreate a destroyed environment.
- Budget alerts, `Project` cost allocation tag, weekly Cost Explorer check. Numbers: TODO from
  `docs/cost.md`.

## 14. What breaks first at 100× the load, and what would you change?

- Database first: `check_results` insert rate and size; stats queries over large ranges; connection
  count across many pods. Changes: daily partitions (drop instead of delete), batched inserts,
  pre-aggregated stats, read replica for the status page, PgBouncer/RDS Proxy.
- Scheduler: single query per loop over all monitors; shard by hash across replicas.
- Workers: noisy neighbours; separate queues by priority or timeout class; more concurrency per pod.
- Egress: NAT throughput and cost; multi-region check pools.
- Validate with the load-test bottleneck findings (TODO: your measured first bottleneck).

## 15. Why GitOps? What does Argo CD give you over `kubectl apply` in CI?

- Desired state in git: every change is reviewed, auditable, revertible.
- Pull model: CI never needs cluster credentials or network access to the API endpoint (which is
  restricted to my IP).
- Continuous reconciliation: drift is detected and self-healed, not just applied once.
- Health and sync status per resource; sync waves for ordering (CRDs and operators before apps);
  PreSync hooks for migrations; app-of-apps for platform add-ons.
- Rebuilding a cluster is `make argocd-bootstrap`: everything else follows from git.

## 16. Why does the checks queue keep messages for only 1 hour?

- A check is only useful near its scheduled time. An hour-old check says nothing about now, and the
  scheduler has already enqueued newer checks for the same monitor.
- After a long worker outage, short retention prevents a flood of useless stale checks when workers
  return, which would delay the fresh ones even more.
- Trade-off: expired checks disappear silently, so freshness SLI and alerts must catch "nothing ran"
  separately (`PulseChecksStalled`, enqueued vs executed). The DLQ keeps 4 days because those messages
  are evidence to investigate.
