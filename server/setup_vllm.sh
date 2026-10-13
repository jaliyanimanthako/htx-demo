#!/usr/bin/env bash
# One-time setup on the GPU server (Linux + NVIDIA driver).
# Creates ~/qwen-vllm/.venv with vLLM, then prints a GPU / CUDA sanity check.
#
#   bash server/setup_vllm.sh
#
# Env overrides: ENV_DIR (default ~/qwen-vllm), VLLM_VERSION (default 0.31.0, the tested one)
set -euo pipefail

ENV_DIR="${ENV_DIR:-$HOME/qwen-vllm}"

echo "== GPU =="
nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv || {
  echo "nvidia-smi failed — install the NVIDIA driver first."; exit 1; }

mkdir -p "$ENV_DIR"
cd "$ENV_DIR"

if command -v uv >/dev/null 2>&1; then
  echo "== Creating venv with uv =="
  uv venv --python 3.12 .venv
  # shellcheck disable=SC1091
  source .venv/bin/activate
  uv pip install "vllm==${VLLM_VERSION:-0.31.0}" opencv-python-headless python-dotenv
else
  echo "== Creating venv with python3 -m venv (install 'uv' for a faster setup) =="
  python3 -m venv .venv
  # shellcheck disable=SC1091
  source .venv/bin/activate
  pip install -U pip
  pip install "vllm==${VLLM_VERSION:-0.31.0}" opencv-python-headless python-dotenv
fi

echo "== Check =="
python - <<'PY'
import torch, vllm
print("vllm", vllm.__version__, "| torch", torch.__version__,
      "| CUDA", torch.cuda.is_available(),
      "|", torch.cuda.get_device_name(0) if torch.cuda.is_available() else "no GPU")
PY

echo
echo "Done. Next: bash serve_qwen.sh 4b"
