# Service level objectives

This document defines Pulse's SLOs, how each SLI is measured in Prometheus, the error budget each
one allows, and the burn-rate alerts that page or warn when the budget is being spent too fast.

Metric names come from [CONTRACT.md](CONTRACT.md). Alert names match the runbooks in
[runbooks/](runbooks/README.md).

## SLOs

| SLO | SLI | Target | Window |
|---|---|---|---|
| Status API availability | Share of API requests that are not 5xx | 99.5% | 30 days rolling |
| Check freshness | Share of checks executed within 90 s of `scheduled_for` | 99% | 30 days rolling |
| Alert latency (stretch) | Share of incidents notified within 3 minutes of the first failed check | 95% | 30 days rolling |

Why these: availability is what visitors of the status page experience; freshness is what makes
Pulse useful as a monitor (a check that runs 10 minutes late is a missed check); alert latency is
the end-to-end promise to users who rely on notifications.

## SLI definitions (PromQL)

### Status API availability

```promql
# Good-event ratio over 5m (1 = perfect). Health, readiness and metrics endpoints are excluded:
# probes and scrapes are frequent and almost always succeed, so they would dilute real errors.
1 - (
  sum(rate(pulse_http_requests_total{status=~"5..", route!~"/healthz|/readyz|/metrics"}[5m]))
  /
  sum(rate(pulse_http_requests_total{route!~"/healthz|/readyz|/metrics"}[5m]))
)
```

Limitations:

