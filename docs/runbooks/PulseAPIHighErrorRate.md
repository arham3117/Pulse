# PulseAPIHighErrorRate

## What it means

More than 5% of API requests returned a 5xx status for 5 minutes, measured by the API itself:

```promql
sum(rate(pulse_http_requests_total{status=~"5.."}[5m]))
  / sum(rate(pulse_http_requests_total[5m])) > 0.05
```

The status page and JSON API are failing for a meaningful share of visitors. Checks may still be
running (workers do not depend on the API), so this is usually a read-path problem, but a database
outage breaks both.

Note: this metric only counts responses produced by Pulse. If no pod is ready, the ALB returns 503
itself and those errors are **not** in this metric (the alert can even go quiet as traffic to pods
drops to zero). Always check the ALB metrics too.

## Impact

Status page and API requests fail or partially fail. Admin writes (create/pause monitors) may fail.
The availability SLO budget (0.5% over 30 days) is being spent; see [slo.md](../slo.md).

## First checks (5 minutes)

1. Grafana "Pulse / Service overview": panels "API requests/sec", "error %", "latency p50/p95/p99".
   Which routes and status codes?

   ```sh
   promq 'sum by (route, status) (rate(pulse_http_requests_total{status=~"5.."}[5m]))'
   ```

2. Pods and recent events:

   ```sh
   kubectl -n pulse get pods -o wide | grep -E '^NAME|pulse-api'
   kubectl -n pulse describe deploy pulse-api | sed -n '/Conditions/,$p'
   kubectl -n pulse get events --sort-by=.lastTimestamp | grep -i api | tail -20
   ```

3. Logs: `{namespace="pulse", pod=~"pulse-api-.*"} | json | level="error"`. Look for database
   errors (connection refused, too many connections, SSL), timeouts, and tracebacks.
4. Argo CD: was anything deployed in the last hour? (`kubectl -n argocd get application pulse -o
   jsonpath='{range .status.history[*]}{.deployedAt}{"  "}{.revision}{"\n"}{end}'`)
5. ALB-side errors and target health:

   ```sh
   ALB_DNS=$(kubectl -n pulse get ingress -o jsonpath='{.items[0].status.loadBalancer.ingress[0].hostname}')
   ALB_ARN=$(aws elbv2 describe-load-balancers --query "LoadBalancers[?DNSName=='$ALB_DNS'].LoadBalancerArn" --output text)
   for tg in $(aws elbv2 describe-target-groups --load-balancer-arn "$ALB_ARN" --query 'TargetGroups[].TargetGroupArn' --output text); do
     aws elbv2 describe-target-health --target-group-arn "$tg" \
       --query 'TargetHealthDescriptions[].[Target.Id,TargetHealth.State,TargetHealth.Reason]' --output table
   done
   # ALB-generated 5xx in the last 30 minutes (macOS date; on Linux use: date -u -d '-30 min' ...)
   aws cloudwatch get-metric-statistics --namespace AWS/ApplicationELB --metric-name HTTPCode_ELB_5XX_Count \
     --dimensions Name=LoadBalancer,Value="${ALB_ARN#*:loadbalancer/}" \
     --start-time "$(date -u -v-30M +%FT%TZ)" --end-time "$(date -u +%FT%TZ)" \
     --period 60 --statistics Sum --output table
   ```

6. Database state:

   ```sh
   aws rds describe-db-instances --db-instance-identifier "pulse-$ENV-db" \
     --query 'DBInstances[0].[DBInstanceStatus,MultiAZ]' --output text
   aws rds describe-events --source-type db-instance --source-identifier "pulse-$ENV-db" --duration 120
   ```

## Likely causes and fixes

| Cause | How to confirm | Fix |
|---|---|---|
| Bad deploy (new code raises errors) | Errors started at a deploy time in Argo CD history; tracebacks in new pods only | `git revert` the deploy commit in `pulse-gitops` (see [README](README.md#rolling-back)) |
| Database unavailable (reboot, failover, maintenance) | `DBInstanceStatus` not `available`; RDS events; logs show connection errors; `/readyz` 503 | Wait for RDS; pods return to the ALB when `/readyz` passes. In prod, Multi-AZ failover should be short |
| Database connections exhausted | Logs: "too many connections" or pool timeout; `SELECT count(*) FROM pg_stat_activity` near `max_connections` | Lower pool size per pod or HPA max; check for leaked connections; larger instance class has a higher `max_connections` |
| Slow queries causing timeouts | Errors on `/stats` or status page routes; slow query log (`log_min_duration_statement = 500`) | See [PulseAPIHighLatency](PulseAPIHighLatency.md); check `check_results` index and retention job |
| Secret missing or wrong (DB password rotated) | ExternalSecret not `SecretSynced`; auth failures in logs | `kubectl -n pulse get externalsecret`; force a sync (see [set-secrets.md](set-secrets.md)), then `kubectl -n pulse rollout restart deploy/pulse-api` |
| All pods unready (ALB 503s, app metric quiet) | Target health `unhealthy`; `HTTPCode_ELB_5XX_Count` > 0 | Fix the readiness cause (DB, config); if from a deploy, revert |

## Escalation / follow-up

- Roll back immediately if the error rate started with a deploy; investigate after.
- If the cause is RDS and dev is single-AZ, note it; this is expected and is why prod is Multi-AZ.
- For the postmortem, record: start time (first 5xx), alert time, ALB vs app error counts, routes
  affected, deploy SHA involved, and SLO budget consumed.
