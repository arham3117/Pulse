# PulsePodCrashLooping

## What it means

A container in namespace `pulse` restarted more than 3 times in 15 minutes:

```promql
increase(kube_pod_container_status_restarts_total{namespace="pulse"}[15m]) > 3
```

The alert's `pod` and `container` labels say which one. A container that keeps restarting is either
crashing (non-zero exit), being OOM-killed, or failing its liveness probe.

## Impact

Depends on the component and how many replicas are affected:

| Component | One replica looping | All replicas looping |
|---|---|---|
| `pulse-api` | Reduced capacity; other replica serves | Status page and API down (ALB 503) |
| `pulse-scheduler` | Other replica schedules everything | No checks enqueued ([PulseSchedulerDown](PulseSchedulerDown.md)) |
| `pulse-worker` | Lower throughput; in-flight messages redelivered after 60 s | Checks stop ([PulseChecksStalled](PulseChecksStalled.md)) |
| `pulse-target` | Chaos target monitors fail (test-only) | Same |

## First checks (5 minutes)

1. Which pods, and why did the last run end?

   ```sh
   kubectl -n pulse get pods -o wide
   kubectl -n pulse get pod <pod> -o jsonpath='{range .status.containerStatuses[*]}{.name}{"  restarts="}{.restartCount}{"  reason="}{.lastState.terminated.reason}{"  exit="}{.lastState.terminated.exitCode}{"\n"}{end}'
   kubectl -n pulse describe pod <pod> | sed -n '/Events:/,$p'
   ```

   `reason=OOMKilled` (exit 137) means memory limit; `Error` with exit 1 means the process crashed;
   events with `Liveness probe failed` mean Kubernetes killed it.
2. Logs from the crashed run:

   ```sh
   kubectl -n pulse logs <pod> -c <container> --previous --tail=200
   ```

   Loki: `{namespace="pulse", pod="<pod>"} | json | level=~"error|critical"`.
3. Grafana "Pulse / Service overview" for the affected component; memory usage vs limit:

   ```sh
   promq 'max by (pod) (container_memory_working_set_bytes{namespace="pulse", container!=""})'
   promq 'max by (pod) (kube_pod_container_resource_limits{namespace="pulse", resource="memory"})'
   ```

4. Argo CD: did this start with a deploy? Did the migration PreSync Job succeed?

   ```sh
   kubectl -n pulse get jobs --sort-by=.metadata.creationTimestamp | tail -5
   ```

## Likely causes and fixes

| Cause | How to confirm | Fix |
|---|---|---|
| Bad deploy (exception at startup) | Started at deploy time; traceback in `--previous` logs | `git revert` in `pulse-gitops` |
| Config/secret problem | pydantic-settings validation error naming a `PULSE_` variable; DB auth failures | Fix values or secret ([set-secrets.md](set-secrets.md)); restart after the secret syncs |
| OOMKilled | `lastState.terminated.reason=OOMKilled`; working set near limit | Raise memory limit in values; look for a leak (worker response bodies are capped at 1 MB, check that holds) |
| Liveness probe too strict | Events show liveness failures; process logs look healthy; often under load | Liveness should only check the process (`/healthz`), never dependencies; increase `timeoutSeconds`/`failureThreshold` |
| Read-only filesystem write | `Read-only file system` / `Permission denied` in logs | Write only to `/tmp` (emptyDir); fix the code or library config |
| Schema mismatch after failed migration | Errors about missing columns/tables; migration Job failed | Check the Job logs; fix migration; migrations must be backward compatible (expand/contract) |
| Database unavailable at startup | Connection errors; RDS rebooting | Should recover on its own; consider retrying DB connection at startup rather than exiting |

## Escalation / follow-up

- If all replicas of a component are looping, handle it as that component's outage (see the table in
  Impact) and roll back first.
- `CrashLoopBackOff` delays grow up to 5 minutes between restarts, so recovery after a fix can lag;
  `kubectl -n pulse rollout restart deploy/<name>` restarts immediately.
- Record: component, restart count, termination reason, first restart time, and the fix.
