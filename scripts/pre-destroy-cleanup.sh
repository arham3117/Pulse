#!/usr/bin/env bash
# Remove everything Kubernetes controllers created in AWS (ALBs/NLBs, their
# security groups, EBS volumes from PVCs) so `terraform destroy` can delete the
# VPC and nothing keeps billing. These resources are NOT in Terraform state.
#
# Order matters (plan 5.4, adjusted so nothing is orphaned):
#   1. Stop Argo CD from recreating things (disable automated sync/self-heal).
#   2. Delete Ingresses and LoadBalancer Services while the AWS Load Balancer
#      Controller is still running, and wait until AWS shows no load balancers
#      tagged for this cluster. (Deleting the controller first would orphan ALBs.)
#   3. Delete the Argo CD root app with cascade (all child apps and workloads).
#   4. Delete remaining PVCs and wait until the EBS volumes tagged for this
#      cluster are gone (the EBS CSI add-on is an EKS add-on, so it survives step 3).
#
# Usage: scripts/pre-destroy-cleanup.sh dev|prod [--yes]
# Env:   ROOT_APP          Argo CD root Application name   (default: root)
#        ARGOCD_NAMESPACE  Argo CD namespace                (default: argocd)
#        TIMEOUT_SECONDS   per wait loop                    (default: 900)
#        POLL_SECONDS      poll interval                    (default: 10)
#        AWS_REGION        used if terraform output is unavailable (default: us-east-2)
set -euo pipefail

usage() {
  cat >&2 <<EOF
Usage: $(basename "$0") ENV [--yes]

Deletes Kubernetes-created AWS resources for cluster pulse-ENV before terraform destroy.

  ENV     dev | prod
  --yes   do not ask for confirmation
EOF
  exit 2
}

log() { printf '[%s] %s\n' "$(date -u +%H:%M:%S)" "$*" >&2; }
die() {
  log "error: $*"
  exit 1
}

[[ $# -ge 1 && $# -le 2 ]] || usage
ENV="$1"
ASSUME_YES="false"
case "${ENV}" in
  dev | prod) ;;
  -h | --help) usage ;;
  *) echo "error: unknown environment '${ENV}'" >&2; usage ;;
