# 0006: EKS managed node groups instead of Fargate or EKS Auto Mode

- Status: Proposed (draft — rewrite in your own words before marking Accepted)
- Date: 2026-11-22

## Context

EKS needs compute for pods. The options trade operational effort against control and learning. This
is a portfolio project: understanding node-level concerns (capacity, instance metadata, spot,
draining, autoscaling) is part of the point. The platform also needs DaemonSets (Grafana Alloy for
logs, node-exporter) and EBS-backed volumes (Prometheus, Loki).

## Options considered

1. **EKS managed node groups**
   - Pros: AWS handles node provisioning, AMI updates and graceful draining during updates;
     full control of instance types, spot vs on-demand, launch template (IMDSv2, hop limit, encrypted
     root volume); DaemonSets and EBS volumes work normally; works with Cluster Autoscaler.
   - Cons: node capacity, bin-packing and AMI upgrades are ours to plan; nodes cost money while idle.
2. **Fargate**
   - Pros: no nodes to manage; per-pod isolation.
   - Cons: no DaemonSets (log shipping needs a sidecar or Fargate's built-in log router); no EBS
     volumes for pods (Prometheus/Loki would need EFS or another design); no spot; hides exactly
     the node-level topics this project is meant to teach.
3. **EKS Auto Mode**
   - Pros: AWS manages nodes, scaling (Karpenter-based), and core add-ons; less to operate.
   - Cons: an additional management charge on top of instance cost; fewer knobs; again hides
     node-level concerns.
4. **Self-managed node groups or Karpenter**
   - Pros: maximum control; Karpenter gives fast, flexible provisioning.
   - Cons: more to operate; Karpenter is listed as a stretch goal once the basics work.

## Decision

Use one EKS managed node group per environment, with a launch template that requires IMDSv2 with
hop limit 1 and uses an encrypted gp3 root volume.

- dev: 2–3 spot nodes with several instance types (for example `t3.large`, `t3a.large`, `m5.large`)
  to improve spot availability.
- prod: on-demand nodes spread across AZs.

Cluster Autoscaler grows the group when pods are Pending.

## Consequences

- Easier: everything in the platform runs unmodified (DaemonSets, EBS PVCs); the IMDS hop-limit
  control is explicit and testable; spot interruptions provide real practice of node loss (drill 4).
- Harder: capacity planning and node upgrades are ours; spot interruptions evict pods with short
  notice, so PDBs, graceful shutdown and at-least-once processing matter; EBS volumes are bound to an
  AZ, so a Prometheus or Loki pod can only reschedule onto a node in the same AZ.
- Idle nodes cost money: dev is torn down when not in use.
- At larger scale: move to Karpenter for faster, cheaper provisioning, or Auto Mode if the team would
  rather not own nodes.
