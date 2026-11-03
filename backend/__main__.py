"""
Run the backend:   python -m backend [--host 0.0.0.0] [--port 8100]
"""

import argparse
import os
import socket
from pathlib import Path

import uvicorn


def _free_port(start: int) -> int:
    for port in range(start, start + 50):
        s = socket.socket()
        try:
            s.bind(("0.0.0.0", port))
            return port
        except OSError:
            continue
        finally:
            s.close()
    raise SystemExit(f"no free port in {start}-{start + 49}")


def main():
    ap = argparse.ArgumentParser(description="Exit Monitor FastAPI backend")
    ap.add_argument("--config", help="TOML settings file (default: config.toml or HTX_CONFIG)")
    ap.add_argument("--host", default=None)
    ap.add_argument("--port", type=int, default=None, help="first port to try (default 8100)")
    args = ap.parse_args()

    if args.config:
        os.environ["HTX_CONFIG"] = args.config
    from backend import settings
    port = _free_port(args.port if args.port is not None else settings.CONFIG["server"]["port"])
    port_file = Path(os.environ.get("BACKEND_PORT_FILE", Path.home() / "exit-monitor" / "port"))
    try:
        port_file.parent.mkdir(parents=True, exist_ok=True)
        port_file.write_text(str(port))
    except OSError:
        pass
    print(flush=True)
    print(f"  Backend      : http://<this-host>:{port}   (API docs: /docs)")
    print(f"  Models       : LocateAnything {settings.LOCATE_URL} | Qwen {settings.QWEN_URL}")
    print(f"  Videos from  : {settings.VIDEO_DIR}")
    print(f"  Auth         : {'token required' if settings.TOKEN else 'OFF (set BACKEND_TOKEN)'}"
          f" | CORS: {', '.join(settings.CORS_ORIGINS)}", flush=True)
    uvicorn.run("backend.app:app", host=args.host or settings.CONFIG["server"]["host"], port=port, log_level="warning")


if __name__ == "__main__":
    main()
