#!/bin/bash
set -e

ACTION="${1:-start}"
ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
DOCKER_CONFIG_DIR="$ROOT_DIR/.docker-config"

mkdir -p "$DOCKER_CONFIG_DIR"
export DOCKER_CONFIG="$DOCKER_CONFIG_DIR"

case "$ACTION" in
  start)
    cd "$ROOT_DIR"
    docker build -t hp-web . >/dev/null 2>&1 || true
    docker rm -f hp-site >/dev/null 2>&1 || true
    docker run -d --name hp-site -p 8080:80 -p 8443:443 \
      -v "$ROOT_DIR:/usr/share/nginx/html:ro" \
      -v "$ROOT_DIR/certs:/etc/nginx/certs:ro" \
      hp-web >/dev/null
    echo "Started: http://localhost:8080"
    echo "Started: https://localhost:8443"
    ;;
  stop)
    docker rm -f hp-site >/dev/null 2>&1 || true
    echo "Stopped hp-site"
    ;;
  trust)
    "$ROOT_DIR/trust-local-cert.sh"
    ;;
  *)
    echo "Usage: ./manage-site.sh [start|stop|trust]"
    exit 1
    ;;
esac
