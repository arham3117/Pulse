# 0002: Scheduler replicas coordinate with SELECT ... FOR UPDATE SKIP LOCKED

- Status: Proposed (draft — rewrite in your own words before marking Accepted)
- Date: 2026-11-22

## Context

The scheduler finds monitors whose `next_check_at` has passed, enqueues one SQS message per check,
and advances `next_check_at`. One replica is a single point of failure: if it dies, no checks run
until Kubernetes restarts it. Two replicas without coordination would both read the same due rows
and enqueue every check twice.

Requirements:

- Run 2 replicas safely, with no extra infrastructure.
- A crash at any point must not lose a check permanently or corrupt state.
- Duplicate messages, if they happen, must not create duplicate results, incidents or notifications.

## Options considered

1. **Single replica**
   - Pros: simplest.
   - Cons: every restart, eviction, node drain or spot interruption stops scheduling until the pod
     is back.
2. **Leader election** (Kubernetes `Lease`, or a Postgres advisory lock held by one replica)
   - Pros: only one active scheduler; well-known pattern.
   - Cons: standby does no work; failover waits for lease expiry; split-brain handling still needs
     idempotency downstream; more code.
3. **Row-level locking with `FOR UPDATE SKIP LOCKED`**
   - Pros: all replicas do useful work; no coordination service; failover is instant because there
     is nothing to fail over; uses database time (`now()`), so pod clock skew does not matter.
   - Cons: holds row locks while calling SQS (an external side effect inside a DB transaction);
     needs idempotency for the commit-failure case.
4. **Per-monitor schedules in EventBridge Scheduler**
   - Pros: managed timing.
   - Cons: one AWS resource per monitor to create and keep in sync; harder to test locally; cost
     scales with monitor count.

## Decision

Each scheduler loop runs one transaction:

```sql
BEGIN;
SELECT id, interval_seconds, next_check_at
  FROM monitors
 WHERE is_active AND next_check_at <= now()
 ORDER BY next_check_at
 LIMIT :batch_size
 FOR UPDATE SKIP LOCKED;
-- SendMessageBatch to SQS (10 per call): {"monitor_id", "scheduled_for": next_check_at}
-- UPDATE monitors SET next_check_at = <next> WHERE id = ...
COMMIT;
```

`next = next_check_at + interval`; if that is still in the past, `next = now() + interval` (no
burst of catch-up checks after downtime).

How it behaves:

- **Semantics.** `FOR UPDATE` takes a row lock on every selected row until the transaction ends.
  `SKIP LOCKED` makes another transaction's `SELECT ... FOR UPDATE` skip rows that are already locked
  instead of waiting for them. Two replicas running the query at the same moment therefore get
  disjoint sets of rows. Once the first transaction commits, those rows have a future
  `next_check_at` and no longer match `next_check_at <= now()`, so they are not picked up again.
  Under Postgres's default READ COMMITTED isolation, a row whose lock is released by a commit is
  re-checked against the `WHERE` clause before being returned, which is why the updated row is
  excluded.
- **SQS send succeeds, commit fails** (DB connection drops, pod killed between send and commit).
  The transaction rolls back: `next_check_at` is unchanged and the locks are released. The next loop
  (on any replica) selects the same rows and sends messages with the **same**
  `(monitor_id, scheduled_for)`, because `scheduled_for` is the unchanged `next_check_at`. Now two
  messages exist for one check.
- **Why duplicates are harmless.** `check_results` has `UNIQUE (monitor_id, scheduled_for)`. The
  worker inserts with `ON CONFLICT (monitor_id, scheduled_for) DO NOTHING`. If no row was inserted,
  the worker knows the check was already recorded and skips the state update, incident state machine
  and notifications, then deletes the message. The only cost is one extra HTTP request to the
  monitored site (the worker runs the check before the insert). The same mechanism covers SQS's own
  at-least-once redelivery.
- **SQS send fails.** The loop raises, the transaction rolls back, and nothing advances. Partial
  failures from `SendMessageBatch` (some entries in `Failed`) must be handled explicitly: only
  advance `next_check_at` for entries that were sent, or roll back the whole batch.
- **Timestamp precision.** `scheduled_for` must round-trip exactly (microseconds, UTC) through JSON
  and back, or the unique constraint will not recognise the duplicate.

## Consequences

- Easier: two replicas share load and either can die without a gap; no leader election code; a PDB
  with `minAvailable: 1` keeps scheduling running through node drains.
- Harder: row locks are held while SQS is called. A slow SQS call lengthens the transaction; batch
  size (`PULSE_SCHEDULER_BATCH_SIZE`, 500) bounds how many rows are locked. The integration test runs
  two schedulers for 60 s and asserts no `(monitor_id, scheduled_for)` is enqueued twice.
- Not exactly-once: correctness relies on downstream idempotency, which must be preserved by any
  future change to the worker.
- At larger scale: the partial index `(next_check_at) WHERE is_active` keeps the query cheap; beyond
  that, shard monitors by hash across schedulers, or use a transactional outbox so the SQS send
  happens outside the locking transaction.
