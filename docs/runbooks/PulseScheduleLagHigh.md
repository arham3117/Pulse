# PulseScheduleLagHigh

## What it means

The p95 of `checked_at - scheduled_for` has been above 90 seconds for 10 minutes:

```promql
histogram_quantile(0.95, sum by (le) (rate(pulse_check_schedule_lag_seconds_bucket[5m]))) > 90
```

Checks run, but late. Lag is the sum of three delays; finding which one grew is the whole
investigation:

1. **Scheduler delay**: due monitors not yet enqueued (`pulse_scheduler_lag_seconds`).
2. **Queue wait**: messages sitting in SQS before a worker takes them (queue depth).
3. **Execution**: time waiting for a concurrency slot plus the check itself
   (`pulse_check_duration_seconds`).

## Impact

Outage detection and notifications are delayed by roughly the lag. The check freshness SLO
(99% within 90 s) is burning; see [slo.md](../slo.md).

## First checks (5 minutes)

1. Grafana "Pulse / Service overview": "Schedule lag p95", "Queue messages", "Worker in-flight
   checks". Then split the lag:

   ```sh
   promq 'max(pulse_scheduler_lag_seconds)'                                   # 1. scheduler
   promq 'max by (state) (pulse_queue_messages{queue="checks"})'              # 2. queue
   promq 'histogram_quantile(0.95, sum by (le) (rate(pulse_check_duration_seconds_bucket[5m])))'  # 3. execution
   promq 'sum(pulse_worker_inflight_checks)'
   promq 'histogram_quantile(0.95, sum by (le) (rate(pulse_scheduler_loop_duration_seconds_bucket[5m])))'
   ```

2. `kubectl -n pulse get pods -o wide | grep -E '^NAME|pulse-(scheduler|worker)'` and
   `kubectl -n pulse get hpa`.
3. Logs: `{namespace="pulse", pod=~"pulse-scheduler-.*"} | json | level=~"warning|error"`.
4. Argo CD: recent changes to `PULSE_SCHEDULER_BATCH_SIZE`, `PULSE_SCHEDULER_INTERVAL_SECONDS`,
   worker concurrency or KEDA targets?

## Likely causes and fixes

| Cause | How to confirm | Fix |
|---|---|---|
| Workers behind (queue wait) | Visible messages high and rising; see [PulseCheckBacklog](PulseCheckBacklog.md) | Scale workers; fix KEDA or node capacity |
| Worker concurrency saturated by slow targets | In-flight ≈ replicas × `PULSE_WORKER_CONCURRENCY`; check duration p95 near timeouts | Pause or fix noisy monitors; raise concurrency or replicas; consider per-check timeout caps (drill 7) |
| Scheduler behind | `pulse_scheduler_lag_seconds` high; loop duration high; due count at batch size every loop | Raise `PULSE_SCHEDULER_BATCH_SIZE`; check the scheduler query uses the partial index; check SQS send latency |
| Scheduler recovering from downtime | Lag spike right after scheduler restart, then falling | Expected: overdue monitors are sent once with their old `scheduled_for` (large lag), then rescheduled at `now() + interval`. Should clear within one interval |
| Database slow | Loop duration high; worker commit slow; RDS CPU high | See [PulseAPIHighLatency](PulseAPIHighLatency.md) database section |
| Node pressure, CPU starvation | Nodes near capacity; Pending pods | Cluster Autoscaler; node group size |

## Escalation / follow-up

- If lag keeps rising, it becomes a backlog and then lost checks (1-hour retention): escalate to
  [PulseCheckBacklog](PulseCheckBacklog.md) handling.
- Record: peak p95 lag, which component contributed (scheduler / queue / execution), duration, and
  freshness budget consumed.
