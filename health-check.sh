#!/bin/bash
# Simple health check for the local stack.  Returns 0 if healthy, 1
# otherwise.  Intended to be executed on the server as part of the
# deployment process; it may also be run manually.

set -euo pipefail
cd "$(dirname "$0")" || exit 1

if ! docker-compose ps web | grep -q Up; then
    echo "web container is not running"
    exit 1
fi

if ! docker-compose ps db | grep -q Up; then
    echo "db container is not running"
    exit 1
fi

if ! curl -fsS http://localhost:8000/ >/dev/null 2>&1; then
    echo "API did not respond on localhost:8000"
    exit 1
fi

echo "health check passed"
