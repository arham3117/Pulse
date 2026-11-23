# Security

This document covers Pulse's threat model, the control for each threat, and the evidence that each
control works. The evidence section is filled in from real command output and screenshots captured
during Phase 7; nothing in it is pre-filled.

## Assets and trust boundaries

| Asset | Where it lives | Who should reach it |
|---|---|---|
| AWS credentials (node role, workload roles, CI roles) | IMDS, Pod Identity agent, GitHub OIDC | Only the intended workload or workflow |
| Database (monitors, results, incidents) | RDS in database subnets | API, scheduler, worker, maintenance pods |
| Secrets (admin API key, webhook URLs, Grafana password, DB password) | Secrets Manager → Kubernetes Secrets via ESO | The consuming pods |
| Container images | ECR `pulse` | CI push role; nodes pull |
| Desired cluster state | `pulse-gitops` repo | CI (GitHub App, contents only), the owner |
| Terraform state | S3 `pulse-tfstate-ACCOUNT_ID` | Owner, CI plan/apply roles |

Trust boundaries: the internet → ALB → API; user-supplied URLs → workers → anything the VPC can reach;
GitHub Actions → AWS (OIDC); GitHub → cluster (Argo CD pull only).

## Threat model

| # | Threat | Example | Control | Residual risk |
|---|---|---|---|---|
| 1 | **SSRF** via user-supplied monitor URLs | Monitor for `http://169.254.169.254/latest/meta-data/iam/` or `http://10.20.x.x:5432` to steal credentials or map the VPC | URL validation at creation (400) and at check time (`blocked`); all A/AAAA records checked against private, loopback, link-local, CGNAT, multicast, reserved, ULA ranges; manual redirect following (max 3), each hop validated; allow-list only for `pulse-target`. See [ADR 0007](adr/0007-ssrf-protection-for-user-urls.md) | DNS rebinding between validation and connect (TOCTOU); timing and error-type side channels |
| 2 | **Instance metadata (IMDS) credential theft** | A pod (via SSRF or compromise) reads the node role's credentials | Launch template: IMDSv2 required, hop limit 1, so pods cannot complete the token exchange; workloads use Pod Identity, not the node role; workers only send GET/HEAD (IMDSv2 token needs PUT) | Host-network pods are not covered by the hop limit |
| 3 | **Secret leakage** | Webhook URL or DB password committed to git, written to Terraform state, logged, or printed in CI | Secrets created in Terraform without values, set by CLI ([set-secrets.md](runbooks/set-secrets.md)); RDS manages its own master password; ESO syncs into Kubernetes; EKS envelope encryption with KMS; logs redact URL query strings; gitleaks in pre-commit; CI never echoes secrets | Kubernetes Secrets are readable by anyone with `get secrets` in the namespace; webhook URLs are bearer credentials |
| 4 | **Supply chain: CI actions** | A third-party action's tag is moved to malicious code | Every action pinned to a full 40-character commit SHA with a version comment; Dependabot updates pins; `permissions: {}` at workflow level, minimal per job; no `pull_request_target`; untrusted input passed via env vars, not interpolated into `run:`; actionlint (and optionally zizmor) | A compromised SHA-pinned dependency is still possible if a malicious commit is pinned during an update; review Dependabot PRs |
| 5 | **Supply chain: dependencies and images** | Vulnerable Python package or base image | `uv.lock` committed; base image pinned by digest; Trivy image scan fails on fixable HIGH/CRITICAL; SARIF in the Security tab; ECR scan on push; CycloneDX SBOM per build; immutable tags | Unfixed vulnerabilities; no image signing yet (stretch: cosign) |
| 6 | **Long-lived cloud credentials** | Leaked AWS access key used to deploy or read state | No static keys: SSO locally, GitHub OIDC in CI with `sub` scoped per repo/branch/environment; Pod Identity for workloads | GitHub App private key (for `pulse-gitops` writes) is long-lived, but can only mint 1-hour tokens for one repo |
| 7 | **Public exposure** | Admin UIs or the database reachable from the internet | Only the ALB → `pulse-api` is public; Argo CD, Grafana, Prometheus via port-forward only; RDS not publicly accessible, SG only from the cluster, SSL enforced; EKS API endpoint restricted to the owner's `/32` + private; `pulse-target` ClusterIP only; write endpoints need `X-API-Key` (constant-time compare) | API read endpoints and status page are public by design; no rate limiting on the ALB (consider AWS WAF) |
| 8 | **Privilege escalation in the cluster** | Compromised pod escapes to the node or gains broader permissions | Namespace `pulse` enforces Pod Security Standard `restricted`; containers non-root (uid 10001), read-only root filesystem, no privilege escalation, all capabilities dropped, `RuntimeDefault` seccomp; one IAM role per service account; `pulse-api` has no AWS role | Platform namespaces (monitoring, kube-system) run privileged DaemonSets by necessity; NetworkPolicies optional |
| 9 | **Over-privileged CI** | `pulse-ci-tf-apply` can do far more than needed | Separate plan (read-only) and apply roles; apply only via manual `workflow_dispatch` with environment approval for prod; tightened with IAM Access Analyzer | Apply role remains broad by nature |
| 10 | **Abuse of the product** | Using Pulse to flood a third-party site | Interval minimum 30 s; admin-only monitor creation; load tests only against owned endpoints | A malicious admin could still point many monitors at one host |

### The March 2026 `trivy-action` compromise

