#!/usr/bin/env bash
# Redeploy nudgelabapi from GitHub (SPEC §14.0). Run on the API server as ubuntu:
#
#   bash /srv/nudgelabapi/deploy/deploy.sh            # latest main
#   bash /srv/nudgelabapi/deploy/deploy.sh v1.0.0     # a tag (also how you roll back)
#
# Stops at the first failed step. Never runs migrations: if the release has new ones, it stops so they can be
# reviewed and applied by hand with the migrate login (SPEC §14.1), then you run it again.

set -euo pipefail
APP_DIR=/srv/nudgelabapi
REF="${1:-main}"
cd "$APP_DIR"

if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  echo "STOP: $APP_DIR has local changes to tracked files; code only changes through git (SPEC §14.0)." >&2
  git status --short >&2
  exit 1
fi

before=$(git rev-parse --short HEAD)
echo "== fetching ($REF)"
git fetch --tags --prune origin
if [ "$REF" = "main" ]; then
  git checkout -q main
  git merge --ff-only -q origin/main
else
  git checkout -q --detach "$REF"
fi
after=$(git rev-parse --short HEAD)
echo "   $before -> $after"

echo "== dependencies"
venv/bin/pip install -q -r requirements.lock

echo "== migrations"
current=$(venv/bin/alembic current 2>/dev/null | awk 'NF {print $1; exit}')
head=$(venv/bin/alembic heads 2>/dev/null | awk 'NF {print $1; exit}')
if [ -z "$current" ]; then
  echo "STOP: can't read the database's migration version (alembic current). See deploy/README.md." >&2
  exit 2
fi
if [ "$current" != "$head" ]; then
  echo "STOP: this release has migrations the database doesn't ($current -> $head):" >&2
  venv/bin/alembic history -r "$current:head" >&2
  echo "Review alembic/versions/, run 'venv/bin/alembic upgrade head', grant the app login on any new" >&2
  echo "tables (deploy/db-logins.sql), then run this script again." >&2
  exit 2
fi
echo "   at $head"

echo "== restart"
sudo systemctl restart nudgelabapi nudgelabapi-worker
for _ in 1 2 3 4 5 6 7 8 9 10; do
  if curl -fsS http://127.0.0.1:8002/api/v1/health >/dev/null 2>&1; then break; fi
  sleep 1
done
curl -fsS http://127.0.0.1:8002/api/v1/health
echo
systemctl is-active --quiet nudgelabapi-worker || { echo "FAIL: worker not running (journalctl -u nudgelabapi-worker)" >&2; exit 3; }
echo "== deployed $after"
