# PulseSLOFastBurn

## What it means

An SLO error budget is burning at more than 14.4 times the sustainable rate over both the last hour
and the last 5 minutes. At this rate the 30-day budget is gone in about 50 hours, and 2% of it has
already been spent in the last hour.

| SLO | Fires when |
|---|---|
| API availability (99.5%) | 5xx ratio > 7.2% over 1 h and over 5 m |
| Check freshness (99%) | share of checks later than 90 s > 14.4% over 1 h and over 5 m |

Definitions and PromQL are in [slo.md](../slo.md).

## Impact

A significant, ongoing user-facing problem: either the status page/API is failing for a large share
of requests, or a large share of checks are running late (outages detected late or not at all).

## First checks (5 minutes)

1. Which SLO is burning? Check the alert's `slo` label if present, otherwise query both:

   ```sh
   promq 'pulse:slo_api_errors:ratio_rate5m'
   promq 'pulse:slo_api_errors:ratio_rate1h'
   promq 'pulse:slo_check_late:ratio_rate5m'
   promq 'pulse:slo_check_late:ratio_rate1h'
   ```

   (If the recording rule names differ in the chart, use the raw expressions from
   [slo.md](../slo.md).)
2. Grafana "Pulse / SLOs": burn rate and budget remaining panels.
3. Go to the symptom runbook:
   - availability burning → [PulseAPIHighErrorRate](PulseAPIHighErrorRate.md)
   - freshness burning → [PulseScheduleLagHigh](PulseScheduleLagHigh.md),
     [PulseCheckBacklog](PulseCheckBacklog.md), [PulseChecksStalled](PulseChecksStalled.md)
4. `kubectl -n pulse get pods` and Argo CD history: anything deployed in the last hour?

## Likely causes and fixes

| Cause | How to confirm | Fix |
|---|---|---|
| Bad deploy | Burn starts at a deploy time | `git revert` in `pulse-gitops` |
| Database outage | RDS status not `available` | Wait for recovery; see [PulseAPIHighErrorRate](PulseAPIHighErrorRate.md) |
| Workers down or saturated (freshness) | Queue depth rising; workers 0 or at max | [PulseCheckBacklog](PulseCheckBacklog.md) |
| Very low traffic makes the ratio noisy (dev) | A handful of 5xx out of very few requests | Confirm absolute counts: `sum(increase(pulse_http_requests_total{status=~"5.."}[1h]))`; consider a minimum-traffic condition in the rule |
| Load test or drill in progress | k6 or drill running | Expected; note it in the drill worksheet |

## Escalation / follow-up

- This is a paging alert. Mitigate first (roll back, scale out), then investigate.
- Record budget consumed during the incident (from the SLO dashboard) in the postmortem.
- If the budget is exhausted for the window, prioritise reliability work over features.
