# PulseSchedulerDown

## What it means

Prometheus has had no healthy scrape target for the scheduler for 2 minutes:

```promql
sum(up{job="pulse-scheduler"}) == 0
  or absent(up{job="pulse-scheduler"})
```

The first half fires when targets exist but every scrape fails. `absent()` covers the case where the
target disappears entirely (Deployment scaled to 0 or deleted, ServiceMonitor broken), when there is
no `up` series to compare with 0.

Both scheduler replicas are either down or not being scraped. If they are really down, no new checks
are being enqueued.

## Impact

If the scheduler is really down: no checks are enqueued, workers drain the remaining queue within
minutes, then everything stops (`PulseChecksStalled` follows). Outages of monitored sites go
undetected; the status page shows the stale-data banner after 3 minutes.

If only scraping is broken: no user impact, but you are blind to scheduler and queue metrics
(`pulse_queue_messages` and `pulse_active_monitors` come from the scheduler, so backlog and DLQ
alerts also stop working).

## First checks (5 minutes)

1. Is it really down, or just not scraped?

   ```sh
   kubectl -n pulse get deploy pulse-scheduler
   kubectl -n pulse get pods -o wide | grep -E '^NAME|pulse-scheduler'
   promq 'up{job="pulse-scheduler"}'
   ```

   In the Prometheus UI: Status → Targets, filter `pulse-scheduler`.
2. Pod details and previous logs:

   ```sh
   kubectl -n pulse describe pod <scheduler-pod>
   kubectl -n pulse logs <scheduler-pod> --previous --tail=100
   ```

3. Logs: `{namespace="pulse", pod=~"pulse-scheduler-.*"} | json | level="error"`.
4. Argo CD: recent deploy or chart change (ServiceMonitor labels, ports)?
   `kubectl -n argocd get application pulse -o jsonpath='{.status.sync.status} {.status.health.status}'`
5. Dependencies: secrets synced and DB reachable?

   ```sh
   kubectl -n pulse get externalsecret
   aws rds describe-db-instances --db-instance-identifier "pulse-$ENV-db" --query 'DBInstances[0].DBInstanceStatus'
   ```

## Likely causes and fixes

| Cause | How to confirm | Fix |
|---|---|---|
| Pods crash on startup (config validation, missing env) | CrashLoopBackOff; pydantic validation error in `--previous` logs | Fix values in `pulse-gitops` or revert the deploy |
| `pulse-db` or `pulse-app` Secret missing | `CreateContainerConfigError`; ExternalSecret not `SecretSynced` | Check ESO logs (`kubectl -n external-secrets logs deploy/external-secrets --tail=50`) and the Secrets Manager value; see [set-secrets.md](set-secrets.md) |
| Pods not ready (`/readyz` fails because DB is down) | Running but not Ready; readiness probe failures in events | Wait for RDS; check SG and SSL settings if it never recovers |
| Scaled to 0 or deleted | Deployment shows 0/0 or does not exist; Argo CD shows OutOfSync | Argo CD sync; check values for `replicas` |
| Pods Pending | No node capacity; `Insufficient` events | Cluster Autoscaler, node group |
| Scraping broken, pods fine | Pods Ready and logs show loops running; target missing in Prometheus | Fix ServiceMonitor/PodMonitor selector, port name `9100`, or the label Prometheus selects on (kube-prometheus-stack selects by release label unless configured otherwise) |

## Escalation / follow-up

- Two replicas exist so that one can fail without impact. Both failing together points at something
  shared: a deploy, a secret, the database, or node capacity.
- If caused by a deploy, roll back via `git revert` in `pulse-gitops`.
- Record: down window, whether checks stopped (`pulse_scheduler_checks_enqueued_total`), and
  overdue monitors at recovery (they resume at `now() + interval`, so the gap is not backfilled).