In March 2026, attackers compromised the popular `aquasecurity/trivy-action` GitHub Action and
force-pushed almost all of its version tags to credential-stealing code. Pipelines that referenced the
action by tag (for example `@0.28.0`) ran the malicious code; pipelines pinned to a full commit SHA
were not affected, because a SHA identifies immutable content while a tag is a movable pointer. The
attackers' initial access reportedly came from a misconfigured `pull_request_target` workflow.

What Pulse does as a result:

- Every action is pinned to a full SHA verified against the action's official releases, with a
  version comment for readability; Dependabot proposes updates as reviewable PRs.
- `trivy-action` is used at v0.35.0 or later, pinned by SHA.
- No workflow uses `pull_request_target`.
- Jobs get only the permissions they need, so a compromised step has little to steal: no long-lived
  AWS keys exist, and `id-token: write` is granted only to jobs that assume AWS roles.

## Evidence

Phase 7.1 verifications. For each item, paste the command and its output (or a screenshot saved in
`docs/images/`) and the date it was captured. Redact account IDs, IPs and anything sensitive.

### 1. No static AWS credentials

- [ ] `gitleaks detect` over the full history of `pulse` and `pulse-gitops` finds nothing.
- [ ] CI workflows use `aws-actions/configure-aws-credentials` with `role-to-assume`, no access keys
      in repository secrets.
- [ ] Pods use Pod Identity (`aws eks list-pod-identity-associations --cluster-name pulse-dev`).

```
TODO: paste output/screenshot
```

### 2. IMDS unreachable from pods

- [ ] From inside a pod, a request to `169.254.169.254` times out.

```sh
kubectl -n pulse exec deploy/pulse-worker -- python -c \
  "import urllib.request; urllib.request.urlopen(urllib.request.Request('http://169.254.169.254/latest/api/token', method='PUT', headers={'X-aws-ec2-metadata-token-ttl-seconds': '60'}), timeout=3)"
```

```
TODO: paste output/screenshot
```

### 3. SSRF blocked at creation

- [ ] Each of these returns 400: `http://169.254.169.254/latest/meta-data/`, `http://localhost`,
      `http://10.20.0.10`, and a hostname that resolves to a private IP.

```sh
for u in http://169.254.169.254/latest/meta-data/ http://localhost http://10.20.0.10 http://<private-resolving-host>; do
  printf '%s -> ' "$u"
  curl -s -o /dev/null -w '%{http_code}\n' -X POST "$API/api/v1/monitors" \
    -H "X-API-Key: $PULSE_KEY" -H 'Content-Type: application/json' \
    -d "{\"name\":\"ssrf-test\",\"url\":\"$u\"}"
done
```

```
TODO: paste output/screenshot
```

### 4. RDS locked down

- [ ] `PubliclyAccessible` is false.
- [ ] The security group allows 5432 only from the cluster security group.
- [ ] A non-SSL connection is rejected (`sslmode=disable` fails because `rds.force_ssl = 1`).

```sh
aws rds describe-db-instances --db-instance-identifier pulse-dev-db \
  --query 'DBInstances[0].[PubliclyAccessible,VpcSecurityGroups]'
aws ec2 describe-security-groups --group-ids <rds-sg-id> --query 'SecurityGroups[0].IpPermissions'
```

```
TODO: paste output/screenshot
```

### 5. Pod security

- [ ] Pulse pods run as uid 10001 with a read-only root filesystem.
- [ ] A privileged pod is rejected by the `restricted` Pod Security Standard.

```sh
kubectl -n pulse get pods -o jsonpath='{range .items[*]}{.metadata.name}{"  "}{.spec.securityContext.runAsUser}{"  "}{.spec.containers[0].securityContext.readOnlyRootFilesystem}{"\n"}{end}'
kubectl -n pulse run priv-test --image=busybox:1.36 --restart=Never \
  --overrides='{"spec":{"containers":[{"name":"priv-test","image":"busybox:1.36","securityContext":{"privileged":true}}]}}'
# Expected: Error from server (Forbidden): ... violates PodSecurity "restricted:latest" ...
```

```
TODO: paste output/screenshot
```

### 6. No secrets in git or Terraform state

- [ ] `terraform state pull | grep -iE "password|webhook"` finds nothing sensitive (secret resource
      names and ARNs may match; values must not).

```
TODO: paste output/screenshot
```

### 7. Supply chain

- [ ] No workflow references an action by tag:
      `grep -rnE 'uses: [^ ]+@' .github/workflows | grep -vE '@[0-9a-f]{40}'` returns nothing
      (local `./` actions excepted).
- [ ] Dependabot PRs are arriving for actions, Python, Docker and Terraform.
- [ ] Trivy (Security tab) and ECR scan show no fixable CRITICAL vulnerabilities.
- [ ] An SBOM artifact exists for a recent build.

```
TODO: paste output/screenshot
```

### 8. IAM least privilege

- [ ] Every role reviewed; IAM Access Analyzer unused-access findings checked;
      `pulse-ci-tf-apply` and workload roles tightened. Record what was removed.

```
TODO: paste output/screenshot
```

### 9. Checkov

- [ ] `checkov -d infra/` is clean, or each suppression has an inline justification. List the
      suppressions here.

```
TODO: paste output/screenshot
```

## Known gaps and next steps

- Close the SSRF TOCTOU gap by pinning resolved IPs or adding an egress proxy.
- NetworkPolicies: default-deny ingress in `pulse` and egress restrictions for workers.
- Image signing (cosign) and admission verification; policy as code (Kyverno).
- Separate least-privilege database user for the app instead of the RDS master user.
- Rate limiting / AWS WAF in front of the public ALB.
