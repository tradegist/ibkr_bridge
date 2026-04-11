#!/bin/sh
# CGI script: starts the ib-gateway container via Docker socket

if [ "$ENV" = "local" ]; then
  printf "Content-Type: application/json\r\nAccess-Control-Allow-Origin: *\r\n\r\n"
else
  printf "Content-Type: application/json\r\n\r\n"
fi

if [ "$REQUEST_METHOD" != "POST" ]; then
  printf '{"error":"method not allowed"}'
  exit 0
fi

label_filters="--filter label=com.docker.compose.service=ib-gateway"
if [ -n "$COMPOSE_PROJECT_NAME" ]; then
  label_filters="--filter label=com.docker.compose.project=$COMPOSE_PROJECT_NAME $label_filters"
fi

container_id=$(sh -c "docker ps -aq $label_filters" | head -n 1)

if [ -n "$container_id" ]; then
  result=$(docker start "$container_id" 2>&1)
  exit_code=$?
else
  result="ib-gateway container not found"
  exit_code=1
fi

if [ $exit_code -eq 0 ]; then
  printf '{"status":"started"}'
else
  detail=$(echo "$result" | tail -1 | tr '"\\' "' ")
  printf '{"status":"error","detail":"%s"}' "$detail"
fi
