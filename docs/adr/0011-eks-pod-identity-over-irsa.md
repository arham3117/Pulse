# 0011: EKS Pod Identity instead of IRSA for workload AWS permissions

- Status: Proposed (draft — rewrite in your own words before marking Accepted)
- Date: 2026-11-22

## Context

Several pods need AWS permissions: the scheduler and workers (SQS), External Secrets Operator
(Secrets Manager), KEDA (SQS attributes), the AWS Load Balancer Controller, the EBS CSI driver and
Cluster Autoscaler. Giving the node instance role these permissions would grant them to every pod on
the node, so each workload gets its own IAM role. The node launch template enforces IMDSv2 with hop
limit 1 so pods cannot fall back to node credentials.

EKS offers two ways to map a Kubernetes service account to an IAM role.

## Options considered

1. **EKS Pod Identity**
   - How: the `eks-pod-identity-agent` add-on runs on each node; an association
     (`cluster + namespace + service account → role`) is created through the EKS API; the role trusts
     the service principal `pods.eks.amazonaws.com`. SDKs fetch credentials from the agent via the
     container credentials provider.
   - Pros: no per-cluster OIDC provider; role trust policies do not embed cluster-specific OIDC URLs,
     so a role can be reused across clusters and survives cluster rebuilds; associations are plain
     Terraform resources (`aws_eks_pod_identity_association`); session tags (cluster, namespace,
     service account) enable attribute-based access control; no service account annotations.
   - Cons: needs the agent add-on; requires SDK versions that support the container credentials
     provider; not available for Fargate pods; some add-on documentation still only describes IRSA.
2. **IAM Roles for Service Accounts (IRSA)**
   - How: an IAM OIDC provider for the cluster; role trust policy conditions on the OIDC `sub`
     (`system:serviceaccount:<ns>:<sa>`); service account annotated with the role ARN.
   - Pros: mature, works on Fargate, documented by every add-on.
   - Cons: one OIDC provider per cluster; trust policies embed the cluster's OIDC issuer URL, so a
     rebuilt cluster (dev is rebuilt often) needs updated trust policies; annotations must match.
3. **Node instance role**
   - Rejected: every pod on the node gets every permission.

## Decision

Use EKS Pod Identity for every workload role listed in [CONTRACT.md](../CONTRACT.md):
`pulse/pulse-scheduler`, `pulse/pulse-worker`, `external-secrets/external-secrets`,
`keda/keda-operator`, `kube-system/aws-load-balancer-controller`, `kube-system/ebs-csi-controller-sa`,
`kube-system/cluster-autoscaler`. `pulse-api` has no AWS role.

If an add-on's documentation only covers IRSA and it does not work with Pod Identity, use IRSA for
that add-on alone and record it in the table below.

### Per-add-on check (fill in at build time)

| Add-on | Pod Identity documented? | Notes / what to verify | Mechanism used |
|---|---|---|---|
| EBS CSI driver (EKS add-on) | TODO: verify | EKS managed add-on accepts a Pod Identity association | TODO |
| AWS Load Balancer Controller | TODO: verify | Chart docs focus on the IRSA service account annotation; uses the AWS SDK default credential chain | TODO |
| External Secrets Operator | TODO: verify | With Pod Identity, the `SecretStore`/`ClusterSecretStore` must omit the `auth.jwt` (IRSA) block so the controller's own credentials are used | TODO |
| KEDA | TODO: verify | `TriggerAuthentication` `podIdentity.provider`: the older `aws-eks` value is IRSA-specific; check the current docs for the `aws` provider and `identityOwner` | TODO |
| Cluster Autoscaler | TODO: verify | AWS provider docs have historically described IRSA or node-role setups | TODO |

## Consequences

- Easier: dev cluster rebuilds do not invalidate role trust policies; associations live in Terraform
  next to the roles; least privilege per service account.
- Harder: credentials depend on the Pod Identity agent DaemonSet being healthy; the agent listens on
  link-local `169.254.170.23`, which the SSRF validator must (and does) block for user URLs.
- With ESO using controller credentials, any `SecretStore` that can use them can read every secret
  the ESO role allows (`pulse/<env>/*` and the RDS secret), so restrict who can create ExternalSecrets.
- Mixed IRSA and Pod Identity, if needed for one add-on, means also creating an OIDC provider; record
  the reason here.
