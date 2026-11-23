# PulseSLOSlowBurn

## What it means

An SLO error budget is burning at more than 6 times the sustainable rate over both the last 6 hours
and the last 30 minutes. At this rate the 30-day budget is gone in about 5 days, and 5% of it has
been spent in the last 6 hours.

| SLO | Fires when |
|---|---|
| API availability (99.5%) | 5xx ratio > 3% over 6 h and over 30 m |
| Check freshness (99%) | share of checks later than 90 s > 6% over 6 h and over 30 m |

Definitions and PromQL are in [slo.md](../slo.md).

## Impact

A smaller but sustained problem: some requests fail or some checks run late, persistently. It is not
an emergency, but left alone it will consume the month's budget.

## First checks (5 minutes)

1. Which SLO is burning?

   ```sh
   promq 'pulse:slo_api_errors:ratio_rate30m'
   promq 'pulse:slo_api_errors:ratio_rate6h'
   promq 'pulse:slo_check_late:ratio_rate30m'
   promq 'pulse:slo_check_late:ratio_rate6h'
   ```

2. Grafana "Pulse / SLOs": when did the burn start? Is it steady or periodic (for example aligned
   with the daily retention CronJob or spot interruptions)?
3. For availability, find the failing routes:
   `promq 'sum by (route, status) (increase(pulse_http_requests_total{status=~"5.."}[6h]))'`
4. For freshness, find the contributing stage (scheduler, queue, execution), see
   [PulseScheduleLagHigh](PulseScheduleLagHigh.md).
5. Argo CD and `git -C pulse-gitops log --since=12.hours`: any change in the window?

## Likely causes and fixes

| Cause | How to confirm | Fix |
|---|---|---|
| One route erroring intermittently | 5xx concentrated on one route | Fix the bug; add a test |
| Periodic job causing load (retention CronJob) | Burn aligns with CronJob schedule; DB CPU spikes | Smaller batches, off-peak schedule, or partitioning |
| Workers slightly under-provisioned | Lag rises at peaks; KEDA at max | Raise max replicas or concurrency; revisit KEDA target per pod |
| A few slow monitors dragging others (noisy neighbours) | Execution stage dominates lag; specific monitors time out | Pause/fix those monitors; per-check timeout caps |
| Spot interruptions | Node events, pods rescheduling | Expected in dev; verify PDBs and graceful shutdown |

## Escalation / follow-up

- Warning severity: handle during working hours. Create an issue with the evidence.
- If it escalates to [PulseSLOFastBurn](PulseSLOFastBurn.md), treat it as an incident.
- Record the budget consumed and the cause; slow burns are good material for a postmortem because
  they reveal capacity or design limits rather than outages.
