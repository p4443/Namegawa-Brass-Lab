#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
CERT_DIR="$ROOT_DIR/certs"
CERT_PATH="$CERT_DIR/cert.pem"
KEY_PATH="$CERT_DIR/key.pem"

if [ -e "$CERT_PATH" ] || [ -e "$KEY_PATH" ]; then
  echo "Local certificate already exists in $CERT_DIR"
  echo "Remove both PEM files first if you intentionally need to regenerate them."
  exit 1
fi

mkdir -p "$CERT_DIR"
openssl req -x509 -newkey rsa:2048 -sha256 -nodes \
  -days 825 \
  -keyout "$KEY_PATH" \
  -out "$CERT_PATH" \
  -subj "/CN=localhost" \
  -addext "subjectAltName=DNS:localhost,IP:127.0.0.1,IP:::1" \
  -addext "keyUsage=digitalSignature,keyEncipherment" \
  -addext "extendedKeyUsage=serverAuth"

chmod 600 "$KEY_PATH"
chmod 644 "$CERT_PATH"

echo "Created local certificate: $CERT_PATH"
echo "Trust it with ./trust-local-cert.sh before opening https://localhost:8443"