"""
Browser view for headless runs (e.g. on the GPU server)
=======================================================
Serves frames as MJPEG streams over HTTP, plus (optionally) the web frontend:

    streamer = MJPEGStreamer(8090).start()               # simple page: one stream
    streamer = MJPEGStreamer(8090, ui_dir=ROOT / "frontend").start()   # web app
    streamer.publish(frame)                    # default stream "main"
    streamer.publish(frame, "annotated")       # named streams
    streamer.set_state({...})                  # JSON for the frontend (GET /state)
    streamer.stop()

Endpoints
  GET /                     frontend/index.html (if ui_dir) or a simple page
  GET /stream               MJPEG of "main"
  GET /stream/<name>        MJPEG of a named stream (e.g. original, annotated)
  GET /state                latest JSON state (CORS enabled)
  GET /<file>               static file from ui_dir (css / js / images)

Open http://<server-ip>:8090 — or, if the port is not reachable, tunnel it:
    ssh -L 8090:localhost:8090 user@server   →   http://localhost:8090
"""

import json
import mimetypes
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import cv2

PAGE = b"""<!doctype html>
<html><head><meta charset="utf-8"><title>Exit detection - live</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  html, body { margin: 0; height: 100%; background: #111; color: #ddd;
               font: 14px -apple-system, Segoe UI, sans-serif; }
  .wrap { display: flex; flex-direction: column; align-items: center;
          justify-content: center; min-height: 100%; gap: 8px; padding: 8px;
          box-sizing: border-box; }
  img { max-width: 100%; max-height: calc(100vh - 48px); }
</style></head>
<body><div class="wrap">
  <img src="/stream" alt="live stream">
  <div>Left: input stream &middot; Right: exit / obstruction detection</div>
</div></body></html>"""


class MJPEGStreamer:
    def __init__(self, port: int = 8090, host: str = "0.0.0.0", quality: int = 80,
                 ui_dir: Path = None):
        self.host, self.port, self.quality = host, port, quality
        self.ui_dir = Path(ui_dir).resolve() if ui_dir else None
        self._frames = {}                 # name → (jpeg bytes, seq)
        self._state = b"{}"
        self._cond = threading.Condition()
        self._server = None
        self._running = False

    # ── HTTP ─────────────────────────────────────────────────────────────────
    def _handler(self):
        streamer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):   # keep the console clean
                pass

            def _send(self, body: bytes, ctype: str, cors: bool = False):
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-cache")
                if cors:
                    self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                path = urlparse(self.path).path
                if path == "/":
                    index = streamer.ui_dir / "index.html" if streamer.ui_dir else None
                    if index and index.is_file():
                        self._send(index.read_bytes(), "text/html; charset=utf-8")
                    else:
                        self._send(PAGE, "text/html; charset=utf-8")
                elif path == "/stream" or path.startswith("/stream/"):
                    name = path[len("/stream/"):] or "main" if path != "/stream" else "main"
                    self._stream(name)
                elif path == "/state":
                    with streamer._cond:
                        body = streamer._state
                    self._send(body, "application/json", cors=True)
                elif streamer.ui_dir and self._static(path):
                    pass
                else:
                    self.send_error(404)

            def _static(self, path: str) -> bool:
                f = (streamer.ui_dir / path.lstrip("/")).resolve()
                if streamer.ui_dir not in f.parents or not f.is_file():   # no path escape
                    return False
                ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
                self._send(f.read_bytes(), ctype)
                return True

            def _stream(self, name: str):
                self.send_response(200)
                self.send_header("Cache-Control", "no-cache, private")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
                self.end_headers()
                last = -1
                try:
                    while streamer._running:
                        with streamer._cond:
                            streamer._cond.wait_for(
                                lambda: streamer._frames.get(name, (None, -1))[1] != last
                                or not streamer._running, timeout=1.0)
                            jpeg, seq = streamer._frames.get(name, (None, -1))
                            if jpeg is None or seq == last:
                                continue
                            last = seq
                        self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\n"
                                         b"Content-Length: " + str(len(jpeg)).encode()
                                         + b"\r\n\r\n" + jpeg + b"\r\n")
                except (BrokenPipeError, ConnectionResetError):
                    pass

        return Handler

    def start(self, max_tries: int = 20) -> "MJPEGStreamer":
        """Bind the first free port from `port` upward and serve in a background thread."""
        for port in range(self.port, self.port + max_tries):
            try:
                self._server = ThreadingHTTPServer((self.host, port), self._handler())
                self.port = port
                break
            except OSError:
                continue
        else:
            raise RuntimeError(f"No free port in {self.port}-{self.port + max_tries - 1}")
        self._server.daemon_threads = True
        self._running = True
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

        ip = _local_ip()
        what = "Web app" if self.ui_dir else "Browser view"
        print(f"  {what:12s} : http://{ip}:{self.port}   (this machine: http://localhost:{self.port})")
        print(f"                 (not reachable? on your Mac: ssh -L {self.port}:localhost:{self.port} "
              f"<user>@<server>  then open http://localhost:{self.port})")
        return self

    # ── data ─────────────────────────────────────────────────────────────────
    def publish(self, frame, name: str = "main") -> None:
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, self.quality])
        if not ok:
            return
        with self._cond:
            seq = self._frames.get(name, (None, 0))[1] + 1
            self._frames[name] = (buf.tobytes(), seq)
            self._cond.notify_all()

    def set_state(self, state: dict) -> None:
        body = json.dumps(state).encode()
        with self._cond:
            self._state = body

    def stop(self) -> None:
        self._running = False
        with self._cond:
            self._cond.notify_all()
        if self._server:
            self._server.shutdown()
            self._server.server_close()


def _local_ip() -> str:
    """Best-effort LAN IP (no packets are sent)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "localhost"
    finally:
        s.close()
