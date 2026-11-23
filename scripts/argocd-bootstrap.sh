#!/usr/bin/env bash
# Install Argo CD (pinned) into the current cluster and hand everything else to GitOps.
#
#   scripts/argocd-bootstrap.sh dev|prod        (or: make argocd-bootstrap ENV=dev)
#
# Steps:
#   1. Safety check: the current kubectl context must point at EKS cluster pulse-<env>.
#   2. helm upgrade --install Argo CD (chart version pinned below) into namespace argocd,
#      with values from pulse-gitops/argocd/bootstrap/argocd-values.yaml.
#   3. kubectl apply the root "app of apps": pulse-gitops/argocd/<env>/root.yaml.
#   4. Wait until every Argo CD Application is Synced + Healthy (timeout: $TIMEOUT_SECONDS).
#   5. Print the ALB hostname of the pulse-api Ingress (the status page URL).
#
# Prereqs: aws eks update-kubeconfig done (make kubeconfig ENV=...), helm, kubectl, jq.
# If pulse-gitops is a private repo, register it with Argo CD first (repository Secret or
# `argocd repo add`); this script never handles Git credentials.
set -euo pipefail

ARGOCD_CHART_VERSION="10.18.2"   # Argo CD v3.6.2
ARGOCD_HELM_REPO="https://argoproj.github.io/argo-helm"
ARGOCD_NAMESPACE="argocd"
TIMEOUT_SECONDS="${TIMEOUT_SECONDS:-1800}"
POLL_SECONDS=15

usage() {
  cat >&2 <<EOF
Usage: $(basename "$0") ENV

  ENV   dev | prod

Installs Argo CD ${ARGOCD_CHART_VERSION} (Helm chart) into namespace '${ARGOCD_NAMESPACE}',
applies pulse-gitops/argocd/ENV/root.yaml, waits for all Applications to be Synced and
Healthy (TIMEOUT_SECONDS, default 1800), then prints the status page URL.

Environment:
  TIMEOUT_SECONDS          max seconds to wait for Applications (default: 1800)
  SKIP_CONTEXT_CHECK=1     skip the "kubectl context must be pulse-ENV" safety check
EOF
  exit 2
}

log() { printf '[argocd-bootstrap] %s\n' "$*" >&2; }
die() { log "ERROR: $*"; exit 1; }

[[ $# -eq 1 ]] || usage
ENV="$1"
case "$ENV" in
  dev|prod) ;;
  -h|--help) usage ;;
  *) log "unknown ENV '$ENV'"; usage ;;
esac

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
GITOPS_DIR="$REPO_ROOT/pulse-gitops"
ROOT_APP="$GITOPS_DIR/argocd/$ENV/root.yaml"
APPS_DIR="$GITOPS_DIR/argocd/$ENV/apps"
ARGOCD_VALUES="$GITOPS_DIR/argocd/bootstrap/argocd-values.yaml"

for bin in helm kubectl jq; do
  command -v "$bin" >/dev/null 2>&1 || die "'$bin' is required but not installed"
done
[[ -f "$ROOT_APP" ]] || die "root Application not found: $ROOT_APP"
[[ -f "$ARGOCD_VALUES" ]] || die "Argo CD values not found: $ARGOCD_VALUES"

# --- 1. Make sure we are talking to the right cluster -------------------------------------
CONTEXT="$(kubectl config current-context 2>/dev/null)" || die "no current kubectl context"
log "kubectl context: $CONTEXT"
if [[ "${SKIP_CONTEXT_CHECK:-0}" != "1" ]]; then
  # aws eks update-kubeconfig names contexts arn:aws:eks:<region>:<account>:cluster/pulse-<env>
  [[ "$CONTEXT" == *"cluster/pulse-$ENV" || "$CONTEXT" == "pulse-$ENV" ]] \
    || die "context '$CONTEXT' is not cluster pulse-$ENV (run: make kubeconfig ENV=$ENV, or set SKIP_CONTEXT_CHECK=1)"
fi
kubectl cluster-info >/dev/null || die "cannot reach the cluster API"

# --- 2. Argo CD ---------------------------------------------------------------------------
log "installing Argo CD chart $ARGOCD_CHART_VERSION into namespace $ARGOCD_NAMESPACE"
helm repo add argo "$ARGOCD_HELM_REPO" --force-update >/dev/null
helm repo update argo >/dev/null
helm upgrade --install argocd argo/argo-cd \
  --version "$ARGOCD_CHART_VERSION" \
  --namespace "$ARGOCD_NAMESPACE" --create-namespace \
  --values "$ARGOCD_VALUES" \
  --wait --timeout 10m

# --- 3. Root app-of-apps ------------------------------------------------------------------
log "applying root Application: $ROOT_APP"
kubectl apply -n "$ARGOCD_NAMESPACE" -f "$ROOT_APP"

# --- 4. Wait for every Application --------------------------------------------------------
# Expected = root + one Application per file in argocd/<env>/apps.
expected=$(( $(find "$APPS_DIR" -maxdepth 1 -name '*.yaml' | wc -l) + 1 ))
log "waiting up to ${TIMEOUT_SECONDS}s for $expected Applications to be Synced + Healthy"
deadline=$(( $(date +%s) + TIMEOUT_SECONDS ))
while true; do
  apps_json="$(kubectl get applications.argoproj.io -n "$ARGOCD_NAMESPACE" -o json)"
  total=$(jq '.items | length' <<<"$apps_json")
  ready=$(jq '[.items[] | select(.status.sync.status == "Synced" and .status.health.status == "Healthy")] | length' <<<"$apps_json")
  if (( total >= expected && ready == total )); then
    log "all $total Applications are Synced and Healthy"
    break
  fi
  if (( $(date +%s) >= deadline )); then
    log "timed out; current state:"
    jq -r '.items[] | "  \(.metadata.name)\t sync=\(.status.sync.status // "?")\t health=\(.status.health.status // "?")"' <<<"$apps_json" >&2
    die "Applications not Synced/Healthy after ${TIMEOUT_SECONDS}s (see: kubectl -n $ARGOCD_NAMESPACE get applications)"
  fi
  not_ready=$(jq -r '[.items[] | select(.status.sync.status != "Synced" or .status.health.status != "Healthy") | .metadata.name] | join(", ")' <<<"$apps_json")
  log "$ready/$total ready (expecting $expected); waiting on: ${not_ready:-<apps not created yet>}"
  sleep "$POLL_SECONDS"
done

# --- 5. Status page URL -------------------------------------------------------------------
log "waiting for the pulse-api Ingress to get an ALB hostname"
hostname=""
for _ in $(seq 1 40); do
  hostname="$(kubectl get ingress pulse-api -n pulse -o jsonpath='{.status.loadBalancer.ingress[0].hostname}' 2>/dev/null || true)"
  [[ -n "$hostname" ]] && break
  sleep "$POLL_SECONDS"
done
[[ -n "$hostname" ]] || die "pulse-api Ingress has no ALB hostname yet (check aws-load-balancer-controller logs)"

scheme="http"
if kubectl get ingress pulse-api -n pulse -o jsonpath='{.metadata.annotations.alb\.ingress\.kubernetes\.io/certificate-arn}' 2>/dev/null | grep -q .; then
  scheme="https"
fi
log "done. ALB DNS can take a few minutes to resolve."
echo "${scheme}://${hostname}"
