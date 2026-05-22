---
applyTo: "cli/**,Makefile,docker-compose*.yml,env_examples/**,terraform/**"
---

# `cli/` — Operator CLI

Stdlib-only Python CLI for deploying and operating the bridge stack. Invoked via `python3 -m cli <command>` or `make`.

## Sibling project mirror (CRITICAL)

Any change to `cli/core/deploy.py`, `cli/core/destroy.py`, or `cli/core/sync.py` must be mirrored in `relayport` in the same session — and vice versa. Includes Terraform state management, reserved-IP handling, rsync exclusions, env-file push logic, and compose startup commands.

## Makefile must mirror CLI arguments

- When adding a new parameter to a `cli/` command, always add the corresponding `$(if $(VAR),--flag $(VAR))` to the Makefile target so `make <target> VAR=value` works.
- **CLI parameters that are optional in the Makefile must be named flags (`--currency`, `--exchange`), never positional args.** When the Makefile uses `$(if $(VAR),...)`, omitting `VAR` omits the entire argument — if the CLI parameter is positional, downstream args shift into the wrong position and get silently misparsed.

## Environment files

Configuration is split into three env files to separate concerns:

- **`.env`** — App-level config + deployment mode + secrets (TWS credentials, API tokens, domains, `DEPLOY_MODE`, `SHARED_NETWORK`). Pushed to the droplet by `make sync` / `make deploy`.
- **`.env.droplet`** — Developer-machine-only vars never pushed to the droplet (`DROPLET_IP`, `SSH_KEY`, `DROPLET_SIZE`, `DEFAULT_CLI_ENV`). Only read by `cli/` and the Makefile.
- **`.env.test`** — E2E test config (paper-account TWS credentials). Used only in `docker-compose.test.yml`.

Templates live in `env_examples/`. `make setup` auto-copies `env_examples/env` → `.env` and `env_examples/env.droplet` → `.env.droplet` if missing. **`.env.test` is intentionally not auto-created** — `make setup` only prints a NOTE; the operator must `cp env_examples/env.test .env.test` and set real paper credentials before running E2E.

## Local development

- **`.venv`** is the project's virtual environment. Created by `make setup` using Homebrew Python.
- **Auto-activation** via a `chpwd` hook in `~/.zshrc`.
- **`make setup`** creates `.venv` (if missing), installs all deps (`requirements-dev.txt` + `services/bridge/requirements.txt`), and writes a `.pth` file (`ibkr-bridge.pth`) adding `services/bridge/` to `sys.path` so `from bridge_models import ...`, `from client import ...`, `from bridge_routes import ...` work without `PYTHONPATH`.
- **`.venv/` is gitignored.**
- **`docker-compose.local.yml`** adds bind mounts that shadow the `COPY`'d files in the image with your local source tree (`:ro`). Code changes are visible on container restart — no rebuild needed.
- **`make sync` respects `DEFAULT_CLI_ENV`.** `local` → restart local compose stack. `prod` (default) → full CLI sync to the droplet. Override per-command with `ENV=local` or `ENV=prod`.
- **`make logs` also respects `DEFAULT_CLI_ENV`.** `make logs S=bridge` streams local container logs when local, droplet logs when prod.

## CoreConfig (project-specific values)

`cli/__init__.py` constructs the `CoreConfig` that `cli/core/*` reads. Current values:

- **`route_prefixes=["/ibkr"]`** — single prefix, no trailing slash. Validation uses `path.startswith(f"{prefix}/")`, so `/ibkr` covers `/ibkr/order`, `/ibkr/trades`, `/ibkr/ws/events`, etc.
- **`required_env`** — `DO_API_TOKEN`, `TWS_USERID`, `TWS_PASSWORD`, `VNC_SERVER_PASSWORD`, `API_TOKEN`, `VNC_DOMAIN`, `SITE_DOMAIN`. Currently enforced for standalone deploy when any required var is missing or empty; `sync` and shared deploy do not validate this list.
- **`service_map`** — maps CLI aliases to compose service names (`{"gateway": "ib-gateway", "ib-gateway": "ib-gateway", "novnc": "novnc", "vnc": "novnc", "caddy": "caddy", "relay": "bridge", "bridge": "bridge", "controller": "gateway-controller", "gateway-controller": "gateway-controller"}`). Used by `make logs S=<alias>`, `make sync S=<alias>`, etc. Aliases are intentional ergonomic shortcuts (e.g. `vnc` → `novnc`, `gateway` → `ib-gateway`).
- **`terraform_vars`** — maps env var names to Terraform variable names for `TF_VAR_*` export.

When adding a new service: extend `service_map` (alias + container name), `route_prefixes` (if it has a Caddy route), `required_env` (if it has an auth token), and the relevant terraform vars.

## Deployment modes

Controlled by `DEPLOY_MODE` in `.env` (required, validated before any deploy or sync).

### Standalone Mode (`DEPLOY_MODE=standalone`)

- Set `DO_API_TOKEN` in `.env`. `make deploy` runs Terraform to create a droplet + firewall + reserved IP, then the CLI rsyncs project files, pushes `.env`, and runs `docker compose up -d --build`.
- Terraform only creates infrastructure — cloud-init installs Docker and creates the project directory. The CLI handles all file transfer and service startup.
- After deploy, add `DROPLET_IP` from terraform output to `.env.droplet` for `make sync`.
- `DO_API_TOKEN` can be removed after first deploy for security.

### Shared Mode (`DEPLOY_MODE=shared`)

