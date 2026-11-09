#!/usr/bin/env python3
"""Serve the AnvilDroid GUI frontend in a normal browser for visual checks.

Serves crates/anvildroid-gui/src-frontend/ unchanged and injects a fake
window.__TAURI__ (fixtures.js + tauri-stub.js) into index.html. No Rust build,
no controller, no Android needed. Source files are never modified.

  python3 .claude/skills/anvildroid-ui-preview/serve.py [--port 1430]
  open http://127.0.0.1:1430/#theme=dark   (or #theme=light)
"""
import argparse
import http.server
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
FRONTEND = ROOT / 'crates/anvildroid-gui/src-frontend'
INJECT = b'<head>\n<script src="/__preview/fixtures.js"></script>\n<script src="/__preview/tauri-stub.js"></script>'


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(FRONTEND), **kwargs)

    def end_headers(self):
        self.send_header('Cache-Control', 'no-store')
        super().end_headers()

    def do_GET(self):
        path = self.path.split('?', 1)[0].split('#', 1)[0]
        if path.startswith('/__preview/'):
            name = path.rsplit('/', 1)[1]
            if name not in ('fixtures.js', 'tauri-stub.js', 'audit-spacing.js'):
                return self.send_error(404)
            return self._send((HERE / name).read_bytes(), 'text/javascript')
        if path in ('/', '/index.html'):
            html = (FRONTEND / 'index.html').read_bytes().replace(b'<head>', INJECT, 1)
            return self._send(html, 'text/html; charset=utf-8')
        return super().do_GET()

    def _send(self, body, kind):
        self.send_response(200)
        self.send_header('Content-Type', kind)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        if args and str(args[1]) != '200':
            super().log_message(fmt, *args)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=1430)
    port = parser.parse_args().port
    server = http.server.ThreadingHTTPServer(('127.0.0.1', port), Handler)
    print(f'AnvilDroid UI preview: http://127.0.0.1:{port}/#theme=dark  (Ctrl+C to stop)', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
