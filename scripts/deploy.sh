#!/usr/bin/env bash
# Shared deploy helper — run on the VPS from each service directory.
set -euo pipefail

BRANCH="${DEPLOY_BRANCH:-main}"
BUILD_NO_CACHE="${BUILD_NO_CACHE:-false}"
PULL_IMAGES="${PULL_IMAGES:-true}"
NEEDS_CADDY_NET="${NEEDS_CADDY_NET:-true}"
NEEDS_RAG_NET="${NEEDS_RAG_NET:-false}"

if [ -f docker-compose.yaml ]; then
  COMPOSE_FILE="docker-compose.yaml"
elif [ -f docker-compose.yml ]; then
  COMPOSE_FILE="docker-compose.yml"
else
  echo "No docker-compose file found in $(pwd)" >&2
  exit 1
fi

compose() {
  docker compose -f "$COMPOSE_FILE" "$@"
}

ensure_network() {
  local name="$1"
  if ! docker network inspect "$name" >/dev/null 2>&1; then
    docker network create "$name"
    echo "Created network '$name'"
  fi
}

if [ "$NEEDS_CADDY_NET" = "true" ]; then
  ensure_network caddy
fi
if [ "$NEEDS_RAG_NET" = "true" ]; then
  ensure_network greenbrain_rag
fi

git fetch origin "$BRANCH"
git reset --hard "origin/$BRANCH"

if [ "$PULL_IMAGES" = "true" ]; then
  compose pull --ignore-buildable 2>/dev/null || true
fi

BUILD_ARGS=()
if [ "$BUILD_NO_CACHE" = "true" ]; then
  BUILD_ARGS+=(--no-cache)
fi

compose build "${BUILD_ARGS[@]}"
compose up -d --remove-orphans
docker image prune -f

compose ps