esac
if [[ $# -eq 2 ]]; then
  [[ "$2" == "--yes" ]] || usage
  ASSUME_YES="true"
fi

for bin in kubectl aws jq; do
  command -v "${bin}" >/dev/null 2>&1 || die "'${bin}' is required but not installed"
done

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TF_DIR="${REPO_ROOT}/infra/envs/${ENV}"
CLUSTER="pulse-${ENV}"
ROOT_APP="${ROOT_APP:-root}"
ARGOCD_NAMESPACE="${ARGOCD_NAMESPACE:-argocd}"
TIMEOUT_SECONDS="${TIMEOUT_SECONDS:-900}"
POLL_SECONDS="${POLL_SECONDS:-10}"

REGION=""
VPC_ID=""
if command -v terraform >/dev/null 2>&1 && [[ -d "${TF_DIR}" ]]; then
  REGION="$(terraform -chdir="${TF_DIR}" output -raw region 2>/dev/null || true)"
  VPC_ID="$(terraform -chdir="${TF_DIR}" output -raw vpc_id 2>/dev/null || true)"
fi
REGION="${REGION:-${AWS_REGION:-us-east-2}}"
export AWS_REGION="${REGION}"

# ---------------------------------------------------------------------------
# Safety: kubectl must point at this environment's cluster.
# ---------------------------------------------------------------------------
context="$(kubectl config current-context 2>/dev/null || true)"
[[ -n "${context}" ]] || die "no current kubectl context; run 'make kubeconfig ENV=${ENV}'"
cluster_entry="$(kubectl config view --minify -o jsonpath='{.clusters[0].name}' 2>/dev/null || true)"
if [[ "${context}" != *"cluster/${CLUSTER}" && "${cluster_entry}" != *"cluster/${CLUSTER}" ]]; then
  die "kubectl context '${context}' is not cluster ${CLUSTER}; run 'make kubeconfig ENV=${ENV}'"
fi
kubectl version --request-timeout=10s >/dev/null 2>&1 \
  || die "cannot reach the Kubernetes API of ${CLUSTER} (is your IP in admin_cidrs?)"

log "cluster=${CLUSTER} region=${REGION} vpc=${VPC_ID:-unknown} context=${context}"
if [[ "${ASSUME_YES}" != "true" ]]; then
  read -r -p "Delete all load balancers, Argo CD apps and PVCs in ${CLUSTER}? Type the cluster name to continue: " answer
  [[ "${answer}" == "${CLUSTER}" ]] || die "aborted"
fi

# wait_until_empty DESCRIPTION COMMAND...: poll until COMMAND prints nothing.
wait_until_empty() {
  local description="$1"
  shift
  local deadline=$((SECONDS + TIMEOUT_SECONDS))
  local remaining
  while true; do
    remaining="$("$@")"
    if [[ -z "${remaining}" ]]; then
      log "${description}: none left"
      return 0
    fi
    if ((SECONDS >= deadline)); then
      log "timed out after ${TIMEOUT_SECONDS}s waiting for ${description}; still present:"
      tr -s ' \t' '\n' <<<"${remaining}" | sed 's/^/  /' >&2
      return 1
    fi
    log "waiting for ${description}: $(wc -w <<<"${remaining}" | tr -d ' ') left"
    sleep "${POLL_SECONDS}"
  done
}

argocd_installed() {
  kubectl get crd applications.argoproj.io >/dev/null 2>&1
}

# ---------------------------------------------------------------------------
# 1. Stop Argo CD from re-creating what we delete.
# ---------------------------------------------------------------------------
apps=()
ordered=()
if argocd_installed; then
  log "disabling automated sync on Argo CD applications"
  while IFS= read -r app; do
    if [[ -n "${app}" ]]; then apps+=("${app}"); fi
  done < <(kubectl -n "${ARGOCD_NAMESPACE}" get applications.argoproj.io \
    -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}' 2>/dev/null || true)

  # Root first, so it stops restoring the children's syncPolicy.
  # ${arr[@]+...} keeps bash 3.2 (macOS) happy with empty arrays under set -u.
  for app in ${apps[@]+"${apps[@]}"}; do
    if [[ "${app}" == "${ROOT_APP}" ]]; then ordered+=("${app}"); fi
  done
  for app in ${apps[@]+"${apps[@]}"}; do
    if [[ "${app}" != "${ROOT_APP}" ]]; then ordered+=("${app}"); fi
  done

  for app in ${ordered[@]+"${ordered[@]}"}; do
    kubectl -n "${ARGOCD_NAMESPACE}" patch applications.argoproj.io "${app}" --type json \
      -p '[{"op":"remove","path":"/spec/syncPolicy/automated"}]' >/dev/null 2>&1 || true
  done
else
  log "Argo CD CRDs not found; skipping Argo CD steps"
fi

# ---------------------------------------------------------------------------
# 2. Ingresses + LoadBalancer Services, then wait for AWS to drop the LBs.
# ---------------------------------------------------------------------------
log "deleting all Ingresses"
kubectl delete ingress --all-namespaces --all --wait=false --ignore-not-found >/dev/null

log "deleting all Services of type LoadBalancer"
while read -r ns name; do
  [[ -n "${ns}" ]] || continue
  kubectl -n "${ns}" delete service "${name}" --wait=false --ignore-not-found >/dev/null
done < <(kubectl get services --all-namespaces -o json \
  | jq -r '.items[] | select(.spec.type == "LoadBalancer") | "\(.metadata.namespace) \(.metadata.name)"')

# Prints ARNs of ELBv2 load balancers tagged elbv2.k8s.aws/cluster=<cluster>.
cluster_load_balancers() {
  local query='LoadBalancers[].LoadBalancerArn'
  if [[ -n "${VPC_ID}" ]]; then
    query="LoadBalancers[?VpcId=='${VPC_ID}'].LoadBalancerArn"
  fi
  local arns=()
  read -r -a arns <<<"$(aws elbv2 describe-load-balancers --query "${query}" --output text)"
  local i
  for ((i = 0; i < ${#arns[@]}; i += 20)); do
    [[ "${arns[i]}" == "None" ]] && continue
    aws elbv2 describe-tags --resource-arns "${arns[@]:i:20}" --output json \
      | jq -r --arg c "${CLUSTER}" '
          .TagDescriptions[]
          | select(any(.Tags[]; (.Key == "elbv2.k8s.aws/cluster" and .Value == $c)
                              or .Key == ("kubernetes.io/cluster/" + $c)))
          | .ResourceArn'
  done
}

wait_until_empty "load balancers tagged for ${CLUSTER}" cluster_load_balancers

# ---------------------------------------------------------------------------
# 3. Delete the Argo CD root app with cascade.
# ---------------------------------------------------------------------------
if argocd_installed; then
  # The resources finalizer makes Argo CD delete each app's resources (cascade).
  finalizer='{"metadata":{"finalizers":["resources-finalizer.argocd.argoproj.io"]}}'
  for app in ${ordered[@]+"${ordered[@]}"}; do
    kubectl -n "${ARGOCD_NAMESPACE}" patch applications.argoproj.io "${app}" --type merge \
      -p "${finalizer}" >/dev/null 2>&1 || true
  done

  if kubectl -n "${ARGOCD_NAMESPACE}" get applications.argoproj.io "${ROOT_APP}" >/dev/null 2>&1; then
    log "deleting Argo CD root application '${ROOT_APP}' (cascade)"
    kubectl -n "${ARGOCD_NAMESPACE}" delete applications.argoproj.io "${ROOT_APP}" \
      --cascade=foreground --wait=false >/dev/null
  else
    log "root application '${ROOT_APP}' not found (set ROOT_APP if it has another name)"
  fi
  # Anything not owned by the root app goes too.
  kubectl -n "${ARGOCD_NAMESPACE}" delete applications.argoproj.io --all --wait=false >/dev/null 2>&1 || true

  remaining_apps() {
    kubectl -n "${ARGOCD_NAMESPACE}" get applications.argoproj.io \
      -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}' 2>/dev/null || true
  }
  wait_until_empty "Argo CD applications" remaining_apps
fi

# ---------------------------------------------------------------------------
# 4. PVCs and their EBS volumes.
# ---------------------------------------------------------------------------
log "deleting all PersistentVolumeClaims"
kubectl delete pvc --all-namespaces --all --wait=false --ignore-not-found >/dev/null

remaining_pvcs() {
  kubectl get pvc --all-namespaces -o jsonpath='{range .items[*]}{.metadata.namespace}/{.metadata.name}{"\n"}{end}'
}
if ! wait_until_empty "PersistentVolumeClaims" remaining_pvcs; then
  log "hint: a pod may still mount the volume; delete its StatefulSet/Deployment and re-run"
  exit 1
fi

# The aws-ebs-csi-driver add-on runs with k8sTagClusterId=<cluster>, which tags
# every dynamically provisioned volume kubernetes.io/cluster/<cluster>=owned.
cluster_volumes() {
  aws ec2 describe-volumes \
    --filters "Name=tag-key,Values=kubernetes.io/cluster/${CLUSTER}" \
    --query 'Volumes[].VolumeId' --output text | tr '\t' '\n' | grep -v '^None$' || true
}
if ! wait_until_empty "EBS volumes tagged for ${CLUSTER}" cluster_volumes; then
  log "hint: volumes from a StorageClass with reclaimPolicy=Retain are not deleted automatically"
  exit 1
fi

# ---------------------------------------------------------------------------
# Report leftovers that would block VPC deletion (informational).
# ---------------------------------------------------------------------------
leftover_sgs="$(aws ec2 describe-security-groups \
  --filters "Name=tag:elbv2.k8s.aws/cluster,Values=${CLUSTER}" \
  --query 'SecurityGroups[].GroupId' --output text 2>/dev/null || true)"
if [[ -n "${leftover_sgs}" && "${leftover_sgs}" != "None" ]]; then
  log "warning: security groups created by the load balancer controller still exist: ${leftover_sgs}"
  log "they normally disappear shortly after the load balancers; re-check before terraform destroy"
fi

log "cleanup complete; safe to run terraform destroy for ${ENV}"
