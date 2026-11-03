#!/usr/bin/env bash
# Serve Qwen3-VL on an Apple Silicon Mac (MLX, via mlx-vlm) as an OpenAI-compatible API,
# so the scripts can use it with QWEN_URL_MAC=http://localhost:8000/v1 + qwen_server.py mac.
#
#   bash server/serve_qwen_mac.sh [8bit|4bit|bf16]      (default: 8bit)
#
# Sizes in unified memory (weights only; images need ~1–2 GB more):
#   8bit   mlx-community/Qwen3-VL-4B-Instruct-8bit   ~5 GB   almost the original quality (recommended)
#   4bit   mlx-community/Qwen3-VL-4B-Instruct-4bit   ~3 GB   fastest, small dates may suffer
#   bf16   Qwen/Qwen3-VL-4B-Instruct converted        ~9 GB   same numbers as the GPU server; tight on 16 GB
#
# First run sets up ~/qwen-mlx (one time) and downloads the weights from Hugging Face.
# Env overrides: PORT (8000), HOST (127.0.0.1; 0.0.0.0 lets other machines reach it), ENV_DIR
set -euo pipefail

ENV_DIR="${ENV_DIR:-$HOME/qwen-mlx}"
PORT="${PORT:-8000}"
HOST="${HOST:-127.0.0.1}"
MLX_VLM_VERSION="0.7.11"

case "${1:-8bit}" in
  8bit)  MODEL="mlx-community/Qwen3-VL-4B-Instruct-8bit" ;;
  4bit)  MODEL="mlx-community/Qwen3-VL-4B-Instruct-4bit" ;;
  bf16)  MODEL="$ENV_DIR/models/Qwen3-VL-4B-Instruct-bf16" ;;
  *)     MODEL="$1" ;;   # any other MLX model (HF repo id or local folder)
esac

if [ "$(uname -s)-$(uname -m)" != "Darwin-arm64" ]; then
  echo "This script is for Apple Silicon Macs. On the GPU server use: bash server/serve_qwen.sh"
  exit 1
fi

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$HERE/_lib.sh"
# First run (or a broken env): set it up automatically
if [ -x "$ENV_DIR/.venv/bin/python" ] && ! "$ENV_DIR/.venv/bin/python" -c "import mlx_vlm" 2>/dev/null; then
  set_aside "$ENV_DIR/.venv"
fi
if [ ! -x "$ENV_DIR/.venv/bin/python" ]; then
  echo "MLX environment not found — setting it up in $ENV_DIR (one time, a few minutes)…"
  mkdir -p "$ENV_DIR"
  new_venv "$ENV_DIR/.venv" 3.11
  "$(ensure_uv)" pip install --python "$ENV_DIR/.venv/bin/python" "mlx-vlm==$MLX_VLM_VERSION"
fi
# shellcheck disable=SC1091
source "$ENV_DIR/.venv/bin/activate"
# (bf16 conversion) plain HTTP downloads: Hugging Face's Xet downloader stalled on this Mac
export HF_HUB_DISABLE_XET=1

# bf16: convert the official Qwen weights to MLX once (no quantization)
if [ "${1:-8bit}" = "bf16" ] && [ ! -f "$MODEL/config.json" ]; then
  echo "Converting Qwen/Qwen3-VL-4B-Instruct to MLX (bf16, one time)…"
  python -m mlx_vlm.convert --hf-path Qwen/Qwen3-VL-4B-Instruct --mlx-path "$MODEL"
fi

# Download the weights into $ENV_DIR/models/<name> before serving. Each try starts curl
# again from the current file size, so a dropped or stalled connection only costs a retry
# (hf download started over from zero here, and curl's own --retry rewinds the file).
if [ ! -d "$MODEL" ]; then
  REPO="$MODEL"; MODEL="$ENV_DIR/models/${REPO##*/}"
  mkdir -p "$MODEL"
  LIST="$(curl -fsS --retry 5 --retry-all-errors "https://huggingface.co/api/models/$REPO?blobs=true" \
          | python -c "import json,sys; [print(s['rfilename'], s.get('size', 0)) for s in json.load(sys.stdin)['siblings']]")" \
    || { echo "Could not reach Hugging Face for $REPO (check the internet connection)"; exit 1; }
  while read -r f size; do
    mkdir -p "$(dirname "$MODEL/$f")"
    have() { stat -f %z "$MODEL/$f" 2>/dev/null || echo 0; }
    [ "$(have)" = "$size" ] && continue
    echo "Downloading $REPO/$f ($((size / 1048576)) MB)"
    for try in $(seq 1 50); do
      curl -fL -C - --speed-limit 1000 --speed-time 30 --progress-bar \
           -o "$MODEL/$f" "https://huggingface.co/$REPO/resolve/main/$f" && break
      [ "$(have)" = "$size" ] && break
      echo "Connection dropped at $(( $(have) / 1048576 )) MB — continuing ($try/50)…"; sleep 5
    done
    [ "$(have)" = "$size" ] || { echo "Download of $f stopped — run the script again to continue where it left off"; exit 1; }
  done <<< "$LIST"
fi

# The scripts expect the port in QWEN_URL_MAC, so don't move to another one
if python -c "import socket,sys; s=socket.socket(); sys.exit(s.connect_ex(('127.0.0.1', $PORT)) != 0)"; then
  echo "Port $PORT is already in use (another server running?). Stop it, or run with PORT=<n>"
  echo "and set QWEN_URL_MAC=http://localhost:<n>/v1 in .env."
  exit 1
fi

echo "$PORT" > "$ENV_DIR/port"
echo "Serving $MODEL on $HOST:$PORT (first start downloads the weights)"
echo "Client URL: http://localhost:$PORT/v1   →  .env: QWEN_URL_MAC=http://localhost:$PORT/v1"
echo "Then: .venv/bin/python qwen_server.py mac"

exec python -m mlx_vlm.server --model "$MODEL" --host "$HOST" --port "$PORT"
