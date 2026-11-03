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


OPENROUTER_URL = "https://openrouter.ai/api/v1"


def qwen_servers() -> dict:
    """Named Qwen servers: every QWEN_URL_<NAME>=<url> in .env (QWEN_URL_GPU, QWEN_URL_MAC, …)
    plus the built-in "openrouter". Pick one with qwen_server.py or QWEN_URL=<name>."""
    servers = {k[len("QWEN_URL_"):].lower(): v.strip() for k, v in sorted(os.environ.items())
               if k.startswith("QWEN_URL_") and v.strip()}
    servers.setdefault("openrouter", OPENROUTER_URL)
    return servers


def qwen_url(url):
    """QWEN_URL / --qwen-url: a self-hosted vLLM server (http://<server-ip>:8010/v1), the
    shorthand "openrouter" for the hosted OpenRouter API (see qwen_assist.qwen_endpoint),
    or a server name from .env ("mac" → QWEN_URL_MAC)."""
    if not url or "://" in url:
        return url
    name = url.strip().lower().replace("-", "_")
    servers = qwen_servers()
    if name not in servers:
        raise SystemExit(f"  Unknown Qwen server '{url}': add QWEN_URL_{name.upper()}=http://... "
                         f"to .env (known: {', '.join(servers)})")
    named = servers[name]
    return OPENROUTER_URL if named.lower() == "openrouter" else named


def is_openrouter(url) -> bool:
    """Qwen through OpenRouter → the "no GPU server" setup: YOLOE + Qwen only,
    LocateAnything is not used (exit_monitor.py, backend)."""
    return bool(url) and "openrouter.ai" in qwen_url(url)


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
