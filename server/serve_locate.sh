#!/usr/bin/env bash
# Serve NVIDIA LocateAnything-3B over HTTP (POST /detect, /ground).
#
#   bash server/serve_locate.sh                      # GPU 1, port 8020 (or next free)
#   CUDA_VISIBLE_DEVICES=0 bash server/serve_locate.sh
#
# Defaults to GPU 1 so it does not compete with the Qwen vLLM server on GPU 0.
# Env overrides: CUDA_VISIBLE_DEVICES, PORT, ENV_DIR
set -euo pipefail

ENV_DIR="${ENV_DIR:-$HOME/locate-anything}"
PORT="${PORT:-8020}"
export CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES:-1}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ -f "$ENV_DIR/.venv/bin/activate" ]; then
  # shellcheck disable=SC1091
  source "$ENV_DIR/.venv/bin/activate"
else
  # shellcheck disable=SC1091
  source "$(conda info --base)/etc/profile.d/conda.sh" && conda activate "$ENV_DIR/.venv"
fi

GPU_ID="${CUDA_VISIBLE_DEVICES%%,*}"
echo "== GPU $GPU_ID =="
nvidia-smi -i "$GPU_ID" --query-gpu=name,memory.used,memory.total --format=csv,noheader

# Next free port from $PORT upward (shared server)
PORT="$(python - "$PORT" <<'PY'
import socket, sys
p = int(sys.argv[1])
for port in range(p, p + 50):
    s = socket.socket()
    try:
        s.bind(("0.0.0.0", port)); print(port); break
    except OSError:
        continue
    finally:
        s.close()
PY
)"
echo "$PORT" > "$ENV_DIR/port"
SERVER_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
echo "LocateAnything on GPU $GPU_ID → http://${SERVER_IP:-<server-ip>}:$PORT"

exec python "$HERE/locate_server.py" --port "$PORT"
