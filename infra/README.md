# Pulse infrastructure (Terraform)

Terraform owns everything in AWS; Argo CD owns everything inside Kubernetes (ADR 0004).
Shared names (cluster, queues, secrets, outputs, service accounts) are fixed by
[`docs/CONTRACT.md`](../docs/CONTRACT.md).

## Layout

```
infra/
├── .tflint.hcl            # tflint + AWS ruleset (pinned)
├── bootstrap/             # applied once by hand: state bucket, GitHub OIDC, CI roles, ECR, budget
├── modules/
│   ├── network/           # VPC 10.20.0.0/16, public/private/database subnets, NAT, S3 endpoint, flow logs
│   ├── eks/               # EKS cluster, access entries, add-ons, KMS, managed node group (IMDSv2, gp3)
│   ├── rds/               # PostgreSQL, RDS-managed master password, force_ssl, SG 5432 from nodes only
│   ├── sqs/               # pulse-<env>-checks + DLQ (1h / 4d retention, 60s visibility, 3 receives)
│   ├── workload-iam/      # one IAM role per service account + EKS Pod Identity associations
│   └── secrets/           # pulse/<env>/{app,grafana,alertmanager} containers, no values
└── envs/
    ├── dev/               # root module + dev.tfvars  (single NAT, spot, db.t4g.micro, disposable)
    └── prod/              # root module + prod.tfvars (NAT per AZ, on-demand, Multi-AZ, protected)
```

`envs/dev` and `envs/prod` contain identical `.tf` files (only `backend.tf` and the
tfvars differ). Change both together.

## Pinned versions

| Component | Version | Source |
|---|---|---|
| Terraform | `>= 1.11` (S3 `use_lockfile`) | |
| hashicorp/aws | 6.73.0 | registry.terraform.io/v1/providers/hashicorp/aws |
| terraform-aws-modules/vpc | 6.8.0 | registry.terraform.io/v1/modules/terraform-aws-modules/vpc/aws |
| terraform-aws-modules/eks | 21.30.2 | registry.terraform.io/v1/modules/terraform-aws-modules/eks/aws |
| terraform-aws-modules/rds | 7.2.2 | registry.terraform.io/v1/modules/terraform-aws-modules/rds/aws |
| Kubernetes (EKS) | 1.37 | EKS user guide, "Available versions on standard support" |
| PostgreSQL (RDS) | 18 (minor auto-upgraded) | RDS for PostgreSQL release calendar |
| AWS LB Controller IAM policy | v3.6.3 | `modules/workload-iam/policies/` (see its README) |
| tflint-ruleset-aws | 0.50.0 | github.com/terraform-linters/tflint-ruleset-aws releases |

## Placeholders to replace before the first apply

| Placeholder | File(s) |
|---|---|
| `ACCOUNT_ID` | `envs/*/backend.tf` (bucket `pulse-tfstate-ACCOUNT_ID`), commented block in `bootstrap/versions.tf` |
| `YOUR_IP/32` | `envs/*/*.tfvars` `admin_cidrs` (plan fails until it is a valid CIDR) |
| `YOUR_NAME` | `envs/*/*.tfvars` `owner`, `bootstrap` var `owner` |
| _(filled in)_ | `bootstrap` vars `github_owner = "arham3117"`, `github_repo = "Pulse"` (OIDC `sub` claims; the repo name is case-sensitive) |
| `us-east-2` | tfvars + backend blocks, if you use another region |

## Apply order

### 1. Bootstrap (once, local state first)

```sh
cd infra/bootstrap
terraform init
terraform apply \
  -var budget_email="you@example.com" \
  -var github_owner="<your GitHub user>" \
  -var owner="<your name>"
# optional: -var monthly_budget_usd=150 -var enable_cost_anomaly_detection=true
```

`budget_email` has no default on purpose (keep it out of git; `TF_VAR_budget_email` works too).
The CI role ARNs in the outputs go into GitHub **repository variables** (they are not secrets).

Then move bootstrap's own state into the bucket it just created so it is not lost with your laptop:

1. In `bootstrap/versions.tf`, uncomment the `backend "s3"` block and replace `ACCOUNT_ID`
   (see output `state_bucket_name`).
2. Run `terraform init -migrate-state` and answer `yes`. State moves to
   `s3://pulse-tfstate-<account-id>/bootstrap/terraform.tfstate`.
