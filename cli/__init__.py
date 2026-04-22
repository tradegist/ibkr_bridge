"""IBKR Bridge CLI — project-specific configuration.

Sets up CoreConfig and exposes IBKR Bridge-specific helpers used by
project-specific commands (order).
"""

import json
import os
import urllib.error
import urllib.request
from pathlib import Path

from cli.core import CoreConfig, die, env, set_config

PROJECT_DIR = Path(__file__).resolve().parent.parent
PROJECT_NAME = "ibkr-bridge"
REMOTE_DIR = f"/opt/{PROJECT_NAME}"


# ── IBKR Bridge-specific helpers ────────────────────────────────────

def _compose_env() -> dict[str, str]:
    """Compute derived env vars for docker compose commands."""
    return {}


def _droplet_size() -> str:
    override = os.environ.get("DROPLET_SIZE", "")
    if override:
        return override
    heap = int(env("JAVA_HEAP_SIZE", "768"))
    if heap <= 1024:
        return "s-1vcpu-2gb"
    elif heap <= 3072:
        return "s-2vcpu-4gb"
    elif heap <= 6144:
        return "s-4vcpu-8gb"
    else:
        return "s-8vcpu-16gb"


def _pre_sync_hook() -> None:
    """Validate env vars before sync."""
    pass


_BRIDGE_URLS: dict[str, str] = {
    "local": "http://localhost:15101",
}


def bridge_api(path: str, method: str = "POST", data: object = None) -> object:
    bridge_env = os.environ.get("BRIDGE_ENV") or os.environ.get("DEFAULT_CLI_BRIDGE_ENV") or "prod"
    base_url = _BRIDGE_URLS.get(bridge_env)
    if base_url:
        url = f"{base_url}{path}"
    else:
        domain = env("SITE_DOMAIN")
        url = f"https://{domain}{path}"
    token = env("API_TOKEN")
    body = json.dumps(data).encode() if data else None
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    if body:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        content = e.read().decode()
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            die(f"Request failed ({e.code}): {content}")


# ── CoreConfig for IBKR Bridge project ─────────────────────────────

_CONFIG = CoreConfig(
    project_name=PROJECT_NAME,
    project_dir=PROJECT_DIR,
    terraform_vars={
        "do_token": "DO_API_TOKEN",
        "java_heap_size": "JAVA_HEAP_SIZE",
        "droplet_size": "DROPLET_SIZE",
        "vnc_domain": "VNC_DOMAIN",
        "site_domain": "SITE_DOMAIN",
    },
    required_env=[
        "DO_API_TOKEN", "TWS_USERID", "TWS_PASSWORD",
        "VNC_SERVER_PASSWORD", "API_TOKEN",
        "VNC_DOMAIN", "SITE_DOMAIN",
    ],
    service_map={
        "gateway": "ib-gateway",
        "ib-gateway": "ib-gateway",
        "novnc": "novnc",
        "vnc": "novnc",
        "caddy": "caddy",
        "relay": "bridge",
        "bridge": "bridge",
        "controller": "gateway-controller",
        "gateway-controller": "gateway-controller",
    },
    post_deploy_message="Open the VNC URL and complete 2FA",
    post_resume_message="Open https://{VNC_DOMAIN} to complete 2FA",
    compose_env_fn=_compose_env,
    size_selector_fn=_droplet_size,
    route_prefixes=["/ibkr"],
    pre_sync_hook=_pre_sync_hook,
)

set_config(_CONFIG)
