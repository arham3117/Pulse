# 0004: Terraform owns AWS resources; Argo CD owns everything inside Kubernetes

- Status: Proposed (draft — rewrite in your own words before marking Accepted)
- Date: 2026-11-22

## Context

The platform has two kinds of resources:

- AWS resources: VPC, EKS, node groups, RDS, SQS, IAM roles, Pod Identity associations,
  Secrets Manager secrets, ECR.
- Kubernetes resources: add-ons (load balancer controller, KEDA, External Secrets, monitoring stack)
  and the Pulse workloads.

Terraform can manage Kubernetes objects through the `kubernetes` and `helm` providers. Doing so in
the same root module that creates the cluster causes known problems: the providers need the cluster
endpoint and credentials at plan time, before the cluster exists (chicken-and-egg); destroy ordering
is fragile; and drift made by controllers inside the cluster shows up as noisy plans.

## Options considered

1. **Terraform for everything** (including Helm releases)
   - Pros: one tool, one state.
   - Cons: provider bootstrap problems above; deploying an app version means `terraform apply`;
     CI needs cluster credentials; no continuous reconciliation.
2. **Terraform for AWS, Argo CD for Kubernetes**
   - Pros: clear boundary; Argo CD continuously reconciles and self-heals; every in-cluster change is
     a git commit in `pulse-gitops`; CI does not need cluster access.
   - Cons: two sources of truth that must agree on names (service accounts, queue URLs, secret
     names); a hand-off step is needed.
3. **Terraform for AWS, `helm install` from CI**
   - Pros: familiar push model.
   - Cons: CI needs cluster credentials and network access to the API endpoint; no drift correction.

## Decision

Terraform manages AWS resources only, including the IAM side of Kubernetes integration (Pod Identity
associations and EKS access entries). Argo CD manages every Kubernetes object, installed via
app-of-apps from `pulse-gitops/argocd/<env>/`. The only manual Kubernetes step is
`make argocd-bootstrap ENV=<env>`, which installs Argo CD and the root Application.

The hand-off is `scripts/render-infra-values.sh`, which turns `terraform output -json` into
`pulse-gitops/envs/<env>/infra-values.yaml` (queue URLs, RDS endpoint, secret names; no secret
values). Names both sides depend on are pinned in [CONTRACT.md](../CONTRACT.md).

## Consequences

- Easier: `terraform apply` never waits on Kubernetes; Argo CD shows sync and health for every
  component; rollbacks are `git revert`.
- Harder: Pod Identity associations reference `namespace/serviceaccount` names that only exist once
  Argo CD syncs; a typo on either side fails silently until a pod tries to call AWS.
- Controllers create AWS resources that are **not in Terraform state**: the ALB and its security
  groups (AWS Load Balancer Controller) and EBS volumes (EBS CSI driver). `terraform destroy` fails on
  the VPC and leaves billable resources unless they are removed first, which is why
  `make infra-down` runs `scripts/pre-destroy-cleanup.sh` before destroying
  (see [runbooks/teardown.md](../runbooks/teardown.md)).
- Infra values must be re-rendered and committed after any Terraform change to outputs.
- At larger scale: consider generating the hand-off automatically (for example Crossplane or ACK
  inside the cluster, or a CI job that opens a PR to `pulse-gitops`).
