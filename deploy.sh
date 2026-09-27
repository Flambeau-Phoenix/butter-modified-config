#!/usr/bin/env bash
# ==============================================================================
# Butter AI Proxy Gateway - Build & Deployment Script
# ==============================================================================
# Usage:
#   ./deploy.sh              Build, test, install to /usr/local/bin/butter, restart service
#   ./deploy.sh --no-deploy  Build and test only (leaves running service untouched)
# ==============================================================================
set -euo pipefail

cd "$(dirname "$0")"
BIN=/tmp/butter-new
TARGET=/usr/local/bin/butter

echo "==> Building Butter binary (cmd/butter)"
go build -o "$BIN" ./cmd/butter/

echo "==> Running static analysis (go vet)"
go vet ./internal/... ./cmd/...

echo "==> Running test suite (go test)"
go test ./internal/...

if [ "${1:-}" = "--no-deploy" ]; then
  echo "==> Build complete: $BIN (not deployed)"
  exit 0
fi

stamp=$(date +%F-%H%M%S)
if [ -f "$TARGET" ]; then
  echo "==> Backing up current binary: $TARGET -> $TARGET.bak-$stamp"
  sudo cp -a "$TARGET" "$TARGET.bak-$stamp"
fi

echo "==> Installing new binary to $TARGET"
sudo install -m 0755 "$BIN" "$TARGET"

echo "==> Restarting butter.service"
sudo systemctl restart butter
sleep 1

STATUS=$(systemctl is-active butter || true)
echo "==> butter.service is $STATUS"

if [ "$STATUS" = "active" ]; then
  echo "==> Deployment successful!"
else
  echo "==> ERROR: Service is not active. Check logs with: sudo journalctl -u butter -n 50"
  exit 1
fi
