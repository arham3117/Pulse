# PulseChecksStalled

## What it means

No checks have completed for 5 minutes while there are active monitors:

```promql
(sum(rate(pulse_checks_total[5m])) or vector(0)) == 0
  and on() max(pulse_active_monitors) > 0
```

The `or vector(0)` matters: when every worker pod is gone, `pulse_checks_total` has no series at all,
and `sum(rate(...))` over nothing returns an empty result rather than 0, so an expression without it
would never fire in exactly the case it exists for. Check the PrometheusRule in the chart uses this
form.

Pulse is not monitoring anything right now.

## Impact

No site is being checked. Outages of monitored sites are not detected, no incidents open, no
notifications are sent. The status page shows the "data may be stale" banner after 3 minutes.
This is a full outage of Pulse's core function.

## First checks (5 minutes)

1. Grafana "Pulse / Service overview": "Checks/sec by result", "Queue messages", "worker replica
   count". Is the queue growing (workers problem) or empty (scheduler problem)?

   ```sh
   qdepth "$QURL"
   promq 'sum(rate(pulse_scheduler_checks_enqueued_total[5m]))'
   promq 'sum(rate(pulse_worker_message_errors_total[5m]))'
   promq 'up{job=~"pulse-worker|pulse-scheduler"}'
   ```

2. Pods:

   ```sh
   kubectl -n pulse get deploy pulse-worker pulse-scheduler
   kubectl -n pulse get pods -o wide | grep -E '^NAME|pulse-worker|pulse-scheduler'
   kubectl -n pulse get scaledobject pulse-worker -o jsonpath='{.metadata.annotations}'; echo
   ```

3. Logs: `{namespace="pulse", pod=~"pulse-worker-.*"} | json | level="error"` and the same for
   `pulse-scheduler-.*`.
4. Argo CD: recent deploy? `kubectl -n argocd get application pulse`.
5. Dependencies: RDS status, and whether workers can reach SQS (AccessDenied or timeouts in logs).

## Likely causes and fixes

| Cause | How to confirm | Fix |
|---|---|---|
| Workers scaled to 0 (KEDA paused; drill 1) | Deployment shows 0 replicas; `autoscaling.keda.sh/paused-replicas: "0"` annotation | Remove the annotation (git revert if set in git, else `kubectl -n pulse annotate scaledobject pulse-worker autoscaling.keda.sh/paused-replicas-`) |
| Workers crashing | CrashLoopBackOff; see [PulsePodCrashLooping](PulsePodCrashLooping.md) | Fix or roll back |
| Scheduler not enqueuing | Queue empty; `pulse_scheduler_checks_enqueued_total` flat | See [PulseSchedulerDown](PulseSchedulerDown.md); check scheduler logs for DB or SQS errors |
| Database down | RDS not `available`; every message fails (`pulse_worker_message_errors_total` rising) | Wait for RDS; messages failing 3 times go to the DLQ, see [PulseDLQNotEmpty](PulseDLQNotEmpty.md) afterwards |
| Workers lack SQS permissions | AccessDenied on `ReceiveMessage` in logs | `aws eks list-pod-identity-associations --cluster-name pulse-$ENV --namespace pulse`; check role policy and that the pod uses service account `pulse-worker` |
| Egress broken (NAT) | Not a stall: checks would complete as failures. If all checks fail with `connection`/`timeout`, look at NAT and route tables instead | See [ADR 0009](../adr/0009-single-nat-gateway-in-dev.md) |
| Metrics problem, not a real stall | Workers healthy and results are being written (`SELECT max(checked_at) FROM check_results`), but `up{job="pulse-worker"}` is 0 or missing | Fix the ServiceMonitor/PodMonitor selector or the metrics port |

## Escalation / follow-up

- Treat as a full outage of the product. Restore workers or scheduler before root-causing.
- If messages went to the DLQ during the stall, decide whether to redrive or purge them (stale checks
  have little value).
- Record: time checks stopped (last `check_results.checked_at`), alert time, recovery time, and
  enqueued-vs-executed difference to estimate lost checks.
