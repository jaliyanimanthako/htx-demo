"""
Backend settings — all from environment variables (or .env):

  BACKEND_TOKEN         shared secret; if set, every API call needs it
                        (header "Authorization: Bearer <token>" or ?token=<token>)
  BACKEND_CORS_ORIGINS  comma-separated origins allowed to call the API (default "*")
  BACKEND_VIDEO_DIR     folder with the videos that sessions may play (default: project root)
  BACKEND_MAX_SESSIONS  sessions that may run at the same time (default 1 — one GPU pipeline)
  BACKEND_OUT_DIR       where reports / snapshots go (default: results)
  LOCATE_URL, QWEN_URL  model servers; if unset, the port files written by
                        server/serve_locate.sh and server/serve_qwen.sh are used (localhost).
                        QWEN_URL=openrouter uses the hosted OpenRouter API instead
                        (OPENROUTER_API_KEY; model QWEN_MODEL, default qwen/qwen3-vl-8b-instruct)
                        and turns LocateAnything off (YOLOE + Qwen only)
"""

import os
from pathlib import Path

from core.config import ROOT, is_openrouter, qwen_url  # also loads .env

VIDEO_EXTS = {".mov", ".mp4", ".avi", ".mkv", ".m4v"}


def _from_port_file(env_dir: str, path_suffix: str, default_port: int) -> str:
    port_file = Path.home() / env_dir / "port"
    try:
        port = int(port_file.read_text().strip())
    except (OSError, ValueError):
        port = default_port
    return f"http://localhost:{port}{path_suffix}"


TOKEN = os.environ.get("BACKEND_TOKEN", "").strip()
CORS_ORIGINS = [o.strip() for o in os.environ.get("BACKEND_CORS_ORIGINS", "*").split(",") if o.strip()]
VIDEO_DIR = Path(os.environ.get("BACKEND_VIDEO_DIR", ROOT)).expanduser().resolve()
MAX_SESSIONS = int(os.environ.get("BACKEND_MAX_SESSIONS", "1"))
OUT_DIR = Path(os.environ.get("BACKEND_OUT_DIR", ROOT / "results")).expanduser()
QWEN_URL = qwen_url(os.environ.get("QWEN_URL")) or _from_port_file("qwen-vllm", "/v1", 8010)
QWEN_PROVIDER = "openrouter" if is_openrouter(QWEN_URL) else "self-hosted"
# With OpenRouter the backend needs no GPU model servers: YOLOE + Qwen only, no LocateAnything
LOCATE_URL = "" if QWEN_PROVIDER == "openrouter" else (
    os.environ.get("LOCATE_URL") or _from_port_file("locate-anything", "", 8020))
