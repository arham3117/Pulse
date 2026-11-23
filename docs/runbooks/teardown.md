# Teardown

Dev runs only while you are working. End every session with:

```sh
make infra-down ENV=dev
```

Then verify nothing billable is left (checklist below). Prod is brought up only for demos and is torn
down the same way.

## Why a plain `terraform destroy` is not enough

Some AWS resources are created by controllers running inside the cluster, not by Terraform, so they
are **not in Terraform state**:

| Resource | Created by | Why it matters |
|---|---|---|
| Application Load Balancer, its target groups and security groups | AWS Load Balancer Controller (from the `pulse-api` Ingress) | Billed hourly; its ENIs and security groups block VPC/subnet deletion |
| EBS volumes | EBS CSI driver (from PVCs: Prometheus, Loki, Grafana if persistent) | Billed per GB-month while they exist, attached or not |
| ENIs | ALB, VPC CNI | Block subnet and security group deletion |

If these still exist, `terraform destroy` fails partway (typically on subnets, security groups or the
VPC with a `DependencyViolation`) and they keep costing money.

## What `make infra-down ENV=<env>` does

1. Runs `scripts/pre-destroy-cleanup.sh <env>` (read the script for the exact steps):
   1. Deletes the Argo CD root Application with cascade, so Argo CD stops recreating resources
      (`selfHeal`) and deletes what it manages.
   2. Deletes all Ingresses and any `Service` of type `LoadBalancer`. Ordering matters: the AWS Load
      Balancer Controller must still be running to process the Ingress finalizer and delete the ALB.
      If the controller is removed first, the Ingress hangs on its finalizer and the ALB is orphaned.
   3. Waits until no load balancer tagged for this cluster remains
      (`aws elbv2 describe-load-balancers` + `describe-tags`).
   4. Deletes PVCs and waits for the EBS volumes to disappear. PVCs created from StatefulSet
      `volumeClaimTemplates` are not deleted with the StatefulSet, so they must be deleted
      explicitly. With reclaim policy `Delete`, deleting the PVC deletes the EBS volume.
2. Runs `terraform destroy` for `infra/envs/<env>`. Review the plan before confirming.

What remains on purpose (created by `infra/bootstrap`, small storage or no cost): the state bucket
`pulse-tfstate-ACCOUNT_ID`, the ECR repository `pulse` and its images, the GitHub OIDC provider, the CI
roles, and the budget.

## Prod specifics

- `deletion_protection = true` on the prod RDS instance: destroy fails until it is turned off with a
  reviewed `terraform apply` (change the variable, apply, then destroy).
- `skip_final_snapshot = false` in prod: a final snapshot is created and **is billed for storage**
  until you delete it.

## Verification checklist (after the first teardown, and whenever destroy had errors)

Run these with the same profile and region. Every command should return nothing for this project
(or only resources you know about).

```sh
export AWS_PROFILE=pulse AWS_REGION=us-east-2 ENV=dev
```

