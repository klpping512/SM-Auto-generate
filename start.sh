#!/bin/bash
PROJECT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
cd "$PROJECT_DIR" || exit 1
if [ -f .env ]; then
  set -a
  . ./.env
  set +a
fi
# 模型密钥为可选能力：服务可先启动，缺失的模型角色会在调用时明确报错或降级。
DEFAULT_ASR_MODEL="$(pwd)/data/models/faster-whisper-small"
if [ -z "${ASSET_ASR_MODEL_PATH:-}" ] && [ -f "$DEFAULT_ASR_MODEL/model.bin" ] && [ -f "$DEFAULT_ASR_MODEL/config.json" ]; then
  export ASSET_ASR_MODEL_PATH="$DEFAULT_ASR_MODEL"
fi
exec python3 app.py
