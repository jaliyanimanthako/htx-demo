"""
Choose which server Qwen runs on, for every script and the backend.

The servers are named in .env, one line each (addresses stay in .env):

  QWEN_URL_GPU=http://<gpu-server-ip>:8010/v1
  QWEN_URL_MAC=http://localhost:8000/v1
  # "openrouter" is built in (needs OPENROUTER_API_KEY)

  python qwen_server.py              # list the servers: which one is in use, up or down
  python qwen_server.py mac          # use the Mac from now on (writes QWEN_URL=mac to .env)
  python qwen_server.py gpu          # back to the GPU server
  python qwen_server.py openrouter   # the hosted OpenRouter API
  python qwen_server.py default      # remove the choice (the backend on the GPU server then
                                     # finds its own vLLM server, the CLIs run without Qwen)

For one CLI run only, pass the name instead: --qwen-url mac. A running backend keeps
its server until it is restarted.
"""

import os
import re
import sys
import urllib.request

from dotenv import dotenv_values

from core.config import ROOT, is_openrouter, qwen_servers

ENV = ROOT / ".env"
LINE = re.compile(r"^QWEN_URL=.*$", re.M)


def _up(url: str) -> str:
    try:
        urllib.request.urlopen(url.rstrip("/") + "/models", timeout=3)
        return "up"
    except Exception:
        return "DOWN"


def show():
    servers = qwen_servers()
    current = (dotenv_values(ENV).get("QWEN_URL") or "").strip().lower()
    print("Qwen servers (addresses in .env):")
    for name, url in servers.items():
        mark = "→" if name == current else " "
        kind = "OpenRouter API" if is_openrouter(url) else "self-hosted"
        print(f"  {mark} {name:<12} {kind:<15} {_up(url)}")
    shell = (os.environ.get("QWEN_URL") or "").strip().lower()
    if shell and shell != current:
        print("\nNote: QWEN_URL is also set in this shell and wins over .env (unset QWEN_URL)")
    if not current:
        print("\nIn use: none set (QWEN_URL is not in .env)")
    elif current not in servers:
        print("\nIn use: QWEN_URL in .env is an address, not a name")
    print("\nSwitch:  python qwen_server.py <name>      add one:  QWEN_URL_<NAME>=http://... in .env")


def use(name: str):
    if not ENV.exists():
        raise SystemExit("  No .env yet: cp .env.sample .env")
    text = ENV.read_text()
    if name == "default":
        ENV.write_text(LINE.sub("", text, count=1))
        print("Qwen server: default (QWEN_URL removed from .env)")
    else:
        servers = qwen_servers()
        if name not in servers:
            raise SystemExit(f"  Unknown Qwen server '{name}': add QWEN_URL_{name.upper()}=http://... "
                             f"to .env (known: {', '.join(servers)})")
        text = LINE.sub(f"QWEN_URL={name}", text, count=1) if LINE.search(text) else \
            text.rstrip("\n") + f"\nQWEN_URL={name}\n"
        ENV.write_text(text)
        print(f"Qwen server: {name} ({_up(servers[name])})")
    print("The CLIs use it from the next run; restart the backend to switch it.")


if __name__ == "__main__":
    if len(sys.argv) > 2 or sys.argv[1:2] in (["-h"], ["--help"]):
        raise SystemExit(__doc__)
    use(sys.argv[1].lower().replace("-", "_")) if len(sys.argv) == 2 else show()
