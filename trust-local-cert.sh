#!/bin/bash
set -e

CERT_PATH="$(cd "$(dirname "$0")" && pwd)/certs/cert.pem"
KEYCHAIN="/Library/Keychains/System.keychain"

if [ ! -f "$CERT_PATH" ]; then
  echo "Certificate not found: $CERT_PATH"
  exit 1
fi

sudo security add-trusted-cert -d -r trustRoot -k "$KEYCHAIN" "$CERT_PATH"

echo "Local certificate trusted for macOS."
echo "Open https://localhost:8443"
