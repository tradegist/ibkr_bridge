#!/bin/sh
# Watches ib-gateway for unexpected exits and sends an email alert via Resend.
# A stop caused by 2FA timeout is identified by the presence of "Second Factor
# Authentication" in the final log lines and is silently ignored.

label_filters="--filter label=com.docker.compose.service=ib-gateway"
if [ -n "$COMPOSE_PROJECT_NAME" ]; then
  label_filters="$label_filters --filter label=com.docker.compose.project=$COMPOSE_PROJECT_NAME"
fi

send_alert() {
  container_id="$1"
  exit_code="$2"

  if [ -z "$RESEND_API_KEY" ] || [ -z "$ALERT_REPORT_EMAIL_TO" ]; then
    echo "[monitor] RESEND_API_KEY or ALERT_REPORT_EMAIL_TO not set — skipping alert"
    return
  fi

  from="${ALERT_EMAIL_FROM:-onboarding@resend.dev}"
  project="${COMPOSE_PROJECT_NAME:-ib-gateway}"
  logs=$(docker logs "$container_id" --tail=40 2>&1)

  payload=$(jq -n \
    --arg from "$from" \
    --arg to   "$ALERT_REPORT_EMAIL_TO" \
    --arg subj "[$project] ib-gateway stopped unexpectedly (exit $exit_code)" \
    --arg body "ib-gateway exited with code $exit_code.

Last 40 log lines:
$logs" \
    '{from: $from, to: [$to], subject: $subj, text: $body}')

  http_code=$(curl -s -o /dev/null -w '%{http_code}' \
    -X POST https://api.resend.com/emails \
    -H "Authorization: Bearer $RESEND_API_KEY" \
    -H "Content-Type: application/json" \
    -d "$payload")

  echo "[monitor] alert sent to $ALERT_REPORT_EMAIL_TO (HTTP $http_code)"
}

echo "[monitor] started, watching for ib-gateway exit events..."

while true; do
  # sh -c wrapper so $label_filters expands as separate words
  sh -c "docker events $label_filters --filter event=die --format '{{.ID}} {{.Actor.Attributes.exitCode}}'" \
  | while IFS=' ' read -r container_id exit_code; do
    echo "[monitor] ib-gateway stopped (exit $exit_code, container $container_id)"

    if docker logs "$container_id" --tail=50 2>&1 | grep -q "Second Factor Authentication"; then
      echo "[monitor] 2FA timeout detected — no alert"
    else
      echo "[monitor] unexpected stop — sending alert"
      send_alert "$container_id" "$exit_code"
    fi
  done

  echo "[monitor] docker events pipe closed, restarting in 5s..."
  sleep 5
done
