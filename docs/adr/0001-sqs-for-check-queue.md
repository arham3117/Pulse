# 0001: Use Amazon SQS for the checks queue instead of Redis or RabbitMQ

- Status: Proposed (draft — rewrite in your own words before marking Accepted)
- Date: 2026-11-22

## Context

The scheduler decides when a check is due; workers execute checks. Something has to carry work
between them so that workers can scale independently, a crashed worker does not lose work, and a
message that keeps failing does not block the rest.

Constraints:

- One person operates this. Anything stateful inside the cluster (a broker with persistent volumes,
  upgrades, failover) is operational work that does not demonstrate much.
- Dev is torn down and rebuilt often. A queue that survives or is recreated by Terraform in seconds
  fits that workflow; a broker StatefulSet with a PVC does not.
- Workers should autoscale on backlog (KEDA).
- Checks are independent, so ordering does not matter. Duplicates can be made harmless in the
  database (see [0002](0002-scheduler-select-for-update-skip-locked.md)).
- Local development must work offline and for free.

## Options considered

1. **Amazon SQS (standard queue)**
   - Pros: fully managed, nothing to patch or scale; built-in dead-letter queue via redrive policy;
     visibility timeout gives at-least-once redelivery when a worker dies; per-queue retention
     (useful to drop stale checks); IAM-scoped access via Pod Identity; KEDA has an
     `aws-sqs-queue` scaler; ElasticMQ provides an SQS-compatible local replacement; cost is
     per request, so it is close to zero when dev is off.
   - Cons: at-least-once delivery and best-effort ordering, so consumers must be idempotent;
     queue attributes (`ApproximateNumberOfMessages`) are approximate; long polling still costs a
     request every 20 s per poller; AWS-specific API.
2. **Redis (lists or streams) in the cluster or ElastiCache**
   - Pros: very low latency; familiar; streams have consumer groups and pending-entry lists.
   - Cons: in-cluster Redis needs persistence and HA to avoid losing work; ElastiCache is billed
     hourly while idle; no native DLQ (must be built with retry counters); KEDA scaling works but
     the redelivery and poison-message logic is ours to write.
3. **RabbitMQ (in-cluster or Amazon MQ)**
   - Pros: mature broker; dead-letter exchanges; acknowledgements; flexible routing.
   - Cons: a stateful system to run (quorum queues, disk alarms, upgrades) or Amazon MQ billed hourly;
     more features than this workload needs.
4. **Kafka / Amazon MSK**
   - Pros: durable log, replay, very high throughput.
   - Cons: designed for streams, not a work queue with per-message acknowledgement; no per-message
     retry or DLQ without extra tooling; MSK is expensive to keep running for a portfolio project.
5. **Postgres as the queue** (`SKIP LOCKED` on a jobs table)
   - Pros: one less service; transactional with the data.
   - Cons: workers would hold DB connections while waiting; scaling on backlog needs a custom metric;
     loses the chance to demonstrate a decoupled queue and DLQ handling.

## Decision

Use an SQS standard queue `pulse-<env>-checks` with a DLQ `pulse-<env>-checks-dlq`
(`maxReceiveCount = 3`), 60 s visibility timeout, 1-hour retention on the main queue and 4 days on
the DLQ, SSE with SQS-managed keys. Locally, use ElasticMQ.

## Consequences

- Easier: no broker to operate; DLQ and redelivery come for free; KEDA scales workers on queue depth;
  queue survives node and pod failures.
- Harder: consumers must be idempotent (`UNIQUE (monitor_id, scheduled_for)` + `ON CONFLICT DO
  NOTHING`). The visibility timeout (60 s) must stay longer than the longest check (30 s timeout plus
  up to 3 redirects) or messages are redelivered while still being processed.
- Retention of 1 hour means checks delayed more than an hour are silently dropped. That is intended
  (a stale check is useless) but it hides lost work from the freshness SLI, so the backlog and stalled
  alerts must cover it.
- The scheduler still needs Postgres for "when is each monitor due": SQS delay is capped at
  15 minutes and is not a scheduler.
- At much larger scale: consider separate queues per priority or per tenant to avoid noisy
  neighbours, FIFO queues only if per-monitor ordering ever matters (with their throughput limits
  in mind), and VPC interface endpoints for SQS to avoid NAT dependency.
