#!/usr/bin/env bash
# Executed ON the host (the server): snapshot the backend SQLite database from
# inside the running container into deploy/data/backups/ (the /data volume).
set -euo pipefail
cd "${YTUI_DEPLOY_DIR:-$HOME/ytui-deploy}"
docker compose -f deploy/docker-compose.yml exec -T ytui-backend \
    python -m ytui_server.backup --keep-days 14
