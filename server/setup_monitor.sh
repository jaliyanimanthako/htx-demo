#!/usr/bin/env bash
# One-time setup for exit_monitor.py on the GPU server.
# Own env (~/exit-monitor/.venv) so it can't break the vLLM or LocateAnything envs.
#
#   bash server/setup_monitor.sh
#
# Env overrides: ENV_DIR (default ~/exit-monitor), TORCH_INDEX (PyTorch wheel index)
set -euo pipefail

ENV_DIR="${ENV_DIR:-$HOME/exit-monitor}"
TORCH_INDEX="${TORCH_INDEX:-https://download.pytorch.org/whl/cu124}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
mkdir -p "$ENV_DIR"

if command -v uv >/dev/null 2>&1; then
  uv venv --python 3.11 "$ENV_DIR/.venv"
  # shellcheck disable=SC1091
  source "$ENV_DIR/.venv/bin/activate"
  PIP="uv pip"
else
  python3 -m venv "$ENV_DIR/.venv"
  # shellcheck disable=SC1091
  source "$ENV_DIR/.venv/bin/activate"
  pip install -U pip
  PIP="pip"
fi

echo "== PyTorch ($TORCH_INDEX) =="
$PIP install torch torchvision --index-url "$TORCH_INDEX"

echo "== Monitor requirements =="
$PIP install ultralytics==8.4.176 openai==2.26.0 requests python-dotenv pillow numpy \
  fastapi==0.121.3 "uvicorn[standard]==0.38.0"
# ultralytics pulls opencv-python (needs a display); swap in the headless build
$PIP uninstall opencv-python 2>/dev/null || pip uninstall -y opencv-python || true
$PIP install opencv-python-headless
# YOLOE text prompts need OpenAI CLIP's tokenizer package
$PIP install "git+https://github.com/ultralytics/CLIP.git"

echo "== Download YOLOE weights + MobileCLIP text encoder into $ROOT/models =="
mkdir -p "$ROOT/models"
cd "$ROOT/models"
python - <<'PY'
from ultralytics import YOLOE
m = YOLOE("yoloe-11s-seg.pt")
m.set_classes(["door"], m.get_text_pe(["door"]))   # triggers the MobileCLIP download
print("YOLOE ready")
PY

python - <<'PY'
import torch, cv2, ultralytics
print("torch", torch.__version__, "| CUDA", torch.cuda.is_available(),
      "| ultralytics", ultralytics.__version__, "| cv2", cv2.__version__)
PY
echo
echo "Done. Next: bash server/run_backend.sh   (or: bash server/run_monitor.sh IMG_0451.MOV)"
