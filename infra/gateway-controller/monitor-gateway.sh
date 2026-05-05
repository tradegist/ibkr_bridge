#!/bin/sh
# Watches ib-gateway for unexpected exits and sends an email alert via Resend.
# A 2FA timeout stops the (restarted) container to break the restart loop and
# sends an alert prompting the user to authenticate via VNC.

# POSIX sh has no arrays; use positional params to hold filter args so each
# --filter value stays a single shell word even if it contains whitespace.
set -- --filter "label=com.docker.compose.service=ib-gateway"
if [ -n "$COMPOSE_PROJECT_NAME" ]; then
  set -- "$@" --filter "label=com.docker.compose.project=$COMPOSE_PROJECT_NAME"
fi

stop_gateway() {
  # Stops the running ib-gateway container (if any) to break the restart loop
  # after a 2FA timeout. Uses the same label filters as the event watcher.
  running_id=$(docker ps -q "$@")
  if [ -n "$running_id" ]; then
    echo "[monitor] stopping restarted ib-gateway ($running_id) to break restart loop"
    docker stop "$running_id"
  fi
}

send_alert() {
  container_id="$1"
  exit_code="$2"
  alert_type="${3:-unexpected}"  # "2fa" or "unexpected"

  if [ -z "$RESEND_API_KEY" ] || [ -z "$ALERT_REPORT_EMAIL_TO" ]; then
    echo "[monitor] RESEND_API_KEY or ALERT_REPORT_EMAIL_TO not set — skipping alert"
    return
  fi

  from="${ALERT_EMAIL_FROM:-onboarding@resend.dev}"
  project="${COMPOSE_PROJECT_NAME:-ib-gateway}"

  if [ "$alert_type" = "2fa" ]; then
    subj="[$project] ib-gateway needs 2FA — authenticate via VNC"
    body="ib-gateway exited after a 2FA timeout and has been stopped to prevent a restart loop.

Project:      $project
Container ID: $container_id

Authenticate via VNC, then start the gateway:

  curl -X POST https://<your-domain>/cgi-bin/start-gateway
"
  else
    subj="[$project] ib-gateway stopped unexpectedly (exit $exit_code)"
    body="ib-gateway exited unexpectedly.

Project:      $project
Container ID: $container_id
Exit code:    $exit_code

Logs are not included in this alert (they may contain credentials or
account data). Retrieve them on the droplet:

  docker logs $container_id --tail=200

If the container has been pruned, find the most recent one by label:

  docker ps -a \\
    --filter label=com.docker.compose.service=ib-gateway \\
    --filter label=com.docker.compose.project=$project
"
  fi

  payload=$(jq -n \
    --arg from "$from" \
    --arg to   "$ALERT_REPORT_EMAIL_TO" \
    --arg subj "$subj" \
    --arg body "$body" \
    '{from: $from, to: [$to], subject: $subj, text: $body}')

  response_file=$(mktemp)
  http_code=$(curl -sS -o "$response_file" -w '%{http_code}' \
    --connect-timeout 5 --max-time 15 --retry 2 --retry-delay 2 \
    -X POST https://api.resend.com/emails \
    -H "Authorization: Bearer $RESEND_API_KEY" \
    -H "Content-Type: application/json" \
    -d "$payload")
  curl_status=$?
  response_body=$(cat "$response_file")
  rm -f "$response_file"

  case "$http_code" in
    2*)
      echo "[monitor] alert sent to $ALERT_REPORT_EMAIL_TO (HTTP $http_code)"
      ;;
    *)
      echo "[monitor] failed to send alert to $ALERT_REPORT_EMAIL_TO: curl_exit=$curl_status http=$http_code body=$response_body" >&2
      return 1
      ;;
  esac
}

echo "[monitor] started, watching for ib-gateway exit events..."

while true; do
  docker events "$@" --filter event=die --format '{{.Actor.ID}} {{.Actor.Attributes.exitCode}}' \
  | while IFS=' ' read -r container_id exit_code; do
    echo "[monitor] ib-gateway stopped (exit $exit_code, container $container_id)"

    if docker logs "$container_id" --tail=50 2>&1 | grep -q "Second Factor Authentication"; then
      echo "[monitor] 2FA timeout detected — stopping container and sending alert"
      stop_gateway "$@"
      send_alert "$container_id" "$exit_code" "2fa"
    else
      echo "[monitor] unexpected stop — sending alert"
      send_alert "$container_id" "$exit_code"
    fi
  done

  echo "[monitor] docker events pipe closed, restarting in 5s..."
  sleep 5
done
