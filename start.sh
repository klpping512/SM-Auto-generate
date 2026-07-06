#!/bin/bash
cd ~/Desktop/distribution-manager
if [ -f .env ]; then
  set -a
  . ./.env
  set +a
fi
: "${MIMO_API_KEY:?请先通过环境变量设置 MIMO_API_KEY}"
exec python3 app.py
