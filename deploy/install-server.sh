#!/usr/bin/env bash
# Pull the latest code and (re)build + start the Naggy container on the serve host.
# Run this from inside a clone of the repo on that host.
#
# Prereqs (one-time on the host):
#   - docker/config.toml  (copy from ../config.example.toml; set database.path = "/data/naggy.db")
#   - docker/.env         (optional: NAGGY_PORT=..., NAGGY_HA_TOKEN=...)
set -euo pipefail

cd "$(dirname "$0")/.."

git pull --ff-only
cd docker
docker compose up -d --build

echo "waiting for health..."
sleep 4
port="${NAGGY_PORT:-8090}"
curl -fsS "http://localhost:${port}/healthz" && echo && echo "naggy is up."
