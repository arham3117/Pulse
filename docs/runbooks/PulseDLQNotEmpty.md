# PulseDLQNotEmpty

## What it means

The dead-letter queue `pulse-<env>-checks-dlq` has held at least one message for 15 minutes:

```promql
max(sum by (instance) (pulse_queue_messages{queue="dlq"})) > 0
```

A message lands in the DLQ after it was received 3 times (`maxReceiveCount = 3`) without being
deleted, meaning the worker failed on it three times in a row (unhandled exception, crash, or
timeout beyond the 60 s visibility timeout). Each DLQ message is a check that never completed.

## Impact

Usually small: a few checks were skipped, and the scheduler has already scheduled the next checks
for those monitors. It matters when:

- the same monitor keeps failing (a bug that makes one monitor uncheckable, so its outages are never
  detected); or
- many messages arrive at once (a systemic failure such as a database outage or a bad deploy).

DLQ retention is 4 days; after that the evidence is gone.

## First checks (5 minutes)

1. Grafana "Pulse / Service overview": "DLQ depth" and "Queue messages". One message or many? When
   did they arrive, and does that line up with a deploy, a DB event, or a drill?

   ```sh
   qdepth "$DLQURL"
   promq 'sum(increase(pulse_worker_message_errors_total[1h]))'
   ```

2. Look at a few messages without removing them. This hides them for 60 s and increments their receive
   count on the DLQ (harmless: the DLQ has no redrive policy of its own).

   ```sh
   aws sqs receive-message --queue-url "$DLQURL" --max-number-of-messages 10 --visibility-timeout 60 \
     --message-system-attribute-names All \
     --query 'Messages[].{body:Body,receives:Attributes.ApproximateReceiveCount,sent:Attributes.SentTimestamp,handle:ReceiptHandle}' \
     --output json
   ```

   (On older AWS CLI versions use `--attribute-names All`.) `SentTimestamp` is epoch milliseconds:
   `date -u -r $((ms/1000))` on macOS.
3. Is the body valid? A normal body is `{"monitor_id": "<uuid>", "scheduled_for": "<ISO8601>"}`. If
   it is, check the monitor in the SQL shell ([README](README.md#sql-shell-against-rds)):
   `SELECT id, url, is_active, status FROM monitors WHERE id = '<uuid>';`
4. Logs around the message's time: `{namespace="pulse", pod=~"pulse-worker-.*"} | json |
   level="error"` and, if logged, `| monitor_id="<uuid>"`.
5. Argo CD: was a worker change deployed shortly before the messages arrived?

## Likely causes and fixes

| Cause | How to confirm | Fix |
|---|---|---|
| Malformed / poison message (drill 5, manual send, bug in producer) | Body is not valid JSON or lacks fields; worker logs show a parse error | Delete it (see below). If the scheduler produced it, fix the scheduler first |
| Database outage during processing (drill 3) | Messages arrived during an RDS reboot/failover window; DB errors in worker logs | Usually purge: the checks are stale and newer ones already ran. Redrive only if you need the data |
| Worker bug on specific monitors | Same `monitor_id`s repeat; same traceback each time | Fix the bug, deploy, then redrive (or let the next scheduled checks run and purge) |
| Worker killed mid-check repeatedly (OOM, node loss) | Restarts and OOMKilled around those times | Fix memory limit or the cause; see [PulsePodCrashLooping](PulsePodCrashLooping.md) |
| Check exceeds visibility timeout | Check duration close to 60 s (long timeout + redirects); message received again while still running | Keep timeout + redirects well under 60 s; or extend visibility with `ChangeMessageVisibility` during long checks |

### Delete one message

Use a receipt handle from a `receive-message` call made within its visibility timeout:

```sh
aws sqs delete-message --queue-url "$DLQURL" --receipt-handle '<handle>'
```

### Redrive messages back to the checks queue

Only after the cause is fixed. Without `--destination-arn`, SQS moves messages back to the queue they
came from.

```sh
DLQ_ARN=$(aws sqs get-queue-attributes --queue-url "$DLQURL" --attribute-names QueueArn \
  --query Attributes.QueueArn --output text)
aws sqs start-message-move-task --source-arn "$DLQ_ARN" --max-number-of-messages-per-second 10
aws sqs list-message-move-tasks --source-arn "$DLQ_ARN"        # status, moved count
# Abort if failures recur:
aws sqs cancel-message-move-task --task-handle '<TaskHandle from start/list>'
```

Things to know before redriving:

- Redriven checks carry their old `scheduled_for`, so they record a large schedule lag and count
  against the freshness SLI.
- Results are applied in arrival order. An old failure processed after newer successes can bump
  `consecutive_failures` once; check affected monitors afterwards.
- The checks queue retains messages for 1 hour. Check the current AWS documentation for how retention
  is counted for redriven messages; old messages may expire soon after being moved back.

### Purge the DLQ

Deletes every message in the DLQ and cannot be undone. Do it only after you have captured what you
need for the postmortem.

```sh
aws sqs purge-queue --queue-url "$DLQURL"
```

## Escalation / follow-up

- Many messages at once means a systemic failure: find and fix it before redriving, or they return
  to the DLQ.
- A repeating `monitor_id` is a bug that silently disables monitoring for that monitor: open an issue.
- Record: message count, arrival window, sample bodies (no secrets: bodies contain only IDs and
  timestamps), cause, and whether you redrove or purged.
