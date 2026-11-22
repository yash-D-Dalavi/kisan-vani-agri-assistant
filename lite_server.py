"""
lite_server.py - zero-dependency fallback server (Python standard library only).

Serves exactly the same routes as app.py (they share handlers.ROUTES). Use it when `pip install fastapi uvicorn` is not
possible (college lab PC, no internet) - the web workbench works identically:

    python lite_server.py            ->  http://127.0.0.1:8000/ui
"""
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qsl

import handlers


class Handler(BaseHTTPRequestHandler):
    server_version = "AgriLite/1.0"

    def _serve(self):
        u = urlparse(self.path)
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n) if n else b""
        req = handlers.Req(method=self.command, path=u.path, query=dict(parse_qsl(u.query)), headers={k: v for k, v in self.headers.items()}, body=body)
        status, payload, ctype = handlers.dispatch(req)
        data = bytes(payload) if isinstance(payload, (bytes, bytearray)) else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        if not isinstance(payload, (bytes, bytearray)):
            ctype = "application/json; charset=utf-8"
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(data)

    do_GET = do_POST = _serve

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()

    def log_message(self, fmt, *args):
        if os.getenv("AGRI_QUIET") != "1":
            sys.stderr.write("[lite] " + fmt % args + "\n")


def make_server(host: str = "127.0.0.1", port: int = 8000) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), Handler)


if __name__ == "__main__":
    host, port = os.getenv("HOST", "127.0.0.1"), int(os.getenv("PORT", "8000"))
    print(f"Lite server (stdlib only) running on http://{host}:{port}/ui   - Ctrl+C to stop")
    make_server(host, port).serve_forever()