- Set `DROPLET_IP`, `SSH_KEY`, and `SHARED_NETWORK` in `.env` (no `DO_API_TOKEN` needed). `SHARED_NETWORK` is **required** — CLI fails fast with a clear error if unset. Putting it in `.env` (rather than `.env.droplet`) means a manual `docker compose up` on the droplet — bypassing the CLI — also finds it.
- `make deploy` rsyncs files, pushes `.env`, ensures the shared Docker network exists on the droplet, and starts services with `docker-compose.shared.yml` + `docker-compose.shared-network.yml` overlays.
- `docker-compose.shared.yml` disables Caddy (the host project runs it). `docker-compose.shared-network.yml` marks the shared network as `external: true`.
- Caddy snippet files must be deployed to the host project's Caddy to enable routing.
- `make sync` uses both overlays automatically.

### Shared Network (`SHARED_NETWORK`)

- Base `docker-compose.yml` uses `name: ${SHARED_NETWORK:-}` on the default network. Unset → isolated project-scoped network. Set → CLI **always** applies `docker-compose.shared-network.yml`, adding `external: true` on top.
- `SHARED_NETWORK` may live in either `.env` or `.env.droplet`. The CLI loads both and explicitly injects `SHARED_NETWORK='<value>'` into the remote `docker compose` command env via `shared_network_compose_env()`. Shell-env precedence beats the droplet's `.env`. `.env` is recommended since it's the only file scp'd to the droplet.
- **The CLI is the network owner.** Before `docker compose up`, `cli/core/__init__.py::ensure_shared_network` runs `docker network inspect <name> >/dev/null 2>&1 || docker network create <name>` on the droplet. Idempotent. Removes any ordering dependency between projects.
- **Running `docker compose up` manually on the droplet bypasses the CLI's overlay assembly** and falls back to the base `name:` only, re-introducing the "network was not created for project X" warning. Always go through `make sync` / `make deploy`.

## Droplet sizing

- Droplet size is auto-selected based on `JAVA_HEAP_SIZE` (IB Gateway's Java heap). Higher heap = larger droplet.
- Override with `DROPLET_SIZE` in `.env.droplet` to use a specific slug regardless of heap.
- `cli/__init__.py::_droplet_size()` implements the sizing logic. `cli/core/resume.py` uses `cfg.droplet_size()` which delegates to the same function.

| Heap (MB) | Droplet | RAM |
| --- | --- | --- |
| ≤ 1024 | `s-1vcpu-2gb` | 2 GB |
| ≤ 3072 | `s-2vcpu-4gb` | 4 GB |
| ≤ 6144 | `s-4vcpu-8gb` | 8 GB |
| > 6144 | `s-8vcpu-16gb` | 16 GB |

## Build & Deploy commands

```bash
make deploy    # Standalone: Terraform | Shared: rsync + compose
make sync      # Push .env to droplet + restart services
make sync LOCAL_FILES=1  # rsync files + rebuild + restart
make destroy   # Terraform destroy
make pause     # Snapshot + delete droplet (save costs)
make resume    # Restore from snapshot
make order Q=10 SYM=AAPL T=MKT  # Place an order
make e2e       # Run E2E tests (starts/stops stack)
make lint      # Run ruff linter (FIX=1 to auto-fix)
```

Direct CLI:
```bash
python3 -m cli deploy
python3 -m cli sync --local-files
python3 -m cli order 10 AAPL MKT
python3 -m cli order -5 TSLA LMT 250.00
```

## Deployment Model (MANDATORY)

- **`make sync LOCAL_FILES=1` uses rsync** to transfer files from the local working tree to `/opt/ibkr-bridge/` on the droplet. Does NOT use git on the droplet — no clone, no deploy keys, no GitHub access needed from the server.
- **Guards:** Must be on `main` branch with a clean working tree. Ensures rsync deploys a known committed state.
- **`--delete` flag:** rsync removes files on the droplet that no longer exist locally. Correct for renames/deletions, dangerous for server-generated files.
- **Invariant: the project directory (`/opt/ibkr-bridge/`) contains only source files.** No service or container may write into the project directory. All runtime-generated data (databases, caches, certificates) MUST use Docker named volumes (e.g. `caddy-data:/data`). Docker volumes live under `/var/lib/docker/volumes/`, safe from rsync `--delete`.
- **When adding new runtime data**, create a Docker named volume in `docker-compose.yml` and mount it. Never write to a path inside `/opt/ibkr-bridge/`.
- **`.deployed-sha`** is the only server-side file inside the project directory. Written by `cli/core/sync.py` after each `--local-files` sync; excluded from rsync `--delete`. Records the deployed commit SHA.
- **rsync exclusions:** `.git/`, `.env`, `.env.droplet`, `.env.test`, `.deployed-sha`, and everything in `.gitignore`.

## Docker

- **Never use `env_file:` in service definitions.** Always declare each env var explicitly in the `environment:` block with `${VAR}` interpolation. Prevents `.env` from leaking across compose override files.
- **`.dockerignore` uses an allowlist** (`*` then `!services/bridge/**`). Tests, `__pycache__`, and the Dockerfile itself are re-excluded.
- The bridge Dockerfile uses directory COPYs (`COPY services/bridge/client/ ./client/`, `COPY services/bridge/bridge_routes/ ./bridge_routes/`) so new files are picked up automatically.
- **Never nest bind mounts in compose override files.** Docker auto-creates empty host directories to back nested mount points, shadowing real content on restart. Mount at separate paths outside `/app` instead.
