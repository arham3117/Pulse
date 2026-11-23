# Architecture decision records

Each ADR records one decision: the context, the options, what was chosen, and the consequences.
Format: [Appendix B of the plan](../../plan/plan.md#appendix-b-adr-template-docsadrnnnn-titlemd).

All ADRs below are **drafts** (status `Proposed`). The owner rewrites each one in their own words,
checks it against what was actually built, and then changes the status to `Accepted`. An ADR is never
edited to reverse a decision: write a new ADR and mark the old one `Superseded by NNNN`.

| ADR | Decision | Status |
|---|---|---|
| [0001](0001-sqs-for-check-queue.md) | SQS instead of Redis/RabbitMQ for the checks queue | Proposed |
| [0002](0002-scheduler-select-for-update-skip-locked.md) | Scheduler replicas coordinate with `SELECT ... FOR UPDATE SKIP LOCKED` | Proposed |
| [0003](0003-one-image-multiple-entrypoints.md) | One container image, multiple entrypoints | Proposed |
| [0004](0004-terraform-owns-aws-argocd-owns-kubernetes.md) | Terraform owns AWS; Argo CD owns everything inside Kubernetes | Proposed |
| [0005](0005-separate-gitops-repo-updated-by-ci.md) | Separate `pulse-gitops` repo updated by CI with a GitHub App token | Proposed |
| [0006](0006-eks-managed-node-groups.md) | EKS managed node groups (not Fargate or Auto Mode) | Proposed |
| [0007](0007-ssrf-protection-for-user-urls.md) | SSRF protection on user-supplied URLs | Proposed |
| [0008](0008-build-once-promote-same-image.md) | Build once, promote the same image tag dev → prod | Proposed |
| [0009](0009-single-nat-gateway-in-dev.md) | Single NAT gateway in dev, one per AZ in prod | Proposed |
| [0010](0010-no-cpu-limits.md) | CPU requests and memory limits, no CPU limits | Proposed |
| [0011](0011-eks-pod-identity-over-irsa.md) | EKS Pod Identity instead of IRSA | Proposed |

## Writing a new ADR

1. Copy the template from the plan into `NNNN-kebab-title.md` with the next number.
2. Keep it short: context, 2–4 real options with pros and cons, a one- or two-sentence decision,
   and honest consequences (including what gets harder).
3. Add a row to the table above.