| Check | Command | Console location |
|---|---|---|
| EKS cluster gone | `aws eks list-clusters` | EKS → Clusters |
| Load balancers | `aws elbv2 describe-load-balancers --query 'LoadBalancers[].[LoadBalancerName,VpcId,State.Code]' --output table` | EC2 → Load Balancers |
| Target groups (no cost, but leftovers) | `aws elbv2 describe-target-groups --query 'TargetGroups[].TargetGroupName'` | EC2 → Target Groups |
| EBS volumes | `aws ec2 describe-volumes --query 'Volumes[].[VolumeId,State,Size,CreateTime]' --output table` (look especially for `available`, meaning unattached) | EC2 → Volumes |
| Elastic IPs | `aws ec2 describe-addresses --query 'Addresses[].[PublicIp,AllocationId,AssociationId]' --output table` | EC2 → Elastic IPs |
| NAT gateways | `aws ec2 describe-nat-gateways --filter Name=state,Values=pending,available --query 'NatGateways[].[NatGatewayId,VpcId,State]' --output table` | VPC → NAT gateways |
| Network interfaces | `aws ec2 describe-network-interfaces --filters Name=tag:Project,Values=pulse --query 'NetworkInterfaces[].[NetworkInterfaceId,Status,Description]' --output table`, and without the filter if a VPC still exists | EC2 → Network Interfaces |
| LB controller security groups | `aws ec2 describe-security-groups --filters Name=tag:elbv2.k8s.aws/cluster,Values=pulse-$ENV --query 'SecurityGroups[].GroupId'` | EC2 → Security Groups |
| VPC gone | `aws ec2 describe-vpcs --filters Name=tag:Project,Values=pulse --query 'Vpcs[].VpcId'` | VPC → Your VPCs |
| EC2 instances | `aws ec2 describe-instances --filters Name=instance-state-name,Values=pending,running,stopping,stopped --query 'Reservations[].Instances[].[InstanceId,InstanceType,State.Name]' --output table` | EC2 → Instances |
| RDS instances | `aws rds describe-db-instances --query 'DBInstances[].[DBInstanceIdentifier,DBInstanceStatus]' --output table` | RDS → Databases |
| RDS snapshots (prod final snapshot) | `aws rds describe-db-snapshots --snapshot-type manual --query 'DBSnapshots[].[DBSnapshotIdentifier,SnapshotCreateTime]' --output table` | RDS → Snapshots |
| SQS queues | `aws sqs list-queues --queue-name-prefix pulse-$ENV` | SQS |
| CloudWatch log groups | `aws logs describe-log-groups --log-group-name-prefix /aws/eks/pulse-$ENV --query 'logGroups[].logGroupName'`; also `/aws/rds/instance/pulse-$ENV-db` and VPC flow log groups | CloudWatch → Log groups |
| KMS key for EKS secrets | `aws kms list-aliases --query "Aliases[?contains(AliasName, 'pulse')]"` then `aws kms describe-key --key-id <id> --query KeyMetadata.KeyState` (expect `PendingDeletion`) | KMS → Customer managed keys |
| Secrets | `aws secretsmanager list-secrets --include-planned-deletion --query 'SecretList[].[Name,DeletedDate]' --output table` | Secrets Manager |

Log groups created by AWS services at runtime (for example RDS log exports, or EKS control plane logs
written after Terraform deleted the group) are not in Terraform state and can survive a destroy.

If something is left:

- Load balancer or volume left over: delete it in the console or with the CLI, then re-run
  `terraform destroy`.
- Destroy stuck on a security group: find what still uses it
  (`aws ec2 describe-network-interfaces --filters Name=group-id,Values=<sg-id>`).

## Secrets Manager recovery window

When Terraform destroys an `aws_secretsmanager_secret`, AWS schedules it for deletion with a recovery
window (30 days by default) instead of deleting it immediately. During that window the **name is still
taken**, so the next `make infra-up` fails with an error saying a secret with that name is already
scheduled for deletion.

Options:

- Set `recovery_window_in_days = 0` for dev secrets in Terraform (immediate deletion).
- Or restore it before the next apply and import it:
  `aws secretsmanager restore-secret --secret-id pulse/dev/app`.
- Or force-delete it now (permanent, cannot be undone):
  `aws secretsmanager delete-secret --secret-id pulse/dev/app --force-delete-without-recovery`.

Either way, after a rebuild the secrets may be empty: follow [set-secrets.md](set-secrets.md).

## Record

TODO: after the first full teardown, note here what (if anything) was left behind, how it was found,
and the fix made to `scripts/pre-destroy-cleanup.sh`. Also record the measured time for
`make infra-up` + `make argocd-bootstrap` and for `make infra-down`.

| Run date | infra-up + bootstrap time | infra-down time | Leftovers found | Fix |
|---|---|---|---|---|
| TODO | TODO | TODO | TODO | TODO |
