#!/bin/bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_ROOT"

AGENT_NAME="SA-LogiFlow-Agent"
VERSION="0.1.0"
rm -rf "$PROJECT_ROOT/build" "$PROJECT_ROOT/dist" "/private/tmp/salogiflow-agent-pkgroot"
export PYINSTALLER_CONFIG_DIR="/private/tmp/salogiflow-pyinstaller-cache"
mkdir -p "$PYINSTALLER_CONFIG_DIR"

pyinstaller --noconfirm --clean --windowed \
  --name "$AGENT_NAME" \
  --collect-all playwright \
  local_agent/agent.py

codesign --deep --force --sign - "$PROJECT_ROOT/dist/$AGENT_NAME.app"

mkdir -p "/private/tmp/salogiflow-agent-pkgroot/Applications"
cp -R "$PROJECT_ROOT/dist/$AGENT_NAME.app" "/private/tmp/salogiflow-agent-pkgroot/Applications/"

pkgbuild \
  --root "/private/tmp/salogiflow-agent-pkgroot" \
  --identifier com.buffalo.salogiflow.agent \
  --version "$VERSION" \
  --install-location / \
  --scripts "$PROJECT_ROOT/local_agent/pkg-scripts" \
  "$PROJECT_ROOT/dist/$AGENT_NAME-macOS.pkg"

echo "Built: $PROJECT_ROOT/dist/$AGENT_NAME-macOS.pkg"
