# Chaos drill worksheets (Phase 8)

Run each drill in **dev**. Before starting, write your own hypothesis ("I expect alert X within Y
minutes and no data loss") in the worksheet. The suggested hypotheses below are starting points
derived from the design; replace them with your own before you begin. During the drill, record UTC
timestamps as things happen. Afterwards, write a postmortem in [postmortems/](postmortems/README.md).

All commands assume the shell setup in [runbooks/README.md](runbooks/README.md#common-setup)
(`ENV`, `QURL`, `DLQURL`, `API`, `PULSE_KEY`, `promq`, `qdepth`). Keep a terminal open with:

```sh
make prometheus      # and make grafana
date -u +%FT%TZ      # run this at every step you want to timestamp
```

## Before any drill

- [ ] Dev is up, all Argo CD apps `Synced` and `Healthy` (`kubectl -n argocd get applications`).
- [ ] Alertmanager is delivering to `#pulse-platform` (the always-firing `Watchdog` alert, or a test
      alert, has arrived).
- [ ] Monitor count noted (`promq 'max(pulse_active_monitors)'`). Several hypotheses depend on it.
- [ ] Grafana "Pulse / Service overview" open.

---

## Drill 1: Workers die

**Hypothesis** (edit before starting):
Pausing KEDA at 0 replicas stops all checks. The queue grows at about `N / 60` messages per second for
N monitors at 60 s intervals. `PulseChecksStalled` fires after roughly 10 minutes (the `rate()` window
of 5 m has to empty, then the alert's `for: 5m`). `PulseCheckBacklog` fires only if visible messages
pass 500, which takes about `30,000 / N` seconds plus 5 minutes `for`; with fewer than about 9 monitors
it never fires, because 1-hour retention caps the queue at about `60 × N` messages. The status page shows
"data may be stale" after 3 minutes. No data is corrupted; checks older than 1 hour are dropped.

**Steps**

```sh
# Baseline
kubectl -n pulse get deploy pulse-worker; qdepth "$QURL"

# Inject (T0)
date -u +%FT%TZ
kubectl -n pulse annotate scaledobject pulse-worker autoscaling.keda.sh/paused-replicas="0" --overwrite
kubectl -n pulse get deploy pulse-worker -w            # wait for 0/0, then Ctrl-C

# Observe
while true; do date -u +%T; qdepth "$QURL"; sleep 60; done
promq '(sum(rate(pulse_checks_total[5m])) or vector(0))'
curl -s "$API/" | grep -i -c 'stale'                    # banner present?

# Optional: keep paused for 65+ minutes to watch retention expire messages (queue plateaus)

# Recover
date -u +%FT%TZ
kubectl -n pulse annotate scaledobject pulse-worker autoscaling.keda.sh/paused-replicas-
kubectl -n pulse get deploy pulse-worker -w
```

If Argo CD removes a manually added annotation in your setup, add it to the ScaledObject in
`pulse-gitops` instead and revert that commit to recover.

**Expected**: queue grows; stalled alert (and backlog alert if enough monitors); stale banner;
after recovery KEDA scales out, the queue drains, alerts resolve.

**Observed**: TODO

**Timestamps (UTC)**

| Event | Time |
|---|---|
| Workers paused (T0) | TODO |
| Workers at 0 | TODO |
| Stale banner visible | TODO |
| `PulseChecksStalled` fired | TODO |
| `PulseCheckBacklog` fired (if at all) | TODO |
| Recovery started | TODO |
| Queue drained / alerts resolved | TODO |

**Lessons to look for**: detection time vs the plan's "about 5–7 minutes" target; whether the stalled
alert's expression handles "no series" (`or vector(0)`); whether stale checks expired as intended.

---

## Drill 2: Bad deploy

**Hypothesis** (edit before starting):
With `PULSE_CHAOS_BREAK_READINESS=true`, the new API pod never becomes ready. Because of
`maxUnavailable: 0` and `maxSurge: 1`, the two old pods keep serving and users see no errors. The
Deployment reports `ProgressDeadlineExceeded` after its progress deadline (600 s by default), and Argo
CD marks the app `Degraded`. Recovery by `git revert` takes about Argo CD's polling interval (about
3 minutes by default without a webhook) plus one rollout.

**Steps**

```sh
# Optional: constant traffic during the drill to prove no user impact
k6 run loadtest/rollout_under_load.js        # in another terminal (check the script's target URL variable)

# Inject (T0): in pulse-gitops, set the API env var in envs/dev/values.yaml.
# The exact key depends on charts/pulse/values.yaml (for example api.env.PULSE_CHAOS_BREAK_READINESS).
cd pulse-gitops
yq -i '<path-to-api-env>.PULSE_CHAOS_BREAK_READINESS = "true"' envs/dev/values.yaml
git commit -am "chore(dev): drill 2 break readiness" && git push
date -u +%FT%TZ

# Observe
kubectl -n argocd get application pulse -w        # sync, then health Progressing → Degraded
kubectl -n pulse get pods -w | grep pulse-api
kubectl -n pulse rollout status deploy/pulse-api --timeout=15m
kubectl -n pulse get deploy pulse-api -o jsonpath='{.status.conditions}' | jq .

# Recover
date -u +%FT%TZ
git revert --no-edit HEAD && git push
kubectl -n argocd get application pulse -w        # use "Refresh" in the UI to skip the poll wait
```

**Expected**: one new pod stuck not-ready; old pods serving; no 5xx at the ALB; Argo CD `Degraded`;
recovery after revert.

**Observed**: TODO

**Timestamps (UTC)**

| Event | Time |
|---|---|
| Bad commit pushed (T0) | TODO |
| Argo CD synced | TODO |
| New pod created, not ready | TODO |
| Argo CD `Degraded` | TODO |
| Revert pushed | TODO |
| Healthy again | TODO |
| Failed requests during drill (k6) | TODO |

**Lessons to look for**: time to recover and what dominates it (polling, rollout); whether any alert
fired (should one?); how Argo CD health reflects a stuck rollout.

---

## Drill 3: Database outage

**Hypothesis** (edit before starting):
Rebooting the single-AZ dev RDS instance makes the database unavailable for a few minutes. API
`/readyz` fails, API pods leave the ALB, and visitors get 503s from the ALB (not counted in the app's
own metrics). The scheduler loop fails and enqueues nothing. Workers fail on messages already in the
queue; each failed message becomes visible again after 60 s, and after 3 receives it moves to the DLQ,
so an outage longer than about 2–3 minutes sends some checks to the DLQ and `PulseDLQNotEmpty` fires
15 minutes later. After recovery the scheduler sends overdue checks with old `scheduled_for` values,
causing a one-off schedule lag spike.

**Steps**

```sh
aws rds describe-db-instances --db-instance-identifier "pulse-$ENV-db" --query 'DBInstances[0].DBInstanceStatus'

# Inject (T0)
date -u +%FT%TZ
aws rds reboot-db-instance --db-instance-identifier "pulse-$ENV-db"

# Observe
while true; do date -u +%T; curl -s -o /dev/null -w '%{http_code}\n' "$API/readyz"; sleep 10; done
kubectl -n pulse get pods -w
aws rds wait db-instance-available --db-instance-identifier "pulse-$ENV-db"; date -u +%FT%TZ
qdepth "$DLQURL"
aws rds describe-events --source-type db-instance --source-identifier "pulse-$ENV-db" --duration 60

# Afterwards: lost checks (enqueued vs executed over the drill window)
promq 'sum(increase(pulse_scheduler_checks_enqueued_total[30m])) - sum(increase(pulse_checks_total[30m]))'
```

Then handle the DLQ per [PulseDLQNotEmpty](runbooks/PulseDLQNotEmpty.md).

**Expected**: 503s during the reboot; alerts as above; automatic recovery; possibly DLQ messages.

**Observed**: TODO

**Timestamps (UTC)**

| Event | Time |
|---|---|
| Reboot issued (T0) | TODO |
| First 503 seen | TODO |
| Alerts fired (which) | TODO |
| RDS available again | TODO |
| API serving again | TODO |
| Checks flowing again | TODO |
| DLQ messages (count) | TODO |

**Lessons to look for**: time to recovery; whether connection pools recovered on their own (stale
connections after reboot); whether checks were lost; why prod uses Multi-AZ.

---

## Drill 4: Node loss

**Hypothesis** (edit before starting):
Draining a node evicts its pods while respecting PDBs (`minAvailable: 1`), so at least one API,
scheduler and worker pod keeps running. Terminating a node is not a voluntary disruption, so PDBs do
not apply; pods on it die and are rescheduled once Kubernetes notices the node is gone. Messages in
flight on a dead worker reappear after 60 s and are processed again. Stateful pods with EBS volumes
(Prometheus, Loki) can only reschedule onto a node in the same AZ.

**Steps**

```sh
kubectl get nodes -o wide
kubectl get pods -A -o wide --field-selector spec.nodeName=<node>
kubectl -n pulse get pdb

# Option A: drain (T0)
date -u +%FT%TZ
kubectl drain <node> --ignore-daemonsets --delete-emptydir-data --timeout=5m
kubectl uncordon <node>                       # afterwards, if the node should stay

# Option B: terminate (T0); the managed node group launches a replacement
INSTANCE_ID=$(kubectl get node <node> -o jsonpath='{.spec.providerID}' | awk -F/ '{print $NF}')
date -u +%FT%TZ
aws ec2 terminate-instances --instance-ids "$INSTANCE_ID"

# Observe
kubectl get nodes -w
kubectl -n pulse get pods -o wide -w
kubectl -n monitoring get pods -o wide | grep -E 'prometheus|loki'
while true; do date -u +%T; curl -s -o /dev/null -w '%{http_code}\n' "$API/"; sleep 5; done
```

**Expected**: no user-facing errors with drain; brief capacity loss with termination; replacement node
joins; pods rebalance.

**Observed**: TODO

**Timestamps (UTC)**

| Event | Time |
|---|---|
| Drain / terminate (T0) | TODO |
| Node NotReady / gone | TODO |
| Pods rescheduled (all Running) | TODO |
| Replacement node Ready | TODO |
| Any errors seen (count) | TODO |

**Lessons to look for**: spot interruptions are this drill happening for real; PDB behaviour for
voluntary vs involuntary disruption; EBS AZ affinity for monitoring pods.

---

## Drill 5: Poison message

**Hypothesis** (edit before starting):
A malformed message fails in the worker three times, then SQS moves it to the DLQ (about 2–3 minutes
with a 60 s visibility timeout). The scheduler's DLQ gauge updates within its 15-second poll, and
`PulseDLQNotEmpty` fires 15 minutes after that. Other checks are unaffected.

**Steps**

```sh
# Inject (T0)
date -u +%FT%TZ
aws sqs send-message --queue-url "$QURL" --message-body 'this is not json'
aws sqs send-message --queue-url "$QURL" --message-body '{"monitor_id":"not-a-uuid","scheduled_for":"yesterday"}'

# Observe
while true; do date -u +%T; qdepth "$DLQURL"; sleep 30; done
promq 'max by (state) (pulse_queue_messages{queue="dlq"})'
# Loki: {namespace="pulse", pod=~"pulse-worker-.*"} | json | level="error"

# Recover: follow the runbook (inspect, then delete these messages; do not redrive)
```

Variation: a valid UUID for a monitor that does not exist should be deleted by the worker immediately
(check procedure step 1), not sent to the DLQ. Try it and confirm.

If the worker deletes unparseable messages itself instead of letting them fail, nothing reaches the
DLQ. Record which behaviour you observe and whether it is what you want.

**Expected**: messages in DLQ; alert; runbook works end to end.

**Observed**: TODO

**Timestamps (UTC)**

| Event | Time |
|---|---|
| Messages sent (T0) | TODO |
| Messages in DLQ | TODO |
| `PulseDLQNotEmpty` fired | TODO |
| Messages inspected and deleted | TODO |
| Alert resolved | TODO |

**Lessons to look for**: runbook accuracy (inspect, fix, redrive or delete); total detection time
(retries + `for`).

---

## Drill 6: Monitored site outage (product path)

**Hypothesis** (edit before starting):
With a 60 s interval and `failure_threshold = 3`, the first failed check happens within 60 s of the
switch going down, and the incident opens on the third failure about 120 s later, so the
notification arrives roughly 2–3 minutes after the switch flips. Measured from the first failed check,
that is about 2 minutes plus processing, inside the 3-minute alert-latency SLO. After `switch/up`, the
first successful check resolves the incident and a recovery message reports the downtime.

`pulse-target` must run a single replica: the switch state is in memory per pod.

**Steps**

```sh
# Monitor on the switch (skip if seeded already). Adjust the port if the Service port differs from 8080.
curl -s -X POST "$API/api/v1/monitors" -H "X-API-Key: $PULSE_KEY" -H 'Content-Type: application/json' \
  -d '{"name":"drill-6 switch","url":"http://pulse-target:8080/switch","keyword":"PULSE_OK","interval_seconds":60,"failure_threshold":3}' | jq .

kubectl -n pulse port-forward svc/pulse-target 18080:8080    # separate terminal (18080 = same host port as make target-down)

# Inject (T0)
date -u +%FT%TZ
curl -s -X POST http://localhost:18080/switch/down

# Observe
curl -s "$API/api/v1/incidents?open=true" | jq .
# status page, #pulse-alerts channel

# Recover
date -u +%FT%TZ
curl -s -X POST http://localhost:18080/switch/up
```

For exact times, read them from the database afterwards (SQL shell in the runbooks README):

```sql
SELECT started_at, notified_open_at, resolved_at, notified_resolved_at,
       notified_open_at - started_at AS alert_latency
  FROM incidents ORDER BY started_at DESC LIMIT 1;
SELECT scheduled_for, checked_at, success, error_type
  FROM check_results WHERE monitor_id = '<id>' ORDER BY scheduled_for DESC LIMIT 10;
```

**Expected**: incident opens after 3 failures; message in `#pulse-alerts`; status page red; recovery
message with duration.

**Observed**: TODO

**Timestamps (UTC)**

| Event | Time |
|---|---|
| Switch down (T0) | TODO |
| First failed check (`checked_at`) | TODO |
| Incident opened (`started_at` is the first failure) | TODO |
| Notification sent (`notified_open_at`) | TODO |
| Switch up | TODO |
| Incident resolved + recovery message | TODO |

**Lessons to look for**: detection time vs the alert-latency SLO; how the interval and threshold set
a floor on detection time.

---

## Drill 7 (optional): Noisy monitors

**Hypothesis** (edit before starting):
500 monitors on `/slow?ms=30000` at a 30 s interval need about 500 concurrent checks
(Little's law: 500 / 30 s ≈ 16.7 checks/s × 30 s each). Maximum worker capacity is 10 pods × 50 =
500 slots, so workers saturate at max replicas and schedule lag rises for **all** monitors, including
healthy ones. Messages that wait in a worker for a free slot keep their visibility clock running; if
waiting plus checking exceeds 60 s, SQS redelivers them (duplicates, receive counts rising, possibly
DLQ).

**Steps**

```sh
# Inject (T0)
date -u +%FT%TZ
for i in $(seq 1 500); do
  curl -s -o /dev/null -w '%{http_code} ' -X POST "$API/api/v1/monitors" \
    -H "X-API-Key: $PULSE_KEY" -H 'Content-Type: application/json' \
    -d "{\"name\":\"noisy-$i\",\"url\":\"http://pulse-target:8080/slow?ms=30000\",\"interval_seconds\":30,\"timeout_ms\":30000}"
done; echo

# Observe
promq 'histogram_quantile(0.95, sum by (le) (rate(pulse_check_schedule_lag_seconds_bucket[5m])))'
promq 'sum(pulse_worker_inflight_checks)'
kubectl -n pulse get hpa,scaledobject
qdepth "$QURL"; qdepth "$DLQURL"

# Clean up (the list response shape may differ; adjust the jq path)
curl -s -H "X-API-Key: $PULSE_KEY" "$API/api/v1/monitors" \
  | jq -r '(.items? // .)[] | select(.name | startswith("noisy-")) | .id' \
  | while read -r id; do
      curl -s -o /dev/null -X DELETE -H "X-API-Key: $PULSE_KEY" "$API/api/v1/monitors/$id"
    done
```

**Expected**: lag rises for everyone; KEDA at max; possibly backlog and DLQ.

**Observed**: TODO

**Timestamps (UTC)**

| Event | Time |
|---|---|
| Monitors created (T0) | TODO |
| Workers at max | TODO |
| `PulseScheduleLagHigh` fired | TODO |
| Monitors deleted | TODO |
| Lag back to normal | TODO |

**Lessons to look for**: fairness between monitors; per-check timeout caps; concurrency tuning;
separate queues for slow monitors; visibility timeout vs time spent waiting for a slot.
