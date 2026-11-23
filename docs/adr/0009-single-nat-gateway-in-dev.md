# 0009: Single NAT gateway in dev, one NAT gateway per AZ in prod

- Status: Proposed (draft — rewrite in your own words before marking Accepted)
- Date: 2026-11-22

## Context

Nodes and pods run in private subnets across 2 AZs. They need outbound internet access for: pulling
images (ECR API; layers come from S3 via the gateway endpoint), calling SQS, Secrets Manager and STS,
sending Slack/Discord webhooks, and, most importantly, **running every external check**. For Pulse,
egress is the product.

A NAT gateway is a zonal resource billed per hour plus per GB processed, whether or not traffic flows
(see [cost.md](../cost.md)). Each additional NAT gateway also needs its own Elastic IP (public IPv4
addresses are billed hourly).

## Options considered

1. **One NAT gateway per AZ**
   - Pros: an AZ failure only affects that AZ's private subnets; no cross-AZ data transfer for egress
     because each private subnet routes to the NAT in its own AZ.
   - Cons: N times the hourly cost.
2. **Single NAT gateway shared by all AZs**
   - Pros: lowest hourly cost.
   - Cons: if the NAT's AZ fails, private subnets in every AZ lose internet egress: all external
     checks fail, and SQS/Secrets Manager calls fail unless VPC endpoints exist; traffic from the
     other AZ crosses AZ boundaries (data transfer charges).
3. **NAT instance** (for example a small EC2 instance running NAT)
   - Pros: cheaper.
   - Cons: we operate it (patching, failover, bandwidth limits); a single instance is a single point
     of failure.
4. **VPC interface endpoints for AWS APIs** (SQS, ECR API, Secrets Manager, STS) in addition to NAT
   - Pros: AWS API traffic stays inside the VPC and does not depend on NAT.
   - Cons: each interface endpoint is billed per hour per AZ; still need NAT for external checks.

## Decision

Make NAT count a Terraform variable (`single_nat_gateway`). Dev uses a single NAT gateway. Prod uses
one NAT gateway per AZ. The Checkov finding for the dev configuration is suppressed inline with the
justification "single NAT in dev is a deliberate cost trade-off".

## Consequences

- Easier: dev costs less per hour while running.
- Harder: dev has a zonal single point of failure for all egress. During an AZ outage, dev Pulse
  would report every monitored site as down (connection errors) even though the sites are fine, which
  is the worst kind of false alarm for a monitoring product.
- Prod's per-AZ NAT removes that dependency at a higher fixed cost.
- An outbound failure mode worth knowing: when NAT egress fails, checks fail with `connection` or
  `timeout` errors for every monitor at once. A sudden mass-incident across unrelated sites points to
  Pulse's egress, not to the sites.
- At larger scale: add interface endpoints for SQS, STS and Secrets Manager so the control path does
  not depend on NAT, and consider running checks from multiple regions.
