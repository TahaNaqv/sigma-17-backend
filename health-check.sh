#!/bin/bash
# Simple health check for the local stack.  Returns 0 if healthy, non‑zero
# otherwise.  Intended to be executed on the server as part of the deployment
# process; it may also be run manually.

set -euo pipefail
cd "$(dirname "$0")" || exit 1

if ! command -v docker-compose >/dev/null 2>&1; then
    echo "docker-compose not available; this script must be run on the server"
    exit 1
fi

if ! docker-compose ps web | grep -q Up; then
    echo "web container is not running"
    exit 1
fi

if ! docker-compose ps db | grep -q Up; then
    echo "db container is not running"
    exit 1
fi

# hit the root URL and accept 200 or 404 as “healthy”
status=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/)

if [[ "$status" != "200" && "$status" != "404" ]]; then
    echo "unexpected HTTP status $status from localhost:8000"
    exit 1
fi

echo "health check passed (status $status)"
