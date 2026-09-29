"""Local MCP Apps preview host: a web page that renders any server's ui:// views.

The page is a spec-following web host: it wraps each view in a cross-origin
sandbox proxy (127.0.0.1 <-> localhost), injects the declared CSP, runs the
ui/initialize handshake, delivers tool input and results, proxies app-initiated
tools/call with visibility enforcement, and logs every JSON-RPC message.
"""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import __version__
from .client import MCPError, ui_link
from .lint import lint

WEB = Path(__file__).with_name("web")
STATIC = {"/": ("host.html", "text/html"), "/host.js": ("host.js", "text/javascript"),
          "/host.css": ("host.css", "text/css"), "/sandbox.html": ("sandbox.html", "text/html"),
          "/view-client.js": ("view-client.js", "text/javascript")}
MAX_BODY = 2_000_000


class Preview:
    def __init__(self, client, label):
        self.client, self.label = client, label

    def describe(self):
        tools = []
        for tool in self.client.tools():
            uri, visibility, _ = ui_link(tool)
            tools.append({"name": tool.get("name"), "title": tool.get("title"), "description": tool.get("description", ""),
                          "inputSchema": tool.get("inputSchema") or {}, "ui": uri, "visibility": visibility})
        tools.sort(key=lambda t: (t["ui"] is None, t["name"]))
        return {"version": __version__, "server": self.client.server_info, "protocol": self.client.protocol,
                "label": self.label, "tools": tools}

    def call(self, name, arguments, caller):
        tool = next((t for t in self.client.tools() if t.get("name") == name), None)
        if tool is None:
            raise PermissionError(f"Unknown tool {name!r}")
        visibility = ui_link(tool)[1]
        needed = "app" if caller == "app" else "model"
        if needed not in visibility:
            raise PermissionError(f"Tool {name!r} visibility {visibility} does not include {needed!r}")
        return self.client.call(name, arguments)

    def resource(self, uri):
        if not uri.startswith("ui://"):
            raise PermissionError("Only ui:// resources are rendered")
        contents = (self.client.read(uri) or {}).get("contents") or []
        if not contents:
            raise MCPError("Empty resource")
        return contents[0]


def make_handler(preview):
    class Handler(BaseHTTPRequestHandler):
        server_version = "mcp-apps-preview/" + __version__

        def log_message(self, fmt, *args):
            pass

        def _send(self, status, body, kind="application/json", extra=None):
            data = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", kind + "; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            for key, value in (extra or {}).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            parsed = urlparse(self.path)
            if parsed.path in STATIC:
                name, kind = STATIC[parsed.path]
                extra = {"Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                         "frame-src http://127.0.0.1:* http://localhost:*; img-src 'self' data:"} if name == "host.html" else None
                return self._send(200, (WEB / name).read_bytes(), kind, extra)
            try:
                if parsed.path == "/api/server":
                    return self._send(200, preview.describe())
                if parsed.path == "/api/lint":
                    return self._send(200, lint(preview.client).to_json())
                if parsed.path == "/api/resource":
                    return self._send(200, preview.resource(parse_qs(parsed.query).get("uri", [""])[0]))
            except PermissionError as exc:
                return self._send(403, {"error": str(exc)})
            except MCPError as exc:
                return self._send(502, {"error": str(exc), "code": exc.code})
            self._send(404, {"error": "Not found"})

        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                return self._send(413, {"error": "Request too large"})
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError:
                return self._send(400, {"error": "Invalid JSON"})
            if self.path != "/api/call":
                return self._send(404, {"error": "Not found"})
            if body.get("caller") not in ("host", "app") or not isinstance(body.get("arguments", {}), dict):
                return self._send(400, {"error": "caller must be host or app; arguments must be an object"})
            try:
                return self._send(200, preview.call(str(body.get("name")), body.get("arguments") or {}, body["caller"]))
            except PermissionError as exc:
                return self._send(403, {"error": str(exc)})
            except MCPError as exc:
                return self._send(502, {"error": str(exc), "code": exc.code})

    return Handler


def serve(client, label, port=8790):
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(Preview(client, label)))
    return server
