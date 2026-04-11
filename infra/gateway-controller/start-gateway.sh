#!/bin/sh
# CGI script: starts the ib-gateway container via Docker socket

printf "Content-Type: application/json\r\n\r\n"

if [ "$REQUEST_METHOD" != "POST" ]; then
  printf '{"error":"method not allowed"}'
  exit 0
fi

result=$(docker start "${COMPOSE_PROJECT_NAME}-ib-gateway-1" 2>&1)
exit_code=$?

if [ $exit_code -eq 0 ]; then
  printf '{"status":"started"}'
else
  detail=$(echo "$result" | tail -1 | tr '"\\' "' ")
  printf '{"status":"error","detail":"%s"}' "$detail"
fi
