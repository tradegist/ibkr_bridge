#!/bin/sh
# CGI script: returns ib-gateway container status

printf "Content-Type: application/json\r\n\r\n"

state=$(docker inspect --format '{{.State.Status}}' "${COMPOSE_PROJECT_NAME}-ib-gateway-1" 2>/dev/null)
if [ -z "$state" ]; then
  state="not found"
fi

printf '{"container":"ib-gateway","state":"%s"}' "$state"
