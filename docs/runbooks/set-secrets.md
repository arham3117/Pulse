# Set secret values

Terraform creates the Secrets Manager secrets **without values**, so secret values never enter
Terraform state or git. You set the values yourself with the AWS CLI after `make infra-up`, and
External Secrets Operator copies them into Kubernetes.

| Secret | Keys | Synced to (Kubernetes) | Used for |
|---|---|---|---|
| `pulse/<env>/app` | `admin_api_key`, `alert_webhook_url` | `pulse/pulse-app` | `PULSE_ADMIN_API_KEY` (API writes), `PULSE_ALERT_WEBHOOK_URL` (product alerts to `#pulse-alerts`) |
| `pulse/<env>/grafana` | `admin_password` | `monitoring/grafana-admin` | Grafana admin login |
| `pulse/<env>/alertmanager` | `webhook_url` | `monitoring/alertmanager-webhook` | Platform alerts to `#pulse-platform` |

The RDS master password is created and rotated by RDS itself (`manage_master_user_password`); you
never set it. It syncs to `pulse/pulse-db`.

## Rules

- Never pass a secret as a literal on the command line (for example
  `--secret-string '{"admin_api_key":"..."}'`). It ends up in shell history and is visible in the
  process list while the command runs.
- Read values with `read -s` (no echo, not saved to history) or generate them locally, write the JSON
  to a temporary file readable only by you, pass it with `file://`, and delete the file.
- Never paste secret values into chat, issues, commits or screenshots.
- Optional extra safety: in zsh, `setopt HIST_IGNORE_SPACE` (bash: `HISTCONTROL=ignorespace`) and
  start sensitive commands with a space so they are not recorded.

## 0. Setup and check the secrets exist

```sh
export AWS_PROFILE=pulse AWS_REGION=us-east-2 ENV=dev

for s in app grafana alertmanager; do
  aws secretsmanager describe-secret --secret-id "pulse/$ENV/$s" \
    --query '{name:Name, versions:VersionIdsToStages}' --output json
done
```

A secret with no versions (`versions: null`) has no value yet. If `describe-secret` says the secret is
scheduled for deletion, see [teardown.md](teardown.md#secrets-manager-recovery-window).

## 1. App secret (`pulse/<env>/app`)

```sh
# Generate the admin API key locally. It is never printed.
ADMIN_KEY="$(openssl rand -base64 32 | tr -d '\n')"

# Paste the Slack/Discord webhook URL for #pulse-alerts. Input is hidden.
read -rs "ALERT_WEBHOOK?Webhook URL for #pulse-alerts: "; echo          # zsh
# read -rs -p "Webhook URL for #pulse-alerts: " ALERT_WEBHOOK; echo     # bash

umask 077
tmp="$(mktemp)"
ADMIN_KEY="$ADMIN_KEY" ALERT_WEBHOOK="$ALERT_WEBHOOK" \
  jq -n '{admin_api_key: env.ADMIN_KEY, alert_webhook_url: env.ALERT_WEBHOOK}' > "$tmp"
aws secretsmanager put-secret-value --secret-id "pulse/$ENV/app" --secret-string "file://$tmp"
rm -f "$tmp"
unset ADMIN_KEY ALERT_WEBHOOK
```

Passing values to `jq` through environment variables (`env.X`) keeps them out of `jq`'s command-line
arguments.

## 2. Grafana secret (`pulse/<env>/grafana`)

```sh
GRAFANA_PW="$(openssl rand -base64 24 | tr -d '\n')"
umask 077
tmp="$(mktemp)"
GRAFANA_PW="$GRAFANA_PW" jq -n '{admin_password: env.GRAFANA_PW}' > "$tmp"
aws secretsmanager put-secret-value --secret-id "pulse/$ENV/grafana" --secret-string "file://$tmp"
rm -f "$tmp"
unset GRAFANA_PW
```

## 3. Alertmanager secret (`pulse/<env>/alertmanager`)

```sh
read -rs "PLATFORM_WEBHOOK?Webhook URL for #pulse-platform: "; echo     # zsh
# read -rs -p "Webhook URL for #pulse-platform: " PLATFORM_WEBHOOK; echo  # bash

umask 077
tmp="$(mktemp)"
PLATFORM_WEBHOOK="$PLATFORM_WEBHOOK" jq -n '{webhook_url: env.PLATFORM_WEBHOOK}' > "$tmp"
aws secretsmanager put-secret-value --secret-id "pulse/$ENV/alertmanager" --secret-string "file://$tmp"
rm -f "$tmp"
unset PLATFORM_WEBHOOK
```

## 4. Verify without printing values

```sh
for s in app grafana alertmanager; do
  printf '%s: ' "pulse/$ENV/$s"
  aws secretsmanager get-secret-value --secret-id "pulse/$ENV/$s" \
    --query SecretString --output text | jq -c 'keys'
done
```

Expected: `["admin_api_key","alert_webhook_url"]`, `["admin_password"]`, `["webhook_url"]`.

## 5. Sync into Kubernetes

ESO refreshes on its `refreshInterval`. To sync now, change an annotation on the ExternalSecret:

```sh
kubectl -n pulse annotate externalsecret pulse-app force-sync="$(date +%s)" --overwrite
kubectl -n pulse get externalsecret                # STATUS should be SecretSynced
kubectl -n monitoring get externalsecret
kubectl -n monitoring annotate externalsecret --all force-sync="$(date +%s)" --overwrite
```

Environment variables from a Secret are read at container start, so restart the consumers:

```sh
kubectl -n pulse rollout restart deploy/pulse-api deploy/pulse-worker
kubectl -n monitoring get deploy,statefulset | grep -E 'grafana|alertmanager'
kubectl -n monitoring rollout restart deploy/<grafana-deployment>
```

Notes:

- Grafana only applies the admin password from its environment when the admin user is first created.
  If Grafana has persistent storage, changing the secret does not change an existing password; reset
  it with `grafana cli admin reset-admin-password` inside the Grafana container.
- How Alertmanager picks up a new webhook URL depends on how the chart wires it. If the URL is read
  from a mounted secret file, it updates after the kubelet refreshes the mount; otherwise restart the
  Alertmanager StatefulSet.

## Using the admin key

Load it into a variable when you need to call the API; do not print it:

```sh
PULSE_KEY="$(aws secretsmanager get-secret-value --secret-id "pulse/$ENV/app" \
  --query SecretString --output text | jq -r .admin_api_key)"
API="http://$(kubectl -n pulse get ingress -o jsonpath='{.items[0].status.loadBalancer.ingress[0].hostname}')"
curl -s -H "X-API-Key: $PULSE_KEY" "$API/api/v1/monitors" | jq .
```

## Rotation

Run the relevant step again with a new value. `put-secret-value` creates a new version labelled
`AWSCURRENT` and moves the old one to `AWSPREVIOUS`. Then force a sync and restart the consumers
(step 5). For the admin key, there is a short window where old pods accept the old key and new pods
accept the new one.

## After a rebuild

Whether values survive `make infra-down` depends on whether Terraform destroys the secrets. If it
does, the secrets are recreated empty by the next `make infra-up` and you repeat steps 1–5. Check
[teardown.md](teardown.md#secrets-manager-recovery-window) for the recovery-window issue.
