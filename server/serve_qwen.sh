#!/usr/bin/env bash
# Serve Qwen3-VL with vLLM as an OpenAI-compatible API on port 8000.
#
#   bash serve_qwen.sh [model]      (default: 4b)
#
# model: a name from core/models.py (python run.py inspect --list-models), a repo name such
# as qwen3-vl-4b-instruct, or any other Hugging Face model id. Weights only (the rest of
# the GPU share is KV cache); FP8 runs natively on Ada (RTX 40xx) and newer:
#   2b      Qwen/Qwen3-VL-2B-Instruct        4.3 GB   fastest, weakest (the 8 GB laptop's model)
#   2b-fp8  Qwen/Qwen3-VL-2B-Instruct-FP8    3.5 GB
#   4b      Qwen/Qwen3-VL-4B-Instruct        8.9 GB   good speed/quality start point
#   4b-fp8  Qwen/Qwen3-VL-4B-Instruct-FP8    6.0 GB
#   8b-fp8  Qwen/Qwen3-VL-8B-Instruct-FP8    10.6 GB  same model as OpenRouter, FP8
#   8b      Qwen/Qwen3-VL-8B-Instruct        17.5 GB  full precision, tight on 24 GB
#
# First start downloads the weights from Hugging Face (~5–17 GB).
# Env overrides: PORT, MAX_LEN, GPU_UTIL, MAX_SEQS, ENV_DIR, CUDA_VISIBLE_DEVICES (pick the GPU),
#   MM_MAX_SIDE (largest image side to reserve memory for; small GPUs, see start_laptop.sh)
set -euo pipefail

ENV_DIR="${ENV_DIR:-$HOME/qwen-vllm}"
PORT="${PORT:-8000}"
MAX_LEN="${MAX_LEN:-8192}"
GPU_UTIL="${GPU_UTIL:-0.90}"
MAX_SEQS="${MAX_SEQS:-16}"
# vLLM sizes its memory with the largest image the model accepts (~16k image tokens for
# Qwen3-VL). Clients send at most 1280 px (apps/extinguisher_monitor.py; 640 px elsewhere),
# so on an 8 GB GPU cap the profiled image to leave room for the KV cache.
MM_LIMIT='{"image": 1, "video": 0}'
if [ -n "${MM_MAX_SIDE:-}" ]; then
  MM_LIMIT="{\"image\": {\"count\": 1, \"width\": $MM_MAX_SIDE, \"height\": $MM_MAX_SIDE}, \"video\": 0}"
fi

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$HERE/_lib.sh"

MODEL="$(qwen_lookup "${1:-4b}")" || exit 1
MODEL="${MODEL% *}"
if [ "$MODEL" = "Qwen/Qwen3-VL-8B-Instruct" ]; then
  MAX_LEN="${MAX_LEN_8B:-4096}"; GPU_UTIL="${GPU_UTIL_8B:-0.95}"
fi
# First run (or a broken env): set it up automatically
if [ -x "$ENV_DIR/.venv/bin/python" ] && ! "$ENV_DIR/.venv/bin/python" -c "import vllm" 2>/dev/null; then
  set_aside "$ENV_DIR/.venv"
fi
if [ ! -x "$ENV_DIR/.venv/bin/python" ]; then
  echo "vLLM environment not found — setting it up (one time, several minutes)…"
  ENV_DIR="$ENV_DIR" bash "$HERE/setup_vllm.sh"
fi
# shellcheck disable=SC1091
source "$ENV_DIR/.venv/bin/activate"

# FlashInfer's sampler JIT-compiles CUDA kernels with the system nvcc; an older
# CUDA toolkit fails ("Unknown option '--compress-mode=size'"). Use vLLM's
# PyTorch sampler instead — no compile step. Set to 1 if your nvcc is ≥ 12.8.
export VLLM_USE_FLASHINFER_SAMPLER="${VLLM_USE_FLASHINFER_SAMPLER:-0}"

# Shared GPU: never ask for more than what is free right now (minus 1 GiB headroom)
# (checks the GPU vLLM will use: first id in CUDA_VISIBLE_DEVICES, else 0)
GPU_ID="${CUDA_VISIBLE_DEVICES:-0}"; GPU_ID="${GPU_ID%%,*}"
read -r FREE_MB TOTAL_MB < <(nvidia-smi --query-gpu=memory.free,memory.total \
                               --format=csv,noheader,nounits -i "$GPU_ID" | tr -d ',')
FIT_UTIL="$(python -c "print(f'{max(0.0, ($FREE_MB - 256) / $TOTAL_MB):.2f}')")"
if python -c "import sys; sys.exit(0 if $FIT_UTIL < $GPU_UTIL else 1)"; then
  echo "GPU is shared: ${FREE_MB} MiB free of ${TOTAL_MB} MiB — lowering gpu_util $GPU_UTIL → $FIT_UTIL"
  GPU_UTIL="$FIT_UTIL"
fi
nvidia-smi -i "$GPU_ID" --query-compute-apps=pid,process_name,used_memory --format=csv,noheader \
  | sed "s/^/  other process on GPU $GPU_ID: /" || true

# Shared server: if the port is taken, move to the next free one (8000 → 8001 → ...)
port_busy() {
  python - "$1" <<'PY'
import socket, sys
s = socket.socket()
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)   # only a listening server counts, not
try:                                                        # TIME_WAIT left by a stopped one
    s.bind(("0.0.0.0", int(sys.argv[1])))
except OSError:
    sys.exit(0)   # busy
finally:
    s.close()
sys.exit(1)       # free
PY
}
START_PORT="$PORT"
while port_busy "$PORT"; do
  PORT=$((PORT + 1))
  if [ "$PORT" -gt $((START_PORT + 50)) ]; then
    echo "No free port in $START_PORT-$PORT"; exit 1
  fi
done
[ "$PORT" != "$START_PORT" ] && echo "Port $START_PORT is in use — using $PORT instead."

echo "$PORT" > "$ENV_DIR/port"   # read by run_realtime_test.sh
SERVER_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
echo "Serving $MODEL on 0.0.0.0:$PORT (max_len=$MAX_LEN, gpu_util=$GPU_UTIL, max_seqs=$MAX_SEQS)"
echo "Client URL: http://${SERVER_IP:-<this-server-ip>}:$PORT/v1   model: $MODEL"

exec vllm serve "$MODEL" \
  --host 0.0.0.0 --port "$PORT" \
  --served-model-name "$MODEL" \
  --max-model-len "$MAX_LEN" \
  --gpu-memory-utilization "$GPU_UTIL" \
  --max-num-seqs "$MAX_SEQS" \
  --limit-mm-per-prompt "$MM_LIMIT" \
  --enable-prefix-caching
