#!/bin/bash
set -e

ACTION="${1:-start}"
ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"

case "$ACTION" in
  start)
    cd "$ROOT_DIR"
    docker-compose up -d --build
    echo "Started: http://localhost:8080"
    echo "Started: https://localhost:8443"
    ;;
  stop)
    cd "$ROOT_DIR"
    docker-compose down
    echo "Stopped hp-site and hp-app"
    ;;
  trust)
    "$ROOT_DIR/trust-local-cert.sh"
    ;;
  *)
    echo "Usage: ./manage-site.sh [start|stop|trust]"
    exit 1
    ;;
esac
