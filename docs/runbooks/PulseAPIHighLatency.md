# PulseAPIHighLatency

## What it means

The API's p95 response time has been above 0.5 s for 10 minutes:

```promql
histogram_quantile(0.95, sum by (le) (rate(pulse_http_request_duration_seconds_bucket[5m]))) > 0.5
```

Requests still succeed, but slowly. Latency problems often precede errors (timeouts, pool
exhaustion), so treat this as an early warning.

## Impact

Status page and API feel slow. If latency keeps growing, the ALB or clients time out and this turns
into [PulseAPIHighErrorRate](PulseAPIHighErrorRate.md).

## First checks (5 minutes)

1. Grafana "Pulse / Service overview": "latency p50/p95/p99" and "API requests/sec". Is it all
   routes or one? Did traffic increase?

   ```sh
   promq 'histogram_quantile(0.95, sum by (le, route) (rate(pulse_http_request_duration_seconds_bucket[5m])))'
   promq 'sum by (route) (rate(pulse_http_requests_total[5m]))'
   ```

2. CPU and scaling:

   ```sh
   kubectl -n pulse get hpa
   kubectl -n pulse top pods | grep -E '^NAME|pulse-api'
   kubectl top nodes
   kubectl -n pulse get pods | grep -E 'pulse-api.*Pending'
   ```

3. Logs: `{namespace="pulse", pod=~"pulse-api-.*"} | json | level=~"warning|error"`; look for pool
   timeouts or slow-query warnings.
4. Argo CD: any deploy in the last hour (new query, removed index, migration)?
5. Database: RDS CPU and connections in CloudWatch, slow query log, and from the SQL shell
   ([README](README.md#sql-shell-against-rds)) the `pg_stat_activity` and blocking queries.

   ```sh
   aws rds describe-db-log-files --db-instance-identifier "pulse-$ENV-db" --query 'DescribeDBLogFiles[-3:]'
   aws rds download-db-log-file-portion --db-instance-identifier "pulse-$ENV-db" \
     --log-file-name <name-from-above> --output text | grep 'duration:' | tail -20
   ```

## Likely causes and fixes

| Cause | How to confirm | Fix |
|---|---|---|
| Stats/status page queries scan too much of `check_results` | Slow routes are `/api/v1/monitors/{id}/stats`, `/`, `/monitors/{id}`; slow query log shows them; `EXPLAIN ANALYZE` shows seq scans | Confirm index `(monitor_id, checked_at DESC)` exists; confirm the retention CronJob runs (`kubectl -n pulse get cronjob,jobs`); cache or precompute stats |
| Retention job failing, table growing | `pg_total_relation_size('check_results')` keeps growing; failed maintenance Jobs | Fix the CronJob; run it manually: `kubectl -n pulse create job --from=cronjob/<name> retention-manual` |
| CPU saturation, HPA at max | HPA at 6 replicas; pods near request; nodes full | Raise HPA max in `pulse-gitops` values; check Cluster Autoscaler is adding nodes |
| Pods Pending (no node capacity) | Pending api pods; `kubectl describe pod` shows `Insufficient cpu` | See Cluster Autoscaler logs: `kubectl -n kube-system logs -l app.kubernetes.io/name=aws-cluster-autoscaler --tail=100` |
| DB connection pool waits | Logs show pool timeout; DB connections at pool max per pod | Tune pool size vs replicas vs `max_connections` |
| RDS under load (CPU credits on burstable class) | RDS `CPUUtilization` high, `CPUCreditBalance` near zero (t4g classes) | Reduce load; larger class in prod; investigate what is consuming CPU |
| Load test or traffic spike | Request rate jump; k6 running | Expected during Scenario A; otherwise scale out |

## Escalation / follow-up

- If latency started with a deploy, roll back via `git revert` in `pulse-gitops`.
- If p95 approaches client/ALB timeouts and errors appear, follow
  [PulseAPIHighErrorRate](PulseAPIHighErrorRate.md).
- Record: which routes, p95 values, request rate, DB metrics, and any query plan captured. A slow
  query found here is a good candidate for the "find and fix one bottleneck" exercise
  ([load-test-results.md](../load-test-results.md)).
