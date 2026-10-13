#!/usr/bin/env bash
# Serve Qwen3-VL with vLLM as an OpenAI-compatible API on port 8000.
#
#   bash serve_qwen.sh [2b|4b|8b-fp8|8b]      (default: 4b)
#
# Sizes on a 24 GB RTX 4090 (weights only; the rest is KV cache):
#   2b      Qwen/Qwen3-VL-2B-Instruct        ~4 GB   fastest, weakest
#   4b      Qwen/Qwen3-VL-4B-Instruct        ~9 GB   good speed/quality start point
#   8b-fp8  Qwen/Qwen3-VL-8B-Instruct-FP8    ~10 GB  same model as OpenRouter, FP8 (Ada supports it)
#   8b      Qwen/Qwen3-VL-8B-Instruct        ~17 GB  full precision, tight on 24 GB
#
# First start downloads the weights from Hugging Face (~5–17 GB).
# Env overrides: PORT, MAX_LEN, GPU_UTIL, MAX_SEQS, ENV_DIR, CUDA_VISIBLE_DEVICES (pick the GPU)
set -euo pipefail

ENV_DIR="${ENV_DIR:-$HOME/qwen-vllm}"
PORT="${PORT:-8000}"
MAX_LEN="${MAX_LEN:-8192}"
GPU_UTIL="${GPU_UTIL:-0.90}"
MAX_SEQS="${MAX_SEQS:-16}"

case "${1:-4b}" in
  2b)      MODEL="Qwen/Qwen3-VL-2B-Instruct" ;;
  4b)      MODEL="Qwen/Qwen3-VL-4B-Instruct" ;;
  8b-fp8)  MODEL="Qwen/Qwen3-VL-8B-Instruct-FP8" ;;
  8b)      MODEL="Qwen/Qwen3-VL-8B-Instruct"; MAX_LEN="${MAX_LEN_8B:-4096}"; GPU_UTIL="${GPU_UTIL_8B:-0.95}" ;;
  *)       MODEL="$1" ;;   # any other HF model id
esac

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
FIT_UTIL="$(python -c "print(f'{max(0.0, ($FREE_MB - 1024) / $TOTAL_MB):.2f}')")"
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
try:
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
  --limit-mm-per-prompt '{"image": 1, "video": 0}' \
  --enable-prefix-caching
