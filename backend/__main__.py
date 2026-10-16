"""
Run the backend:   python -m backend [--host 0.0.0.0] [--port 8100]
"""

import argparse
import socket

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
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8100, help="first port to try (default 8100)")
    args = ap.parse_args()

    from backend import settings
    port = _free_port(args.port)
    print(flush=True)
    print(f"  Backend      : http://<this-host>:{port}   (API docs: /docs, web app: /)")
    print(f"  Models       : LocateAnything {settings.LOCATE_URL} | Qwen {settings.QWEN_URL}")
    print(f"  Videos from  : {settings.VIDEO_DIR}")
    print(f"  Auth         : {'token required' if settings.TOKEN else 'OFF (set BACKEND_TOKEN)'}"
          f" | CORS: {', '.join(settings.CORS_ORIGINS)}", flush=True)
    uvicorn.run("backend.app:app", host=args.host, port=port, log_level="warning")


if __name__ == "__main__":
    main()
