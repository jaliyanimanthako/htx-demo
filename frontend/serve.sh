#!/usr/bin/env bash
# Host the web app on this machine (any OS with Python 3):
#
#   bash frontend/serve.sh            # http://localhost:5173
#   PORT=8080 bash frontend/serve.sh
#   HOST=0.0.0.0 bash frontend/serve.sh   # also reachable from other machines
#
# Then open the page, enter the backend URL (http://<server-ip>:<port>, printed
# port from server/start_all.sh) and the token, and Connect.
# Without bash:  python3 -m http.server 5173 --bind localhost -d frontend
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PORT="${PORT:-5173}"
HOST="${HOST:-localhost}"
PY="$(command -v python3 || command -v python)" || { echo "Python 3 is needed."; exit 1; }
echo "Web app: http://localhost:$PORT   (Ctrl+C to stop)"
[ "$HOST" = localhost ] || echo "Bound to $HOST — the camera only works over https or on localhost."
exec "$PY" -m http.server "$PORT" --bind "$HOST" -d "$DIR"
