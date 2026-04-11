#!/bin/sh
# CGI script: returns ib-gateway container status

if [ "$ENV" = "local" ]; then
  printf "Content-Type: application/json\r\nAccess-Control-Allow-Origin: *\r\n\r\n"
else
  printf "Content-Type: application/json\r\n\r\n"
fi

state=$(docker inspect --format '{{.State.Status}}' "${COMPOSE_PROJECT_NAME}-ib-gateway-1" 2>/dev/null)
if [ -z "$state" ]; then
  state="not found"
fi

printf '{"container":"ib-gateway","state":"%s"}' "$state"
