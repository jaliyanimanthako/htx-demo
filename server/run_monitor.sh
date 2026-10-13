#!/usr/bin/env bash
# Run exit_monitor.py on the GPU server, headless, viewed in a browser.
# Finds the Qwen (vLLM) and LocateAnything servers on this machine automatically.
#
#   bash server/run_monitor.sh IMG_0451.MOV              # loops the video, browser on :8090
#   bash server/run_monitor.sh IMG_0451.MOV --no-yoloe   # extra args go to exit_monitor.py
#   CUDA_VISIBLE_DEVICES=0 bash server/run_monitor.sh ...  # GPU for YOLOE (default 0)
set -euo pipefail

SRC="${1:?usage: bash server/run_monitor.sh <video|rtsp url> [exit_monitor args]}"
shift
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
[ -f "$SRC" ] && SRC="$(realpath "$SRC")"
cd "$ROOT"

# shellcheck disable=SC1091
source "${ENV_DIR:-$HOME/exit-monitor}/.venv/bin/activate"
export CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES:-0}"

QWEN_PORT="$(cat "$HOME/qwen-vllm/port" 2>/dev/null || echo 8010)"
LOCATE_PORT="$(cat "$HOME/locate-anything/port" 2>/dev/null || echo 8020)"
QWEN_URL="${QWEN_URL:-http://localhost:$QWEN_PORT/v1}"
LOCATE_URL="${LOCATE_URL:-http://localhost:$LOCATE_PORT}"

check() {
  python - "$1" <<'PY'
import sys, urllib.request
try:
    urllib.request.urlopen(sys.argv[1], timeout=5); print("ok")
except Exception as e:
    print(f"DOWN ({e})")
PY
}
QWEN_OK="$(check "$QWEN_URL/models")"
LOCATE_OK="$(check "$LOCATE_URL/health")"
echo "Qwen           $QWEN_URL : $QWEN_OK"
echo "LocateAnything $LOCATE_URL : $LOCATE_OK"
if [ "$QWEN_OK" != "ok" ] || [ "$LOCATE_OK" != "ok" ]; then
  echo
  echo "Start the model servers first (in tmux so they survive logout):"
  [ "$QWEN_OK" != "ok" ]   && echo "  tmux new -s qwen    →  cd $ROOT && bash server/serve_qwen.sh 4b     (Ctrl+B D to detach)"
  [ "$LOCATE_OK" != "ok" ] && echo "  tmux new -s locate  →  cd $ROOT && bash server/serve_locate.sh      (Ctrl+B D to detach)"
  exit 1
fi

LOOP=()
[ -f "$SRC" ] && LOOP=(--loop)
exec python exit_monitor.py "$SRC" --locate-url "$LOCATE_URL" --qwen-url "$QWEN_URL" \
  --no-display --stream 8090 "${LOOP[@]}" --out-dir results "$@"
