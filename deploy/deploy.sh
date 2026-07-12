#!/usr/bin/env bash
# Executed ON the host (server) by the Forgejo Actions deploy job (via ssh).
# Pulls the latest master and rebuilds/restarts the backend container.
set -euo pipefail

REPO_DIR="${YTUI_DEPLOY_DIR:-$HOME/ytui-deploy}"

cd "$REPO_DIR"
git fetch origin master
git reset --hard origin/master

docker compose -f deploy/docker-compose.yml up -d --build
docker image prune -f

# Wait for the healthcheck to pass.
for _ in $(seq 1 30); do
    if curl -fsS http://127.0.0.1:8776/health >/dev/null 2>&1; then
        echo "deploy OK: $(curl -fsS http://127.0.0.1:8776/health)"
        exit 0
    fi
    sleep 2
done
echo "deploy FAILED: backend not healthy after 60s" >&2
docker compose -f deploy/docker-compose.yml logs --tail 50 ytui-backend >&2
exit 1
