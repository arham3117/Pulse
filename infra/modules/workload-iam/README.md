# workload-iam

One IAM role per Kubernetes service account, bound with EKS Pod Identity.
The service-account list is fixed by `docs/CONTRACT.md`.

| Service account | Role | Association created by |
|---|---|---|
| `pulse/pulse-scheduler` | `<env>-scheduler` | this module |
| `pulse/pulse-worker` | `<env>-worker` | this module |
| `external-secrets/external-secrets` | `<env>-external-secrets` | this module |
| `keda/keda-operator` | `<env>-keda-operator` | this module |
| `kube-system/aws-load-balancer-controller` | `<env>-aws-load-balancer-controller` | this module |
| `kube-system/ebs-csi-controller-sa` | `<env>-ebs-csi-driver` | `aws-ebs-csi-driver` EKS add-on (`modules/eks`) |
| `kube-system/cluster-autoscaler` | `<env>-cluster-autoscaler` | this module |

## Vendored AWS Load Balancer Controller policy

`policies/aws-load-balancer-controller-v3.6.3.json` is the unmodified
`docs/install/iam_policy.json` from
[kubernetes-sigs/aws-load-balancer-controller](https://github.com/kubernetes-sigs/aws-load-balancer-controller)
at tag **v3.6.3**. When you bump the controller's Helm chart, fetch the policy for
the matching tag, rename the file, and update the reference in `main.tf`:

```sh
curl -fsSL -o policies/aws-load-balancer-controller-vX.Y.Z.json \
  https://raw.githubusercontent.com/kubernetes-sigs/aws-load-balancer-controller/vX.Y.Z/docs/install/iam_policy.json
```
