#!/usr/bin/env bash
# Watch live inference from your browser while it runs on the GPU server.
#
#   1) In another terminal / tmux:   bash server/serve_qwen.sh 4b
#   2) From anywhere:                bash server/watch_live.sh IMG_0450.MOV
#   3) Open the printed URL (http://<server-ip>:8090) in a browser on your Mac.
#
# The video loops as a live stream until Ctrl+C. Left = input, right = Qwen boxes.
# Env overrides: INTERVAL (s between calls, default 0.2), WORKERS (default 4),
#                STREAM_PORT (default 8090), DELAY (>0 = buffered mode, e.g. 3)
set -euo pipefail

VIDEO="$(realpath "${1:?usage: bash server/watch_live.sh <video|webcam index|rtsp url>}" 2>/dev/null || echo "$1")"
ENV_DIR="${ENV_DIR:-$HOME/qwen-vllm}"
INTERVAL="${INTERVAL:-0.2}"
WORKERS="${WORKERS:-4}"
STREAM_PORT="${STREAM_PORT:-8090}"
DELAY="${DELAY:-0}"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
for f in live_infer.py video_infer.py core/prompts.py core/stream_view.py engines/qwen_engine.py; do
  [ -f "$f" ] || { echo "Missing $ROOT/$f — copy it from the Mac."; exit 1; }
done

# shellcheck disable=SC1091
source "$ENV_DIR/.venv/bin/activate"
PORT="${PORT:-$(cat "$ENV_DIR/port" 2>/dev/null || echo 8000)}"
URL="http://localhost:$PORT/v1"
MODEL="$(python - "$URL" <<'PY'
import json, sys, urllib.request
try:
    with urllib.request.urlopen(sys.argv[1] + "/models", timeout=5) as r:
        print(json.load(r)["data"][0]["id"])
except Exception as e:
    sys.exit(f"vLLM not reachable at {sys.argv[1]} ({e}). Start it first: bash server/serve_qwen.sh 4b")
PY
)"

echo "Model $MODEL @ $URL | interval ${INTERVAL}s | ${WORKERS} in flight | Ctrl+C to stop"
LOOP=()
[ -f "$VIDEO" ] && LOOP=(--loop)
exec python live_infer.py "$VIDEO" --provider local --base-url "$URL" --model "$MODEL" \
  --interval "$INTERVAL" --workers "$WORKERS" --change-thresh 0 --delay "$DELAY" \
  --no-display --stream "$STREAM_PORT" "${LOOP[@]}" --out-dir results/server