- This is measured at the application. Requests the ALB rejects itself (502/503/504 when no target is
  healthy) never reach a pod and are not counted. During a total API outage the app-side SLI can look
  fine because the denominator drops to zero. Cross-check with the ALB's CloudWatch metrics
  `HTTPCode_ELB_5XX_Count` and `HTTPCode_Target_5XX_Count`, and with the external uptime check
  (see [architecture.md](architecture.md#who-monitors-the-monitor)).
- If there is no traffic, the ratio is undefined (no data), not 100%.

### Check freshness

`pulse_check_schedule_lag_seconds` is a histogram of `checked_at - scheduled_for`. The SLI is the
fraction of observations that fall in the `le="90"` bucket.

```promql
sum(rate(pulse_check_schedule_lag_seconds_bucket{le=~"90|90.0"}[5m]))
/
sum(rate(pulse_check_schedule_lag_seconds_count[5m]))
```

Requirements and caveats:

- **The histogram must have a bucket boundary at exactly 90 seconds.** A histogram can only answer
  "how many observations were ≤ X" for X equal to a bucket boundary. CONTRACT.md says buckets go up
  to 600 s; check that 90 is one of them in the worker's metric definition (for example
  `(1, 5, 10, 30, 60, 90, 120, 300, 600)`). Without it, this SLI cannot be computed exactly.
- **Label formatting.** The Python client renders bucket bounds as floats (`le="90.0"`), and
  Prometheus 3.x normalises `le` values to float form on ingestion. The matcher `le=~"90|90.0"`
  works either way. Confirm with `count by (le) (pulse_check_schedule_lag_seconds_bucket)`.
- **Checks that never run are invisible.** The histogram only records checks a worker executed.
  Messages that expired after the 1-hour retention or went to the DLQ produce no observation, so this
  SLI overstates freshness during a worker outage. Compare enqueued and executed rates to see the gap:

  ```promql
  sum(rate(pulse_scheduler_checks_enqueued_total[1h])) - sum(rate(pulse_checks_total[1h]))
  ```

  `PulseChecksStalled` and `PulseCheckBacklog` cover the "nothing is running" case directly.

### Alert latency (stretch)

Not measurable from current metrics. It needs either a histogram observed when an
`incident_opened` notification succeeds (`notified_open_at - started_at`), or a SQL query over the
`incidents` table:

```sql
SELECT count(*) FILTER (WHERE notified_open_at - started_at <= interval '3 minutes')::float
       / nullif(count(*), 0)
  FROM incidents
 WHERE started_at > now() - interval '30 days';
```

Note the floor this SLO sits on: with a 60 s interval and `failure_threshold = 3`, the third failed
check happens about 120 s after the first one, before any queueing or notification time.

## Error budgets

Error budget = `1 - target`, applied to the window.

| SLO | Target | Budget (fraction) | Budget over 30 days (720 h) |
|---|---|---|---|
| API availability | 99.5% | 0.5% of requests | Equivalent to 3.6 h = **3 h 36 m** of total outage |
| Check freshness | 99% | 1% of checks may be late | Equivalent to 7.2 h = **7 h 12 m** of all checks late |
| Alert latency | 95% | 5% of incidents may be notified late | n/a (event-based, low volume) |

The time equivalents assume uniform traffic. For an event-based SLI the budget is really a count:
for example, 100 monitors at a 60 s interval produce `100 × 1,440 × 30 = 4,320,000` checks in
30 days, so 1% is 43,200 late checks.

Budget remaining (30-day window), as a Grafana stat panel:

```promql
# API availability: 1 = full budget left, 0 = exhausted, negative = SLO missed
1 - (
  (sum(increase(pulse_http_requests_total{status=~"5..", route!~"/healthz|/readyz|/metrics"}[30d]))
   / sum(increase(pulse_http_requests_total{route!~"/healthz|/readyz|/metrics"}[30d])))
  / 0.005
)
```

Prometheus retention in dev is 7 days, so a 30-day query only covers what is stored. Treat dev
numbers as illustrative; a real 30-day budget needs longer retention (a stretch goal).

## Burn-rate alerting

Burn rate is how fast the budget is being consumed relative to the rate that would use exactly
100% of it by the end of the window.

```
burn rate = observed error ratio / (1 - SLO target)
```

A burn rate of 1 spends the whole budget in 30 days. A burn rate of 14.4 spends it in
720 h / 14.4 = 50 h.

### Thresholds (from the Google SRE Workbook, multiwindow multi-burn-rate)

| Alert | Burn rate | Long window | Short window | Budget spent when it fires | Severity |
|---|---|---|---|---|---|
| `PulseSLOFastBurn` | 14.4× | 1 h | 5 m | 14.4 × 1 h / 720 h = **2%** | critical (page) |
| `PulseSLOSlowBurn` | 6× | 6 h | 30 m | 6 × 6 h / 720 h = **5%** | warning |

Error-ratio thresholds these translate to:

| SLO | Budget | Fast burn threshold (14.4×) | Slow burn threshold (6×) |
|---|---|---|---|
| API availability 99.5% | 0.005 | error ratio > 0.072 (7.2%) | error ratio > 0.03 (3%) |
| Check freshness 99% | 0.01 | late ratio > 0.144 (14.4%) | late ratio > 0.06 (6%) |

### Why burn rate instead of a raw error-rate threshold

- **It is tied to what matters.** A raw "5xx > 1%" alert has no relation to the SLO. Burn rate says
  how much of the agreed unreliability is being spent and how soon it runs out.
- **Fast burn catches big outages quickly**; slow burn catches smaller, sustained problems that a
  short-window alert would ignore and that still threaten the monthly target.
- **Two windows per alert.** The long window (1 h / 6 h) gives significance: the problem is real,
  not a blip. The short window (5 m / 30 m) must also be burning, so the alert resolves soon after
  the problem stops instead of staying red for an hour while the long window catches up.
- **Low-traffic caveat.** With little traffic a handful of errors produces a huge ratio. In dev,
  expect noisy burn alerts unless there is steady synthetic traffic.

`PulseAPIHighErrorRate` (5xx > 5% for 5 m) overlaps with the fast-burn alert for availability.
It is kept as a simpler, direct symptom alert. If both fire, handle the incident once using
[PulseAPIHighErrorRate](runbooks/PulseAPIHighErrorRate.md).

### Recording rules (reference)

Recording rules keep alert expressions cheap and consistent. These names are a suggested convention;
the authoritative rules live in the chart's PrometheusRule template in `pulse-gitops/charts/pulse/`.
If names differ there, the chart wins and this section should be updated.

```yaml
groups:
  - name: pulse-slo-recording
    rules:
      # API availability: error ratio per window
      - record: pulse:slo_api_errors:ratio_rate5m
        expr: |
          sum(rate(pulse_http_requests_total{status=~"5..", route!~"/healthz|/readyz|/metrics"}[5m]))
          / sum(rate(pulse_http_requests_total{route!~"/healthz|/readyz|/metrics"}[5m]))
      # ...repeat for 30m, 1h, 6h windows

      # Check freshness: late ratio per window
      - record: pulse:slo_check_late:ratio_rate5m
        expr: |
          1 - (
            sum(rate(pulse_check_schedule_lag_seconds_bucket{le=~"90|90.0"}[5m]))
            / sum(rate(pulse_check_schedule_lag_seconds_count[5m]))
          )
      # ...repeat for 30m, 1h, 6h windows

  - name: pulse-slo-alerts
    rules:
      - alert: PulseSLOFastBurn
        expr: |
          (pulse:slo_api_errors:ratio_rate1h > 14.4 * 0.005 and pulse:slo_api_errors:ratio_rate5m > 14.4 * 0.005)
          or
          (pulse:slo_check_late:ratio_rate1h > 14.4 * 0.01 and pulse:slo_check_late:ratio_rate5m > 14.4 * 0.01)
        labels:
          severity: critical
      - alert: PulseSLOSlowBurn
        expr: |
          (pulse:slo_api_errors:ratio_rate6h > 6 * 0.005 and pulse:slo_api_errors:ratio_rate30m > 6 * 0.005)
          or
          (pulse:slo_check_late:ratio_rate6h > 6 * 0.01 and pulse:slo_check_late:ratio_rate30m > 6 * 0.01)
        labels:
          severity: warning
```

A practical refinement: add an `slo` label (`api-availability` or `check-freshness`) by splitting
each alert into one rule per SLO, so the notification says which budget is burning.

## Review

- Review SLOs monthly: were they met, how much budget was used, and which incidents used it.
- If the budget is exhausted, prioritise reliability work over features until it recovers.
- TODO: after running in dev, record observed SLI values here with the date range and a Grafana
  screenshot. Do not fill this in from estimates.
