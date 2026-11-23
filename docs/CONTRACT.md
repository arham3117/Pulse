# Pulse: cross-component contract

Layout: this repo is `Pulse/`. The GitOps repo lives at `Pulse/pulse-gitops/` (its own git repo,
listed in this repo's `.gitignore`). Scripts refer to it as `$REPO_ROOT/pulse-gitops`.

This file pins the names that the app, Terraform, the Helm chart, CI and the docs
must agree on. If you change a name here, change it everywhere.

## Placeholders (replace before first use)

GitHub is filled in: app repo `arham3117/Pulse` (capital P: GitHub puts the exact name in the
OIDC `sub` claim and IAM compares it case-sensitively), GitOps repo `arham3117/pulse-gitops`.

| Placeholder | Meaning | Where |
|---|---|---|
| `YOUR_NAME` | `Owner` tag value | `infra/envs/*/*.tfvars` |
| `us-east-2` | AWS region (default; change in tfvars + CLAUDE.md) | tfvars, backend blocks |
| `ACCOUNT_ID` | 12-digit AWS account id | backend bucket name `pulse-tfstate-ACCOUNT_ID` |
| `YOUR_IP/32` | your public IP for the EKS API allow-list | `infra/envs/*/*.tfvars` |

## Container image

- One image. ECR repo name `pulse` (shared by dev and prod). Tags `sha-<7 char sha>`, never `latest`.
- Workdir `/app`, runs as uid/gid `10001`, read-only root FS OK (only `/tmp` writable).
- Commands (console scripts on PATH):
  - `pulse-api` → HTTP on `:8000` (`/healthz`, `/readyz`, `/metrics`, API, status page)
  - `pulse-scheduler` → health/metrics on `:9100`
  - `pulse-worker` → health/metrics on `:9100`
  - `pulse-target` → everything (incl. `/healthz`) on `:8080`
  - `pulse-maintenance` → one-shot retention cleanup, exits 0
  - migrations: `alembic upgrade head` (run from `/app`, uses `/app/alembic.ini`)

## Environment variables (all `PULSE_` prefixed)

`PULSE_ENV`, `PULSE_LOG_LEVEL`, `PULSE_DB_HOST`, `PULSE_DB_PORT`, `PULSE_DB_NAME`,
`PULSE_DB_USER`, `PULSE_DB_PASSWORD`, `PULSE_DB_SSLMODE` (`disable` locally, `require` on AWS),
`PULSE_AWS_REGION`, `PULSE_SQS_QUEUE_URL`, `PULSE_SQS_DLQ_URL`, `PULSE_SQS_ENDPOINT_URL` (local only),
`PULSE_ADMIN_API_KEY`, `PULSE_ALERT_WEBHOOK_URL`, `PULSE_ALLOW_PRIVATE_HOSTS` (comma-separated,
e.g. `pulse-target` in k8s, `target` in compose), `PULSE_WORKER_CONCURRENCY` (50),
`PULSE_SCHEDULER_INTERVAL_SECONDS` (5), `PULSE_SCHEDULER_BATCH_SIZE` (500),
`PULSE_RETENTION_DAYS` (30), `PULSE_CHAOS_BREAK_READINESS` (false),
`PULSE_WORKER_ID` (defaults to hostname; set from pod name via downward API).
Listen ports (rarely changed): `PULSE_API_LISTEN_PORT` (8000), `PULSE_HEALTH_LISTEN_PORT` (9100),
`PULSE_TARGET_LISTEN_PORT` (8080).

**Never** name a setting `PULSE_<SERVICE>_PORT` / `_SERVICE_HOST`: Kubernetes "service links"
inject `<SERVICE>_PORT=tcp://ip:port` for every Service in the namespace (found on kind: the
`pulse-api` Service produced `PULSE_API_PORT=tcp://...` and crashed every pod). The chart also
sets `enableServiceLinks: false` on all pods, and a unit test guards the settings class.

Local docker compose host ports: API 8000, Prometheus 9090, chaos target **18080**, Postgres
**5433** (so they don't clash with services already on the host).

## Prometheus metrics (labels in braces; no unbounded labels)

API: `pulse_http_requests_total{method,route,status}`, `pulse_http_request_duration_seconds{method,route}` (histogram)
Scheduler: `pulse_scheduler_checks_enqueued_total`, `pulse_scheduler_loop_duration_seconds` (histogram),
`pulse_scheduler_due_monitors`, `pulse_scheduler_lag_seconds`, `pulse_queue_messages{queue="checks|dlq",state="visible|inflight"}`,
`pulse_active_monitors`
Worker: `pulse_checks_total{result="success|failure",error_type}` (error_type="none" on success),
`pulse_check_duration_seconds` (histogram), `pulse_check_schedule_lag_seconds` (histogram, buckets up to 600s),
`pulse_worker_inflight_checks`, `pulse_notifications_total{kind,result}`, `pulse_incidents_opened_total`,
`pulse_worker_message_errors_total`

## AWS resource names (per env `<env>` = dev|prod)

| Thing | Name |
|---|---|
| EKS cluster | `pulse-<env>` |
| Checks queue / DLQ | `pulse-<env>-checks` / `pulse-<env>-checks-dlq` |
| RDS instance | `pulse-<env>-db`, database name `pulse`, master user `pulse` (password managed by RDS in Secrets Manager) |
| App secret | `pulse/<env>/app` keys `admin_api_key`, `alert_webhook_url` |
| Grafana secret | `pulse/<env>/grafana` key `admin_password` |
| Alertmanager secret | `pulse/<env>/alertmanager` key `webhook_url` (platform alerts channel) |
| State bucket | `pulse-tfstate-ACCOUNT_ID`, keys `bootstrap/terraform.tfstate`, `envs/<env>/terraform.tfstate` |
| CI roles | `pulse-ci-ecr-push`, `pulse-ci-tf-plan`, `pulse-ci-tf-apply` |

Tags on everything: `Project=pulse`, `Environment=<env>`, `ManagedBy=terraform`, `Owner=<owner>`.

## Pod Identity associations (namespace/serviceaccount)

`pulse/pulse-scheduler`, `pulse/pulse-worker`, `external-secrets/external-secrets`,
`keda/keda-operator`, `kube-system/aws-load-balancer-controller`,
`kube-system/ebs-csi-controller-sa`, `kube-system/cluster-autoscaler`.
(`pulse-api` has a ServiceAccount but no AWS permissions.)

## Kubernetes Secrets produced by ExternalSecrets (namespace `pulse`)

| Secret | Keys | Source | Env var |
|---|---|---|---|
| `pulse-app` | `admin_api_key`, `alert_webhook_url` | `pulse/<env>/app` | `PULSE_ADMIN_API_KEY`, `PULSE_ALERT_WEBHOOK_URL` |
| `pulse-db` | `username`, `password` | RDS-managed secret (ARN in infra-values) | `PULSE_DB_USER`, `PULSE_DB_PASSWORD` |

In `monitoring`: `grafana-admin` (`admin-user`, `admin-password`) from `pulse/<env>/grafana`;
`alertmanager-webhook` (`url`) from `pulse/<env>/alertmanager`.

## `pulse-gitops/envs/<env>/infra-values.yaml` (written by `scripts/render-infra-values.sh`)

```yaml
# GENERATED by pulse/scripts/render-infra-values.sh from terraform outputs. Do not edit.
global:
  env: dev
  awsRegion: us-east-2
  clusterName: pulse-dev
image:
  repository: ACCOUNT_ID.dkr.ecr.us-east-2.amazonaws.com/pulse
infra:
  sqsQueueUrl: https://sqs.us-east-2.amazonaws.com/ACCOUNT_ID/pulse-dev-checks
  sqsQueueName: pulse-dev-checks
  sqsDlqUrl: https://sqs.us-east-2.amazonaws.com/ACCOUNT_ID/pulse-dev-checks-dlq
  dbHost: pulse-dev-db.xxxx.us-east-2.rds.amazonaws.com
  dbPort: 5432
  dbName: pulse
  rdsMasterSecretArn: arn:aws:secretsmanager:...
  appSecretName: pulse/dev/app
  grafanaSecretName: pulse/dev/grafana
  alertmanagerSecretName: pulse/dev/alertmanager
  acmCertificateArn: ""
  vpcId: vpc-...
```

Terraform outputs used by the script (same names in both envs): `region`, `cluster_name`,
`ecr_repository_url` (passed through from bootstrap via variable or data source), `sqs_queue_url`,
`sqs_queue_name`, `sqs_dlq_url`, `db_host`, `db_port`, `db_name`, `rds_master_secret_arn`,
`app_secret_name`, `grafana_secret_name`, `alertmanager_secret_name`, `acm_certificate_arn`, `vpc_id`.

## Alerts (PrometheusRule) → runbooks `docs/runbooks/<AlertName>.md`

`PulseAPIHighErrorRate`, `PulseAPIHighLatency`, `PulseCheckBacklog`, `PulseChecksStalled`,
`PulseScheduleLagHigh`, `PulseDLQNotEmpty`, `PulseSchedulerDown`, `PulsePodCrashLooping`,
`PulseSLOFastBurn`, `PulseSLOSlowBurn`.
Runbook URL: `https://github.com/arham3117/Pulse/blob/main/docs/runbooks/<AlertName>.md`.
Prometheus scrape job names: `pulse-api`, `pulse-scheduler`, `pulse-worker`.
