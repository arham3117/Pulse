# Postmortems

Postmortems are written **after** running the Phase 8 chaos drills in dev (or after a real incident).
None exist yet: there is nothing to write until a drill has been run and its timeline recorded.
Do not write a postmortem from expectations; write it from what was observed.

## Process

1. Before a drill, fill in the hypothesis in the drill's worksheet in [../drills.md](../drills.md).
2. Run the drill and record timestamps (UTC) as things happen: fault injected, alert fired,
   investigation started, mitigation, recovery.
3. Within a day, copy [TEMPLATE.md](TEMPLATE.md) to `YYYY-MM-DD-short-title.md` in this folder and
   write it up, in your own words. Keep it blameless: describe how the system and process allowed the
   problem, not who did what wrong.
4. Every action item gets an issue or PR link. At least one must become a real change (code, alert,
   runbook or infrastructure) and be merged, so the loop incident → postmortem → improvement is
   visible.
5. Update the runbook used during the drill with anything that was wrong or missing.

Goal for Phase 8: at least 3 postmortems.

## Drills

| # | Drill | How | Main alerts expected | Postmortem |
|---|---|---|---|---|
| 1 | Workers die | Pause KEDA at 0 replicas (`autoscaling.keda.sh/paused-replicas: "0"`) | `PulseCheckBacklog`, `PulseChecksStalled` | TODO |
| 2 | Bad deploy | `PULSE_CHAOS_BREAK_READINESS=true` in dev values via Argo CD | Argo CD `Degraded`; no user impact expected | TODO |
| 3 | Database outage | `aws rds reboot-db-instance` (single-AZ dev) | `PulseAPIHighErrorRate`, `PulseChecksStalled`, possibly `PulseDLQNotEmpty` | TODO |
| 4 | Node loss | Drain or terminate one worker node | Possibly `PulsePodCrashLooping`; ideally none | TODO |
| 5 | Poison message | Send a malformed message to the checks queue | `PulseDLQNotEmpty` | TODO |
| 6 | Monitored site outage | `POST /switch/down` on `pulse-target` | Product alert in `#pulse-alerts` | TODO |
| 7 | Noisy monitors (optional) | 500 monitors on `/slow?ms=30000` | `PulseScheduleLagHigh`, `PulseCheckBacklog` | TODO |

## Index

| Date | Title | Drill | Severity | Action items merged |
|---|---|---|---|---|
| | | | | |
