# PulseCheckBacklog

## What it means

More than 500 check messages have been waiting (visible) in `pulse-<env>-checks` for 5 minutes:

```promql
max(pulse_queue_messages{queue="checks", state="visible"}) > 500
```

(`max` because both scheduler replicas export the same gauge.) Workers are not keeping up with the
scheduler. Checks are running late, and any message older than the queue's **1-hour retention** is
deleted by SQS without ever being checked.

## Impact

Monitored sites are checked late: outages are detected late and notifications are delayed. The status
page may show the "data may be stale" banner. The check freshness SLO is burning. If the backlog lasts
more than an hour, checks are lost.

## First checks (5 minutes)

1. Grafana "Pulse / Service overview": "Queue messages (visible / in-flight)", "Worker in-flight
   checks", "worker replica count", "Checks/sec by result", "Schedule lag p95".
2. Workers and KEDA:

   ```sh
   kubectl -n pulse get deploy pulse-worker
   kubectl -n pulse get scaledobject
   kubectl -n pulse describe scaledobject pulse-worker | sed -n '/Conditions/,$p'
   kubectl -n pulse get hpa          # KEDA creates keda-hpa-<scaledobject>
   kubectl -n pulse get pods -o wide | grep -E '^NAME|pulse-worker'
   kubectl -n pulse get scaledobject pulse-worker -o jsonpath='{.metadata.annotations}'; echo
   ```

3. Queue from AWS directly, and age of the oldest message:

   ```sh
   qdepth "$QURL"
   aws cloudwatch get-metric-statistics --namespace AWS/SQS --metric-name ApproximateAgeOfOldestMessage \
     --dimensions Name=QueueName,Value="pulse-$ENV-checks" \
     --start-time "$(date -u -v-30M +%FT%TZ)" --end-time "$(date -u +%FT%TZ)" \
     --period 60 --statistics Maximum --output table
   ```

4. Logs: `{namespace="pulse", pod=~"pulse-worker-.*"} | json | level="error"` and
   `kubectl -n keda logs deploy/keda-operator --tail=100 | grep -i -E 'error|pulse'`.
5. Argo CD: recent change to worker values (concurrency, replicas, KEDA settings)?

## Likely causes and fixes

| Cause | How to confirm | Fix |
|---|---|---|
| KEDA paused or not scaling | Annotation `autoscaling.keda.sh/paused-replicas` present; ScaledObject `Ready=False`; KEDA logs show AccessDenied | Remove the annotation (in git if it came from git; otherwise `kubectl -n pulse annotate scaledobject pulse-worker autoscaling.keda.sh/paused-replicas-`); fix the KEDA Pod Identity association (`aws eks list-pod-identity-associations --cluster-name pulse-$ENV --namespace keda`) |
| Workers at max replicas (10) and still behind | HPA shows 10/10; in-flight ≈ 10 × concurrency | Temporarily raise `maxReplicaCount` and/or `PULSE_WORKER_CONCURRENCY` in `pulse-gitops` values |
| Worker pods Pending (no node capacity) | Pending pods; `Insufficient cpu/memory` events | Check Cluster Autoscaler logs (`kubectl -n kube-system logs -l app.kubernetes.io/name=aws-cluster-autoscaler --tail=100`); node group max size; spot capacity |
| Slow targets saturating concurrency (noisy monitors) | `pulse_check_duration_seconds` p95 high; many `timeout` errors; in-flight at concurrency limit | Pause the offending monitors (`POST /api/v1/monitors/{id}/pause`); lower their timeout; see drill 7 |
| Workers crashing | Restarts, see [PulsePodCrashLooping](PulsePodCrashLooping.md) | Fix the crash; roll back if deploy-related |
| Database slow, every check's transaction slow | DB CPU high; worker logs show slow commits | See [PulseAPIHighLatency](PulseAPIHighLatency.md) database section |
| Load test (Scenario B) | 2,000 test monitors exist | Expected; ensure it drains and delete the test monitors afterwards |

## Escalation / follow-up

- If the oldest message age approaches 1 hour, checks are about to be dropped: scale workers first,
  investigate second.
- If it started with a deploy, roll back in `pulse-gitops`.
- Record: peak visible messages, oldest message age, worker replicas over time, whether messages
  expired (enqueued vs executed: `sum(increase(pulse_scheduler_checks_enqueued_total[1h])) -
  sum(increase(pulse_checks_total[1h]))`).