3. Delete the local `terraform.tfstate*` files once `terraform plan` shows no changes.

### 2. An environment

```sh
# replace ACCOUNT_ID in envs/dev/backend.tf and YOUR_IP/32 / YOUR_NAME in envs/dev/dev.tfvars
terraform -chdir=infra/envs/dev init
terraform -chdir=infra/envs/dev plan -var-file=dev.tfvars -out=tfplan   # review it
terraform -chdir=infra/envs/dev apply tfplan                            # ~20-25 min
```

(`make infra-plan ENV=dev` / `make infra-up ENV=dev` wrap these.)

### 3. After apply

1. Put the secret values (never through Terraform):
   `aws secretsmanager put-secret-value --secret-id pulse/dev/app --secret-string file://app.json`
   (keys per `docs/CONTRACT.md`; see `docs/runbooks/set-secrets.md`). Dev secrets are deleted
   immediately on destroy (`secret_recovery_window_days = 0`), so repeat this after every rebuild.
2. `aws eks update-kubeconfig --name pulse-dev --region us-east-2` (`make kubeconfig ENV=dev`).
3. `scripts/render-infra-values.sh dev` writes `pulse-gitops/envs/dev/infra-values.yaml`; commit it in pulse-gitops.
4. `scripts/argocd-bootstrap.sh dev`.

### 4. Teardown

```sh
scripts/pre-destroy-cleanup.sh dev    # LBs, Argo CD apps, PVCs/EBS volumes (not in TF state)
terraform -chdir=infra/envs/dev destroy -var-file=dev.tfvars
```

Prod has RDS deletion protection on: set `db_deletion_protection = false`, apply, then destroy.
A final snapshot is kept for prod.

## Design notes and deliberate trade-offs

- **Single NAT in dev** (`single_nat_gateway = true`): one NAT gateway (~$32/month + data) instead
  of one per AZ; an AZ outage breaks egress for the other AZ. Prod uses one per AZ.
- **2 AZs**: enough for RDS Multi-AZ and node spread. Prod could use 3 (`az_count = 3`) for more
  headroom at the cost of another NAT gateway.
- **Subnets**: public `/24` (ALB, NAT), private `/20` (nodes and pods; VPC CNI prefix delegation
  hands out `/28`s), database `/24` with no route to the internet.
- **EKS API**: public endpoint only from `admin_cidrs` plus private endpoint. CI never talks to
  the cluster (Argo CD pulls), so no CI CIDRs are needed.
- **Access**: EKS access entries (`authentication_mode = "API"`), no `aws-auth` ConfigMap. The
  identity that applies gets cluster-admin; add your SSO admin role via `admin_principal_arns`.
- **Nodes**: IMDSv2 required with hop limit 1, so pods cannot read the node role's credentials;
  every AWS-calling workload uses Pod Identity (`modules/workload-iam`). Side effect: in-cluster
  controllers cannot discover region/VPC from IMDS, which is why `vpcId` and `awsRegion` are in
  `infra-values.yaml`.
- **IRSA disabled** (`enable_irsa = false`): every add-on used supports Pod Identity.
- **EBS CSI** add-on sets `k8sTagClusterId`, so dynamically provisioned volumes carry
  `kubernetes.io/cluster/pulse-<env>=owned`; `scripts/pre-destroy-cleanup.sh` waits on that tag.
- **CI roles** trust exact `sub` claims (`StringEquals`, no wildcards). `pulse-ci-tf-apply` starts
  with PowerUserAccess plus IAM writes limited to `pulse-*` roles/policies; tighten in Phase 7.
- **Checkov**: deliberate trade-offs carry inline `#checkov:skip=<id>:<reason>` comments
  (state bucket logging/replication, SSE-S3 vs CMK, RDS dev settings, vendored LBC policy).

## Quality gates

```sh
terraform fmt -check -recursive infra
for d in infra/bootstrap infra/envs/dev infra/envs/prod; do
  terraform -chdir="$d" init -backend=false && terraform -chdir="$d" validate
done
tflint --init --config "$PWD/infra/.tflint.hcl"
tflint --recursive --chdir infra --config "$PWD/infra/.tflint.hcl"
checkov -d infra
```

Commit each root's `.terraform.lock.hcl`; never commit `.terraform/` or `*.tfstate`.
