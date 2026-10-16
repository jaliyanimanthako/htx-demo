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
source "$ROOT/server/_lib.sh"
ENV_DIR="${ENV_DIR:-$HOME/exit-monitor}"
if [ ! -x "$ENV_DIR/.venv/bin/python" ]; then
  echo "Backend environment not found — setting it up (one time, ~10 min)…"
  ENV_DIR="$ENV_DIR" bash "$ROOT/server/setup_monitor.sh"
fi
# shellcheck disable=SC1091
source "$ENV_DIR/.venv/bin/activate"
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
# Packages the backend needs (an env made before FastAPI was added lacks them)
MISSING="$(python - <<'PY'
import importlib.util
need = {"fastapi": "fastapi==0.121.3", "uvicorn": "uvicorn[standard]==0.38.0",
        "openai": "openai==2.26.0", "requests": "requests", "cv2": "opencv-python-headless",
        "ultralytics": "ultralytics==8.4.176", "dotenv": "python-dotenv"}
print(" ".join(f'"{pkg}"' for mod, pkg in need.items() if importlib.util.find_spec(mod) is None))
PY
)"
if [ -n "$MISSING" ]; then
  echo "Installing missing packages into $(python -c 'import sys; print(sys.prefix)'): $MISSING"
  eval "pip_into \"$(command -v python)\" $MISSING"
fi

# Token: use BACKEND_TOKEN, else the one in .env, else create one and save it in .env
if [ -z "${BACKEND_TOKEN:-}" ] && ! grep -qE '^BACKEND_TOKEN=.+' "$ROOT/.env" 2>/dev/null; then
  NEW_TOKEN="$(python -c 'import secrets; print(secrets.token_hex(16))')"
  echo "BACKEND_TOKEN=$NEW_TOKEN" >> "$ROOT/.env"
  echo "Created a backend token and saved it in $ROOT/.env (git-ignored)."
fi

echo "Qwen           $QWEN_URL : $(check "$QWEN_URL/models")"
echo "LocateAnything $LOCATE_URL : $(check "$LOCATE_URL/health")"
echo "(sessions can still start with the servers down — those models are then skipped/failing)"

exec python -m backend --port "${PORT:-8100}"
