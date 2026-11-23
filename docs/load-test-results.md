# Load test results

This document records the k6 load tests from Phase 7.3: method, environment, results, and one
bottleneck found and fixed with before/after measurements. All result cells are `TODO` until the tests
are run; nothing here is estimated.

Only ever load-test endpoints you own: Pulse itself and `pulse-target`.

## Environment

Record the exact setup for each run, since results are meaningless without it.

| Item | Value |
|---|---|
| Date | TODO |
| Environment | dev |
| Image tag | TODO (`sha-xxxxxxx`) |
| Node group | TODO (instance types, count at start, spot/on-demand) |
| RDS class | TODO |
| API HPA | min 2, max 6, CPU 70% |
| Worker KEDA | min 1, max 10, ~50 messages per pod |
| `PULSE_WORKER_CONCURRENCY` | TODO |
| DB pool size per pod | TODO |
| Where k6 ran | TODO (laptop over the internet, or in-cluster; in-cluster removes internet latency from the numbers) |
| k6 version | TODO |

## Method

- Run each scenario from a known starting state (same monitor count, nodes scaled back down).
- Watch Grafana "Pulse / Service overview" during the run; take screenshots at steady state and at
  peak. Save them in `docs/images/` and link them below.
- Record k6's end-of-test summary (`http_req_duration` percentiles, `http_req_failed`, iterations).
- Change one thing at a time when tuning, and re-run the same scenario to compare.

PromQL used for the tables:

```promql
# API p95 latency
histogram_quantile(0.95, sum by (le) (rate(pulse_http_request_duration_seconds_bucket[5m])))
# API error ratio
sum(rate(pulse_http_requests_total{status=~"5.."}[5m])) / sum(rate(pulse_http_requests_total[5m]))
# Checks per second
sum(rate(pulse_checks_total[1m]))
# Schedule lag p95
histogram_quantile(0.95, sum by (le) (rate(pulse_check_schedule_lag_seconds_bucket[5m])))
# Queue depth
max by (state) (pulse_queue_messages{queue="checks"})
# Worker replicas
kube_deployment_status_replicas{namespace="pulse", deployment="pulse-worker"}
# API replicas
kube_deployment_status_replicas{namespace="pulse", deployment="pulse-api"}
# Nodes
count(kube_node_info)
```

## Scenario A: API read load (`loadtest/api_read.js`)

50 virtual users hitting `/` and `/api/v1/monitors/{id}/stats` for 10 minutes. Script thresholds:
p95 < 300 ms, errors < 0.5%.

```sh
make loadtest-read
```

| Metric | Result |
|---|---|
| Requests total / per second | TODO |
| p50 / p95 / p99 latency | TODO |
| Error rate | TODO |
| Thresholds passed? | TODO |
| API replicas (start → peak) | TODO |
| API CPU at peak (vs request) | TODO |
| RDS CPU at peak | TODO |

Observations: TODO (did the HPA react, how long it took, what limited throughput).

Screenshots: TODO

## Scenario B: check storm (`loadtest/seed_monitors.py`)

2,000 monitors pointing at `pulse-target` (mix of `/ok` and `/slow?ms=500`) with 30-second
intervals, which is 2,000 / 30 ≈ 67 checks/second by design. Delete the monitors afterwards.

```sh
make loadtest-storm
```

| Metric | Result |
|---|---|
| Target checks/sec | ~67 (by configuration) |
| Achieved checks/sec (steady state) | TODO |
| Schedule lag p95 (steady state / peak) | TODO |
| Peak visible queue depth | TODO |
| Worker replicas (start → peak → end) | TODO |
| Time for KEDA to scale out | TODO |
| Nodes (start → peak), Cluster Autoscaler activity | TODO |
| Pods Pending at any point? For how long? | TODO |
| Scale-in after monitors deleted (time to min replicas) | TODO |
| DB CPU / connections at peak | TODO |

Observations: TODO

Screenshots: TODO (queue depth + worker replicas on one panel is the key image)

### Pending pods without node autoscaling

Plan 7.2 asks to reproduce pods stuck in `Pending` once on purpose (Cluster Autoscaler disabled or
node group max reached).

| Item | Result |
|---|---|
| How it was reproduced | TODO |
| Pending pods and events (`Insufficient cpu`/`memory`) | TODO |
| Effect on schedule lag | TODO |

## Scenario C: rollout under load (`loadtest/rollout_under_load.js`)

Constant traffic while a new version is deployed through Argo CD. Expectation: zero failed requests
(`maxUnavailable: 0`, `maxSurge: 1`, readiness gate on `/readyz`).

| Metric | Result |
|---|---|
| Image tag before → after | TODO |
| Rollout duration | TODO |
| Requests during rollout | TODO |
| Failed requests | TODO |
| p95 during rollout vs before | TODO |

Observations: TODO (if any requests failed, why: connection draining, ALB deregistration delay vs
pod shutdown, preStop hook)

## Bottleneck found and fixed

One change, measured before and after with the same scenario.

| Item | Value |
|---|---|
| Symptom | TODO |
| How it was found (metric, query plan, profile) | TODO |
| Hypothesis | TODO |
| Change made (PR link) | TODO |
| Scenario used to measure | TODO |

| Metric | Before | After |
|---|---|---|
| TODO (for example schedule lag p95) | TODO | TODO |
| TODO (for example achieved checks/sec) | TODO | TODO |
| TODO (for example DB CPU) | TODO | TODO |

Likely candidates to investigate: database connection pool size vs replicas vs `max_connections`;
single-row inserts of check results; missing or unused indexes (`EXPLAIN ANALYZE` on the stats query
and the scheduler query); worker concurrency; scheduler batch size.

Screenshots (before/after): TODO

## Conclusions

TODO: what limits Pulse first, at what load, and what you would change next.
