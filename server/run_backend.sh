#!/usr/bin/env bash
# Run the FastAPI backend on the GPU server (the web app on your Mac connects to it).
# Uses the ~/exit-monitor env (bash server/setup_monitor.sh) and finds the Qwen and
# LocateAnything servers on this machine. Run it inside tmux:
#
#   tmux new -s backend
#   BACKEND_TOKEN=<pick-a-secret> bash server/run_backend.sh          # port 8100 (or next free)
#   CUDA_VISIBLE_DEVICES=0 BACKEND_TOKEN=... bash server/run_backend.sh   # GPU for YOLOE
#
# Env: BACKEND_TOKEN (strongly recommended), BACKEND_CORS_ORIGINS (default *),
#      BACKEND_VIDEO_DIR (default: repo root), BACKEND_MAX_SESSIONS (default 1), PORT (8100)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
# shellcheck disable=SC1091
source "${ENV_DIR:-$HOME/exit-monitor}/.venv/bin/activate"
export CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES:-0}"

QWEN_PORT="$(cat "$HOME/qwen-vllm/port" 2>/dev/null || echo 8010)"
LOCATE_PORT="$(cat "$HOME/locate-anything/port" 2>/dev/null || echo 8020)"
export QWEN_URL="${QWEN_URL:-http://localhost:$QWEN_PORT/v1}"
export LOCATE_URL="${LOCATE_URL:-http://localhost:$LOCATE_PORT}"

check() {
  python - "$1" <<'PY'
import sys, urllib.request
try:
    urllib.request.urlopen(sys.argv[1], timeout=5); print("up")
except Exception:
    print("DOWN")
PY
}
echo "Qwen           $QWEN_URL : $(check "$QWEN_URL/models")"
echo "LocateAnything $LOCATE_URL : $(check "$LOCATE_URL/health")"
echo "(sessions can still start with the servers down — those models are then skipped/failing)"
[ -z "${BACKEND_TOKEN:-}" ] && echo "WARNING: BACKEND_TOKEN not set — anyone who can reach this port can start sessions."

exec python -m backend --port "${PORT:-8100}"
