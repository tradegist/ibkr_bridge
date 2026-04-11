#!/bin/sh
# CGI script: returns ib-gateway container status

if [ "$ENV" = "local" ]; then
  printf "Content-Type: application/json\r\nAccess-Control-Allow-Origin: *\r\n\r\n"
else
  printf "Content-Type: application/json\r\n\r\n"
fi

label_filters="--filter label=com.docker.compose.service=ib-gateway"
if [ -n "$COMPOSE_PROJECT_NAME" ]; then
  label_filters="--filter label=com.docker.compose.project=$COMPOSE_PROJECT_NAME $label_filters"
fi

container_id=$(sh -c "docker ps -aq $label_filters" | head -n 1)

if [ -n "$container_id" ]; then
  state=$(docker inspect --format '{{.State.Status}}' "$container_id" 2>/dev/null)
else
  state="not found"
fi

printf '{"container":"ib-gateway","state":"%s"}' "$state"
