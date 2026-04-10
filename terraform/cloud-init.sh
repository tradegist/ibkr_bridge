#!/bin/bash
set -euo pipefail

# ---------------------------------------------------------------------------
# Cloud-init script: Install Docker and prepare the project directory.
# This runs as root on first boot. NO SECRETS here — they are transferred
# separately by the CLI deploy command over SSH.
# ---------------------------------------------------------------------------

# Install Docker via official convenience script
curl -fsSL https://get.docker.com | sh
systemctl enable docker
systemctl start docker

# Create project directory — files arrive via rsync from the CLI
mkdir -p /opt/ibkr-bridge

# Directory is ready — the CLI deploy command will:
# 1. Rsync project files
# 2. Transfer .env with secrets
# 3. Run docker compose up -d
