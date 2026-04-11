#!/bin/sh
# Entrypoint wrapper: hashes VNC_SERVER_PASSWORD into a bcrypt hash
# so Caddy can use it for basic_auth on the VNC domain.

if [ -n "$VNC_SERVER_PASSWORD" ]; then
  export VNC_BASIC_AUTH_HASH=$(caddy hash-password --plaintext "$VNC_SERVER_PASSWORD")
fi

exec caddy run --config /etc/caddy/Caddyfile --adapter caddyfile
