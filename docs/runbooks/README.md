# Runbooks

One runbook per alert, named after the alert. Each alert's `runbook_url` annotation points to
`https://github.com/arham3117/Pulse/blob/main/docs/runbooks/<AlertName>.md`.
Format: [Appendix D of the plan](../../plan/plan.md#appendix-d-runbook-template-docsrunbooksalertnamemd).

## Alert runbooks

| Alert | Severity | For | Runbook |
|---|---|---|---|
| `PulseAPIHighErrorRate` | critical | 5m | [PulseAPIHighErrorRate.md](PulseAPIHighErrorRate.md) |
| `PulseAPIHighLatency` | warning | 10m | [PulseAPIHighLatency.md](PulseAPIHighLatency.md) |
| `PulseCheckBacklog` | critical | 5m | [PulseCheckBacklog.md](PulseCheckBacklog.md) |
| `PulseChecksStalled` | critical | 5m | [PulseChecksStalled.md](PulseChecksStalled.md) |
| `PulseScheduleLagHigh` | warning | 10m | [PulseScheduleLagHigh.md](PulseScheduleLagHigh.md) |
| `PulseDLQNotEmpty` | warning | 15m | [PulseDLQNotEmpty.md](PulseDLQNotEmpty.md) |
| `PulseSchedulerDown` | critical | 2m | [PulseSchedulerDown.md](PulseSchedulerDown.md) |
| `PulsePodCrashLooping` | warning | 0m | [PulsePodCrashLooping.md](PulsePodCrashLooping.md) |
| `PulseSLOFastBurn` | critical | — | [PulseSLOFastBurn.md](PulseSLOFastBurn.md) |
| `PulseSLOSlowBurn` | warning | — | [PulseSLOSlowBurn.md](PulseSLOSlowBurn.md) |

## Operational runbooks

| Runbook | Use when |
|---|---|
| [set-secrets.md](set-secrets.md) | After `make infra-up` on a fresh environment, or to rotate the admin key or a webhook |
| [teardown.md](teardown.md) | Ending a session (`make infra-down`), and verifying nothing billable is left |

## Common setup

Every runbook assumes this shell setup. Names come from [CONTRACT.md](../CONTRACT.md).

```sh
export AWS_PROFILE=pulse AWS_REGION=us-east-2 ENV=dev
aws sso login --profile pulse            # if the session expired
make kubeconfig ENV=$ENV                 # aws eks update-kubeconfig for cluster pulse-$ENV

QURL=$(aws sqs get-queue-url --queue-name "pulse-$ENV-checks" --query QueueUrl --output text)
DLQURL=$(aws sqs get-queue-url --queue-name "pulse-$ENV-checks-dlq" --query QueueUrl --output text)

# Public API/status page via the ALB (use https:// if an ACM certificate is configured)
API="http://$(kubectl -n pulse get ingress -o jsonpath='{.items[0].status.loadBalancer.ingress[0].hostname}')"
# Admin key for write endpoints, loaded without printing it
PULSE_KEY="$(aws secretsmanager get-secret-value --secret-id "pulse/$ENV/app" \
  --query SecretString --output text | jq -r .admin_api_key)"

# Port-forwards (each in its own terminal). Adjust ports if the Makefile uses different ones.
make prometheus    # http://localhost:9090
make grafana       # http://localhost:3000
make argocd-ui

# Instant PromQL query from the shell
promq() {
  curl -s http://localhost:9090/api/v1/query --data-urlencode "query=$1" \
    | jq -r '.data.result[] | "\(.metric | del(.__name__) | tostring)  \(.value[1])"'
}

# SQS depth (visible, in flight, delayed)
qdepth() {
  aws sqs get-queue-attributes --queue-url "$1" \
    --attribute-names ApproximateNumberOfMessages ApproximateNumberOfMessagesNotVisible ApproximateNumberOfMessagesDelayed \
    --query Attributes --output table
}
```

### Quick health snapshot

```sh
kubectl -n pulse get deploy,pods,hpa,scaledobject,pdb -o wide
kubectl -n pulse get events --sort-by=.lastTimestamp | tail -30
kubectl -n argocd get applications
kubectl get nodes -o wide
qdepth "$QURL"; qdepth "$DLQURL"
```

### Logs (Loki in Grafana Explore)

Selectors use pod name prefixes so they do not depend on container names in the chart.

```logql
{namespace="pulse", pod=~"pulse-api-.*"} | json | level="error"
{namespace="pulse", pod=~"pulse-worker-.*"} | json | level="error"
{namespace="pulse", pod=~"pulse-scheduler-.*"} | json | level="error"
# Follow one request across services
{namespace="pulse"} | json | request_id="<id from X-Request-ID>"
# Error counts per pod over time
sum by (pod) (count_over_time({namespace="pulse"} | json | level="error" [5m]))
```

Without Loki: `kubectl -n pulse logs deploy/pulse-worker --since=15m | jq -c 'select(.level=="error")'`.

### Recent deploys

```sh
# What Argo CD deployed, and when
kubectl -n argocd get application pulse \
  -o jsonpath='{range .status.history[*]}{.deployedAt}{"  "}{.revision}{"\n"}{end}'
# What changed in the config repo
git -C pulse-gitops log --oneline -10 -- envs/$ENV charts/pulse
```

### Rolling back

Argo CD runs with `selfHeal`, so `kubectl rollout undo` or `kubectl edit` on a managed field is
reverted within minutes. Roll back in git:

```sh
cd pulse-gitops
git revert <bad-commit-sha>      # usually the "chore(dev): deploy sha-xxxxxxx" commit
git push
# Watch it land
kubectl -n argocd get application pulse -w
kubectl -n pulse rollout status deploy/pulse-api
```

### SQL shell against RDS

The `pulse` namespace enforces the `restricted` Pod Security Standard, so a debug pod must be
non-root with a locked-down security context. This pod reads credentials from the `pulse-db` Secret,
so the password never appears in your shell or history.

```sh
DBHOST=$(aws rds describe-db-instances --db-instance-identifier "pulse-$ENV-db" \
  --query 'DBInstances[0].Endpoint.Address' --output text)

kubectl -n pulse apply -f - <<EOF
apiVersion: v1
kind: Pod
metadata:
  name: psql-debug
spec:
  restartPolicy: Never
  securityContext:
    runAsNonRoot: true
    runAsUser: 70          # postgres user in the alpine image
    seccompProfile: {type: RuntimeDefault}
  containers:
    - name: psql
      image: postgres:17-alpine   # match the RDS major version
      command: ["sleep", "3600"]
      env:
        - {name: PGHOST, value: "$DBHOST"}
        - {name: PGDATABASE, value: pulse}
        - {name: PGSSLMODE, value: require}
        - {name: HOME, value: /tmp}
        - name: PGUSER
          valueFrom: {secretKeyRef: {name: pulse-db, key: username}}
        - name: PGPASSWORD
          valueFrom: {secretKeyRef: {name: pulse-db, key: password}}
      securityContext:
        allowPrivilegeEscalation: false
        readOnlyRootFilesystem: true
        capabilities: {drop: [ALL]}
      resources:
        requests: {cpu: 50m, memory: 64Mi}
        limits: {memory: 128Mi}
      volumeMounts: [{name: tmp, mountPath: /tmp}]
  volumes: [{name: tmp, emptyDir: {}}]
EOF
kubectl -n pulse wait --for=condition=Ready pod/psql-debug --timeout=60s
kubectl -n pulse exec -it psql-debug -- psql
# when done
kubectl -n pulse delete pod psql-debug
```

If NetworkPolicies are enabled in `pulse`, this pod also needs an egress allowance to RDS on 5432.

Useful queries:

```sql
-- Due backlog in the database (scheduler side)
SELECT count(*) AS due, max(now() - next_check_at) AS oldest_overdue
  FROM monitors WHERE is_active AND next_check_at <= now();

-- Active sessions, longest running first
SELECT pid, state, wait_event_type, wait_event, now() - query_start AS running_for, left(query, 100)
  FROM pg_stat_activity WHERE datname = 'pulse' ORDER BY running_for DESC NULLS LAST;

-- Blocked sessions and who blocks them
SELECT pid, pg_blocking_pids(pid) AS blocked_by, left(query, 100)
  FROM pg_stat_activity WHERE cardinality(pg_blocking_pids(pid)) > 0;

-- Connection count vs limit
SELECT count(*) AS connections, current_setting('max_connections') AS max FROM pg_stat_activity;

-- Size of the biggest table
SELECT pg_size_pretty(pg_total_relation_size('check_results'));

-- Open incidents
SELECT monitor_id, started_at, first_error FROM incidents WHERE resolved_at IS NULL;
```

## Writing or updating a runbook

- Keep the Appendix D structure. Commands must be copy-pasteable with the common setup above.
- After every drill or real incident, fix whatever in the runbook was wrong or missing.
