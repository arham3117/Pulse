# Cost

This document estimates what Pulse costs to run on AWS and records what it actually cost. Every
number comes from the AWS Pricing Calculator (estimates) or Cost Explorer (actuals). Cells marked
`TODO` are filled in from those tools, never guessed.

Region: `us-east-2`. Prices differ by region and change over time; record the date you looked them
up.

## What is billed while idle

These resources charge **by the hour even when nothing is happening**. They dominate the bill and are
why dev only runs while you are working.

| Resource | Billing unit | Dev config | Prod config | Unit price (date: TODO) | Dev hourly | Prod hourly |
|---|---|---|---|---|---|---|
| EKS control plane | per cluster-hour (higher rate if the Kubernetes version is in extended support) | 1 cluster | 1 cluster | TODO | TODO | TODO |
| NAT gateway | per NAT-hour + per GB processed | 1 (single) | 1 per AZ (2) | TODO | TODO | TODO |
| EC2 worker nodes | per instance-hour (spot price varies) | 2–3 spot (`t3.large` / `t3a.large` / `m5.large`) | on-demand, spread across AZs (TODO: type, count) | TODO | TODO | TODO |
| RDS PostgreSQL | per instance-hour (Multi-AZ roughly doubles instance cost) + storage GB-month | `db.t4g.micro`, single-AZ | TODO class, Multi-AZ | TODO | TODO | TODO |
| Application Load Balancer | per ALB-hour + per LCU-hour | 1 | 1 | TODO | TODO | TODO |
| EBS volumes | per GB-month (gp3), prorated hourly | node root volumes + Prometheus/Loki PVCs (TODO: sizes) | TODO | TODO | TODO | TODO |
| Public IPv4 addresses | per address-hour | NAT EIP + ALB addresses (TODO: count) | TODO | TODO | TODO | TODO |
| **Total hourly** | | | | | **TODO** | **TODO** |

## Smaller ongoing charges

| Item | Billing unit | Notes | Monthly (TODO) |
|---|---|---|---|
| Secrets Manager | per secret per month + per 10,000 API calls | 3 app secrets per env + RDS-managed secret | TODO |
| CloudWatch Logs | per GB ingested + per GB-month stored | EKS control plane logs, VPC flow logs, RDS logs; short retention in dev | TODO |
| SQS | per million requests | Long polling: each worker pod makes a receive call at least every 20 s | TODO |
| ECR | per GB-month storage | Lifecycle keeps 30 tagged images, expires untagged after 1 day | TODO |
| S3 (Terraform state) | per GB-month + requests | Negligible | TODO |
| KMS | per key per month + requests | EKS secrets envelope encryption | TODO |
| Data transfer | per GB | NAT processing, cross-AZ traffic, internet egress | TODO |

## Scenarios

Fill these from the AWS Pricing Calculator. Save the calculator's shareable link next to each row.

| Scenario | Assumption | Monthly estimate | Calculator link |
|---|---|---|---|
| Dev 24/7 | 730 hours/month | TODO | TODO |
| Dev "on only while working" | TODO hours/week × 4.35 weeks | TODO | TODO |
| Prod 24/7 (for reference only; prod is not kept running) | 730 hours/month | TODO | TODO |
| Prod demo | TODO hours per demo | TODO | TODO |

The difference between "Dev 24/7" and "Dev while working" is the main argument for the
`make infra-up` / `make infra-down` workflow.

## How to fill this in

### Estimates: AWS Pricing Calculator

1. Open the AWS Pricing Calculator, create an estimate, and set the region to US East (Ohio)
   `us-east-2`.
2. Add one service per row of the idle table, using the configuration from `infra/envs/<env>/*.tfvars`:
   - Amazon EKS: 1 cluster.
   - Amazon VPC: NAT gateways (count per env) and an estimate of GB processed; public IPv4 addresses.
   - Amazon EC2: instance types and count; for spot, use the calculator's spot option or note the
     current spot price from the EC2 console's spot pricing history.
   - Amazon RDS for PostgreSQL: instance class, single-AZ vs Multi-AZ, gp3 storage size, backup
     retention.
   - Elastic Load Balancing: 1 ALB with a low LCU estimate.
   - Amazon EBS: gp3 volume sizes from the launch template and the PVCs.
   - Secrets Manager, CloudWatch, SQS, ECR as in the smaller-charges table.
3. For "while working", set the hours per month on hourly resources to your actual working hours.
4. Copy the per-service monthly figures into the tables above and save the estimate link.

### Actuals: Cost Explorer

1. Activate the `Project` tag as a cost allocation tag (Billing → Cost allocation tags). Tags only
   appear after resources carrying them have been billed, and activation is not retroactive by
   default, so do this on day 1.
2. In Cost Explorer, filter by tag `Project = pulse`, group by Service, daily granularity.
3. Also look at the unfiltered view: some charges are not tagged (for example data transfer, some
   EKS charges, or resources created by controllers without the tag) and will not appear under the
   tag filter.
4. Record weekly:

| Week (start date) | Hours dev was up | Tagged cost | Untagged Pulse-related cost | Total | Notes |
|---|---|---|---|---|---|
| TODO | TODO | TODO | TODO | TODO | TODO |

### Budget guardrails

- AWS Budgets alert at 50%, 80%, 100% of the monthly limit (TODO: record your limit).
- Optional Cost Anomaly Detection monitor.

## Cost levers (what moved the number)

TODO: after a few weeks, list which levers actually mattered, with numbers from Cost Explorer.
Candidates: tearing dev down nightly, spot vs on-demand, single NAT, smaller node types, shorter log
retention, S3 gateway endpoint for ECR layers.

## At larger scale

Things that would change the cost shape (no numbers here until measured): Savings Plans or Reserved
Instances for steady prod load; Karpenter for tighter bin-packing; VPC interface endpoints vs NAT data
processing; partitioned `check_results` to keep RDS storage flat; per-namespace cost visibility with
OpenCost.
