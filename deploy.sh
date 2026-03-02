#!/bin/bash
# Local deployment script.  Copies .env, updates code on the remote host,
# rebuilds and restarts Docker containers, runs migrations/collectstatic and
# executes a health check.  Contains a simple rollback mechanism.

set -euo pipefail

SERVER_USER="leadrisks.com_487i3cldg3e"
SERVER_HOST="161.97.101.121"
REMOTE_DIR="~/sigma17/sigma-17-backend"
REPO_URL="https://github.com/TahaNaqv/sigma-17-backend.git"
BRANCH="${1:-master}"

GREEN='\033[0;32m'; RED='\033[0;31m'; YELLOW='\033[1;33m'; NC='\033[0m'

log(){ echo -e "${GREEN}[deploy]${NC} $*"; }
warn(){ echo -e "${YELLOW}[deploy]${NC} $*"; }
error(){ echo -e "${RED}[deploy]${NC} $*"; exit 1; }

if [[ ! -f .env.production ]]; then
    error ".env.production not found in $(pwd)"
fi

if [[ ! -d .git ]]; then
    error "not a git repository"
fi

if ! git diff-index --quiet HEAD --; then
    warn "uncommitted changes detected – stashing them locally"
    git stash --include-untracked
fi

log "copying .env.production → ${REMOTE_DIR}/.env"
scp .env.production "${SERVER_USER}@${SERVER_HOST}:${REMOTE_DIR}/.env"

ssh "${SERVER_USER}@${SERVER_HOST}" "BRANCH=${BRANCH} bash -s" <<'REMOTE_EOF'
set -euo pipefail

GREEN='\033[0;32m'; RED='\033[0;31m'; NC='\033[0m'
log(){ echo -e "${GREEN}[server]${NC} $1"; }
err(){ echo -e "${RED}[server]${NC} $1"; exit 1; }

cd ~/sigma17 || (mkdir -p ~/sigma17 && cd ~/sigma17)
if [ ! -d "sigma-17-backend/.git" ]; then
    log "cloning repository"
    git clone "$REPO_URL" sigma-17-backend
fi
cd sigma-17-backend

rollback() {
    err "rolling back to ${PREV_HASH}"
    git reset --hard "$PREV_HASH"
    docker-compose down || true
    docker-compose build web
    docker-compose up -d
    exit 1
}

PREV_HASH=$(git rev-parse HEAD)
log "previous commit: $PREV_HASH"

log "fetching and resetting ${BRANCH}"
git fetch origin
git checkout "$BRANCH" || rollback
git reset --hard "origin/$BRANCH" || rollback

log "building web image"
if ! docker-compose build web; then
    err "build failed"
    rollback
fi

log "cleaning up old containers and volumes"
docker-compose down || true

log "starting containers"
if ! docker-compose up -d; then
    err "docker-compose up failed"
    rollback
fi

log "waiting for database to be ready"
sleep 5

log "applying migrations"
docker-compose exec -T web python manage.py migrate --noinput

log "collecting static files"
docker-compose exec -T web python manage.py collectstatic --noinput

log "running health check"
./health-check.sh || { err "health check failed"; rollback; }

log "deployment finished successfully"
REMOTE_EOF

log "deployment script completed"
