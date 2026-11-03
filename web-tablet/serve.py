#!/usr/bin/env python3
"""Serve only this standalone frontend; no backend/config changes or dependencies."""
import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


class Handler(SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('X-Content-Type-Options', 'nosniff')
        super().end_headers()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default='0.0.0.0', help='bind address (default: all interfaces, for Wi-Fi access)')
    parser.add_argument('--port', type=int, default=5174)
    args = parser.parse_args()
    directory = Path(__file__).resolve().parent
    server = ThreadingHTTPServer((args.host, args.port), partial(Handler, directory=str(directory)))
    print(f'Web fallback: http://localhost:{server.server_port}', flush=True)
    print(f'Phone/laptop on the same Wi-Fi: http://<this-computer-LAN-IP>:{server.server_port}', flush=True)
    print('Keep this terminal open. Press Ctrl+C to stop. The detection backend runs separately.', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
