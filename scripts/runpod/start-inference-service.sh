#!/usr/bin/env bash
set -euo pipefail

APP_ROOT="${APP_ROOT:-/workspace/text2video/app}"
VENV_PATH="${VENV_PATH:-/workspace/text2video/venv}"
SERVICE_PORT="${SERVICE_PORT:-8000}"

if [ -f /etc/profile.d/text2video-runpod.sh ]; then
  # shellcheck disable=SC1091
  source /etc/profile.d/text2video-runpod.sh
fi

source "${VENV_PATH}/bin/activate"
cd "${APP_ROOT}"

python -m uvicorn apps.runpod_service.main:app --host 0.0.0.0 --port "${SERVICE_PORT}"
