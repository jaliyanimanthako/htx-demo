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

# Use the LocateAnything env's own interpreter — never whatever "python" is on PATH
# (a system Anaconda torch fails with e.g. "libcudnn.so.8: cannot open shared object file")
PY="$ENV_DIR/.venv/bin/python"
if [ ! -x "$PY" ]; then
  echo "LocateAnything env not found: $PY"
  echo "Create it first:  bash server/setup_locate.sh      (or set ENV_DIR=<path to the env's parent>)"
  exit 1
fi
if ! "$PY" -c "import torch, transformers" 2>/dev/null; then
  echo "LocateAnything env at $ENV_DIR/.venv is incomplete (torch / transformers missing)."
  echo "Rebuild it:  rm -rf $ENV_DIR/.venv && bash server/setup_locate.sh"
  exit 1
fi
echo "Python: $PY ($("$PY" -c 'import torch, transformers; print("torch", torch.__version__, "| transformers", transformers.__version__)'))"

GPU_ID="${CUDA_VISIBLE_DEVICES%%,*}"
echo "== GPU $GPU_ID =="
nvidia-smi -i "$GPU_ID" --query-gpu=name,memory.used,memory.total --format=csv,noheader

# Next free port from $PORT upward (shared server)
PORT="$("$PY" - "$PORT" <<'PY'
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

exec "$PY" "$HERE/locate_server.py" --port "$PORT"
