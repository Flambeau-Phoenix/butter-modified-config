#!/usr/bin/env bash
# ==============================================================================
# Butter AI Proxy Gateway - Universal Deployment & Config Script
# ==============================================================================
# Usage:
#   ./deploy.sh                      Validate and deploy ./config.yaml
#   ./deploy.sh /path/to/config.yaml Validate and deploy custom config path
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="${1:-$SCRIPT_DIR/config.yaml}"
TARGET_BIN="/usr/local/bin/butter"

echo "==> Butterproxy Deployment"

# 1. Check & Validate Configuration
if [ ! -f "$CONFIG_FILE" ]; then
  if [ -f "$SCRIPT_DIR/config.example.yaml" ]; then
    echo "==> No config.yaml found. Initializing from config.example.yaml..."
    cp "$SCRIPT_DIR/config.example.yaml" "$CONFIG_FILE"
  else
    echo "ERROR: Config file $CONFIG_FILE not found!"
    exit 1
  fi
fi

echo "==> Validating configuration: $CONFIG_FILE"
if command -v python3 >/dev/null 2>&1; then
  python3 "$SCRIPT_DIR/butterproxy_config_manager.py" validate --config "$CONFIG_FILE"
else
  echo "WARNING: python3 not found, skipping config syntax pre-validation."
fi

# 2. Check or Build Butter Binary
if [ -d "$SCRIPT_DIR/cmd/butter" ]; then
  echo "==> Building Butter binary from local source..."
  go build -o "/tmp/butter-new" "$SCRIPT_DIR/cmd/butter/"
  if [ -f "$TARGET_BIN" ]; then
    sudo cp -a "$TARGET_BIN" "$TARGET_BIN.bak-$(date +%F-%H%M%S)"
  fi
  sudo install -m 0755 "/tmp/butter-new" "$TARGET_BIN"
elif [ -n "${BUTTER_SRC:-}" ] && [ -d "$BUTTER_SRC/cmd/butter" ]; then
  echo "==> Building Butter binary from source at $BUTTER_SRC..."
  (cd "$BUTTER_SRC" && go build -o "/tmp/butter-new" ./cmd/butter/)
  if [ -f "$TARGET_BIN" ]; then
    sudo cp -a "$TARGET_BIN" "$TARGET_BIN.bak-$(date +%F-%H%M%S)"
  fi
  sudo install -m 0755 "/tmp/butter-new" "$TARGET_BIN"
elif [ -d "$SCRIPT_DIR/../butter-src/cmd/butter" ]; then
  echo "==> Building Butter binary from sibling directory ../butter-src..."
  (cd "$SCRIPT_DIR/../butter-src" && go build -o "/tmp/butter-new" ./cmd/butter/)
  if [ -f "$TARGET_BIN" ]; then
    sudo cp -a "$TARGET_BIN" "$TARGET_BIN.bak-$(date +%F-%H%M%S)"
  fi
  sudo install -m 0755 "/tmp/butter-new" "$TARGET_BIN"
elif [ -x "$TARGET_BIN" ]; then
  echo "==> Using existing installed binary at $TARGET_BIN"
elif command -v butter >/dev/null 2>&1; then
  TARGET_BIN="$(command -v butter)"
  echo "==> Using existing butter binary found in PATH: $TARGET_BIN"
else
  echo "==> Notice: No butter binary or source tree found in this repo."
  echo "    To build from source: BUTTER_SRC=/path/to/butter-src ./deploy.sh"
  echo "    Or clone Butter: git clone https://github.com/temikus/butter.git ../butter-src"
  echo "    If running on another machine, make sure /usr/local/bin/butter is installed."
fi

# 3. Service Management
if command -v systemctl >/dev/null 2>&1 && systemctl list-unit-files butter.service >/dev/null 2>&1; then
  echo "==> Restarting butter.service..."
  sudo systemctl restart butter
  sleep 1
  STATUS=$(systemctl is-active butter || true)
  echo "==> butter.service is $STATUS"
else
  echo "==> Config validated and ready!"
  echo "    To run manually: butter --config $CONFIG_FILE"
  echo "    Or set up a systemd service following the instructions in README.md"
fi
