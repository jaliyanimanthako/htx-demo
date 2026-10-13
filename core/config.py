"""
Configuration shared by all scripts: project paths, .env loading and the
VLM provider presets (OpenAI-compatible endpoints).
"""

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

# ~20 tokens per answer line → room for 1 exit + ~7 obstructions; also stops a small
# model that gets stuck repeating lines
MAX_RESPONSE_TOKENS = 160


PROVIDERS = {
    "openrouter": {
        "base_url":   "https://openrouter.ai/api/v1",
        "model":      "qwen/qwen3-vl-8b-instruct",
        "env_key":    "OPENROUTER_API_KEY",
        # Route to the lowest-latency host for the model
        "extra_body": {"provider": {"sort": "latency"}},
    },
    "qwen": {
        "base_url":   "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        "model":      "qwen-vl-max",
        "env_key":    "QWEN_API_KEY",
    },
    "openai": {
        "base_url":   None,
        "model":      "gpt-4o",
        "env_key":    "OPENAI_API_KEY",
    },
    # Self-hosted vLLM server (see server/serve_qwen.sh)
    "local": {
        "base_url":   os.environ.get("LOCAL_VLM_URL", "http://localhost:8000/v1"),
        "model":      os.environ.get("LOCAL_VLM_MODEL", "Qwen/Qwen3-VL-4B-Instruct"),
        "env_key":    "LOCAL_VLM_API_KEY",
        "api_key":    "EMPTY",   # vLLM needs no key unless started with --api-key
    },
}
