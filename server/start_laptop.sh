#!/usr/bin/env bash
# Single-GPU laptop (8 GB, e.g. RTX 4060 Laptop) as the "GPU server": one Qwen3-VL model.
#
#   bash server/start_laptop.sh [model]   # Qwen in tmux session 'qwen'; waits until it answers
#
# model: a name from core/models.py (python run.py inspect --list-models). Default: [models]
# qwen_model in config.toml, else 2b. vLLM serves one model at a time: if another one is
# running, it is stopped and this one started (about 1 min; the first start downloads it).
# Only the 2B models fit next to YOLOE: 2b (4.3 GB of weights) and 2b-fp8 (3.5 GB).
# Qwen-4B (8.9 GB, FP8 6.0 GB) and LocateAnything-3B (~7.7 GB) don't, and are refused.
# The runners then use QWEN_URL=http://localhost:<port>/v1 from .env, no LOCATE_URL.
# Start it before the runners — vLLM reserves its share of GPU memory at start-up.
#
# Status: bash server/status.sh · watch: tmux attach -t qwen · stop: bash server/stop_all.sh qwen
# Env overrides: QWEN_MODEL (instead of the argument), GPU_UTIL (0.70), MAX_LEN (4096),
#   MAX_SEQS (4), MM_MAX_SIDE (1280: largest image side vLLM reserves memory for)
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
# shellcheck disable=SC1091
source "$ROOT/server/_lib.sh"
config_qwen_model() {   # [models] qwen_model in config.toml (nothing if unset)
  python3 - "${HTX_CONFIG:-$ROOT/config.toml}" <<'PY' 2>/dev/null
import sys, tomllib
print(tomllib.load(open(sys.argv[1], "rb")).get("models", {}).get("qwen_model", ""))
PY
}
NAME="${1:-${QWEN_MODEL:-$(config_qwen_model)}}"
NAME="${NAME:-2b}"
INFO="$(qwen_lookup "$NAME")" || exit 1
MODEL="${INFO% *}"
GB="${INFO#* }"
UTIL="${GPU_UTIL:-0.70}"
PFILE="$HOME/qwen-vllm/port"
LOG="$ROOT/logs/qwen.log"
mkdir -p "$ROOT/logs"

# Does it fit? Weights + ~1.2 GB (KV cache for one MAX_LEN request, image activations,
# CUDA graphs) within vLLM's share of the GPU. Checked before stopping a running model.
# TOTAL_MIB="$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits -i 0 2>/dev/null)"
# if [ "$GB" != "?" ] && [ -n "$TOTAL_MIB" ] && ! python3 - "$MODEL" "$GB" "$UTIL" "$TOTAL_MIB" <<'PY'
# import sys
# model, gb, util, total = sys.argv[1], float(sys.argv[2]), float(sys.argv[3]), float(sys.argv[4]) * 1.048576e-3
# if gb + 1.2 > util * total:
#     sys.exit(f"{model}: {gb} GB of weights + ~1.2 GB working memory don't fit in vLLM's share of "
#              f"this GPU (GPU_UTIL {util} x {total:.1f} GB = {util * total:.1f} GB).\n"
#              "Pick a smaller model (2b, 2b-fp8), or raise GPU_UTIL (less room for YOLOE).")
# PY
# then
#   exit 1
# fi

if [ -f "$PFILE" ] && http_up "http://localhost:$(cat "$PFILE")/v1/models"; then
  URL="http://localhost:$(cat "$PFILE")/v1"
  SERVED="$(served_model "$URL")"
  if [ "$SERVED" = "$MODEL" ]; then
    echo "Qwen is already running $MODEL: QWEN_URL=$URL"
    exit 0
  fi
  echo "Qwen is running ${SERVED:-another model} — switching to $MODEL"
  bash "$ROOT/server/stop_all.sh" qwen
  for _ in $(seq 60); do       # the old server holds its GPU memory until its processes exit
    pgrep -f "vllm serve|VLLM::EngineCore" >/dev/null || break
    sleep 1
  done
  if http_up "$URL/models"; then
    echo "It is still running (not started by this script?) — stop it, then run this again."
    exit 1
  fi
fi
command -v tmux >/dev/null 2>&1 || { echo "tmux is needed: sudo apt install tmux"; exit 1; }

rm -f "$PFILE"
: > "$LOG"
tmux kill-session -t qwen 2>/dev/null
tmux new-session -d -s qwen
# settings go in the command line: a tmux shell doesn't inherit this script's environment
tmux send-keys -t qwen "cd '$ROOT' && GPU_UTIL=$UTIL MAX_LEN=${MAX_LEN:-4096} \
MAX_SEQS=${MAX_SEQS:-4} MM_MAX_SIDE=${MM_MAX_SIDE:-1280} bash server/serve_qwen.sh '$MODEL' 2>&1 \
| tee '$LOG'; echo __EXITED__ >> '$LOG'" Enter
echo "Qwen ($MODEL) starting in tmux session 'qwen' (the first start downloads the weights)..."

start=$(date +%s)
until [ -f "$PFILE" ] && http_up "http://localhost:$(cat "$PFILE")/v1/models"; do
  if grep -q "__EXITED__" "$LOG"; then
    echo "Qwen stopped. Last lines of $LOG:"
    tail -n 15 "$LOG" | grep -v "__EXITED__"
    exit 1
  fi
  if [ $(( $(date +%s) - start )) -gt 1800 ]; then
    echo "Qwen not ready after 30 min — see $LOG"
    exit 1
  fi
  sleep 3
done
echo "Qwen is up after $(mmss $(( $(date +%s) - start ))): QWEN_URL=http://localhost:$(cat "$PFILE")/v1"
nvidia-smi --query-gpu=memory.used,memory.total --format=csv,noheader | sed 's/^/GPU memory used: /'
