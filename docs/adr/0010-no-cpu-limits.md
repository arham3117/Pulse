# 0010: Set CPU requests and memory limits, but no CPU limits

- Status: Proposed (draft — rewrite in your own words before marking Accepted)
- Date: 2026-11-22

## Context

Every container needs resource settings. Requests drive scheduling and the HPA's CPU percentage;
limits cap usage. CPU and memory behave differently:

- **CPU is compressible.** When a container exceeds its CPU limit, the kernel's CFS bandwidth control
  throttles it: the container gets its quota per scheduling period (100 ms by default) and is paused
  for the rest of the period, **even if the node has idle CPU**. Short bursts (request spikes, GC,
  TLS handshakes) turn into added latency.
- **Memory is not compressible.** A container that exceeds its memory limit is OOM-killed. Without a
  limit, a leak can push the node into memory pressure and get other pods evicted.

CPU requests already guarantee each container its proportional share under contention (cgroup CPU
weight), so a limit is not needed to protect neighbours from starvation.

## Options considered

1. **CPU and memory limits on everything**
   - Pros: hard caps, predictable per-pod usage; Guaranteed QoS if requests equal limits.
   - Cons: CFS throttling adds tail latency even on idle nodes; limits set too low are hard to
     notice without throttling metrics.
2. **Memory limits, CPU requests, no CPU limits**
   - Pros: pods can use idle CPU; no throttling-induced latency; requests still guarantee fair share
     under contention; memory leaks are contained.
   - Cons: Burstable QoS (not Guaranteed); a CPU-hungry pod can use spare CPU that other pods might
     want to burst into; capacity planning relies on accurate requests.
3. **No limits at all**
   - Pros: simplest.
   - Cons: one memory leak can destabilise a node.

## Decision

Every Pulse container sets CPU and memory **requests** and a memory **limit**. No container sets a
CPU limit. Requests are sized from observed usage under load (Phase 7) and revisited after load
tests.

## Consequences

- Easier: no CPU throttling on the latency-sensitive API; workers can absorb check bursts.
- Harder: pods are Burstable QoS, so under node memory pressure they are evicted before Guaranteed
  pods; accurate requests matter more, since the scheduler and HPA rely on them.
- Watch for throttling and saturation with
  `rate(container_cpu_cfs_throttled_periods_total{namespace="pulse"}[5m])` (should be zero without
  limits) and node CPU usage vs allocatable.
- Policies must match this decision: the stretch goal "Kyverno: require resource limits" should
  require memory limits only. A namespace `LimitRange` or `ResourceQuota` that demands CPU limits
  would conflict.
- At larger scale or in multi-tenant clusters, CPU limits (or Guaranteed QoS with CPU pinning) can be
  justified for isolation; that is a different trade-off from a single-team cluster.
