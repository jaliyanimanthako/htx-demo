"""
Browser view for headless runs (e.g. on the GPU server)
=======================================================
Serves the side-by-side frames as an MJPEG stream over HTTP, so the live
inference can be watched from any browser:

    streamer = MJPEGStreamer(8090).start()     # prints the URL
    streamer.publish(frame)                    # call once per displayed frame
    streamer.stop()

Open http://<server-ip>:8090 — or, if the port is not reachable, tunnel it:
    ssh -L 8090:localhost:8090 user@server   →   http://localhost:8090
"""

import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

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
  <div>Left: input stream &middot; Right: Qwen exit / obstruction detection</div>
</div></body></html>"""


class MJPEGStreamer:
    def __init__(self, port: int = 8090, host: str = "0.0.0.0", quality: int = 80):
        self.host, self.port, self.quality = host, port, quality
        self._jpeg = None
        self._seq = 0
        self._cond = threading.Condition()
        self._server = None
        self._running = False

    def _handler(self):
        streamer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):   # keep the console clean
                pass

            def do_GET(self):
                if self.path == "/":
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(PAGE)))
                    self.end_headers()
                    self.wfile.write(PAGE)
                elif self.path.startswith("/stream"):
                    self.send_response(200)
                    self.send_header("Cache-Control", "no-cache, private")
                    self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
                    self.end_headers()
                    last = -1
                    try:
                        while streamer._running:
                            with streamer._cond:
                                streamer._cond.wait_for(
                                    lambda: streamer._seq != last or not streamer._running,
                                    timeout=1.0)
                                if streamer._seq == last:
                                    continue
                                jpeg, last = streamer._jpeg, streamer._seq
                            self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\n"
                                             b"Content-Length: " + str(len(jpeg)).encode()
                                             + b"\r\n\r\n" + jpeg + b"\r\n")
                    except (BrokenPipeError, ConnectionResetError):
                        pass
                else:
                    self.send_error(404)

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
        print(f"  Browser view : http://{ip}:{self.port}")
        print(f"                 (not reachable? on your Mac: ssh -L {self.port}:localhost:{self.port} "
              f"<user>@<server>  then open http://localhost:{self.port})")
        return self

    def publish(self, frame) -> None:
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, self.quality])
        if not ok:
            return
        with self._cond:
            self._jpeg = buf.tobytes()
            self._seq += 1
            self._cond.notify_all()

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
