#!/usr/bin/env bash
# One-time setup for NVIDIA LocateAnything-3B on the GPU server.
# Separate env from vLLM: the model pins transformers==4.57.1 / numpy==1.25.0.
#
#   bash server/setup_locate.sh
#
# Env overrides: ENV_DIR (default ~/locate-anything), TORCH_INDEX (PyTorch wheel index)
set -euo pipefail

ENV_DIR="${ENV_DIR:-$HOME/locate-anything}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$HERE/_lib.sh"
TORCH_INDEX="${TORCH_INDEX:-https://download.pytorch.org/whl/cu124}"
mkdir -p "$ENV_DIR"
cd "$ENV_DIR"

echo "== GPU =="
nvidia-smi --query-gpu=index,name,memory.used,memory.total,driver_version --format=csv

# numpy==1.25.0 has no wheels for Python ≥ 3.12 → Python 3.11, via uv (downloads it
# if the system has no 3.11). No conda: a shared system Anaconda can't be relied on.
echo "== Creating the environment (Python 3.11) =="
new_venv "$ENV_DIR/.venv" 3.11
# shellcheck disable=SC1091
source .venv/bin/activate
PIP="$(ensure_uv) pip"

echo "== PyTorch ($TORCH_INDEX) =="
$PIP install torch torchvision --index-url "$TORCH_INDEX"

echo "== LocateAnything requirements (from the model card) + server =="
$PIP install opencv-python-headless==4.11.0.86 transformers==4.57.1 numpy==1.25.0 \
  Pillow==11.1.0 peft decord==0.6.0 lmdb==1.7.5 accelerate \
  fastapi "uvicorn[standard]" huggingface_hub

echo "== Download weights =="
python - <<'PY'
from huggingface_hub import snapshot_download
p = snapshot_download("nvidia/LocateAnything-3B")
print("weights at", p)
PY

python - <<'PY'
import torch, transformers
print("torch", torch.__version__, "| CUDA", torch.cuda.is_available(),
      "| transformers", transformers.__version__)
PY
echo
echo "Done. Next: bash server/serve_locate.sh"
