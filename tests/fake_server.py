"""A dependency-free stdio MCP server for tests. FAKE_VARIANT=good|bad selects its behaviour."""

import json
import os
import sys

VARIANT = os.environ.get("FAKE_VARIANT", "good")
GOOD_HTML = ("<!doctype html><html><head><meta charset='utf-8'></head><body><div id=r></div><script>"
             "window.parent.postMessage({jsonrpc:'2.0',id:1,method:'ui/initialize',params:{}},'*');"
             "</script></body></html>")
BAD_HTML = ("<div>not a document</div><script src='https://cdn.example.com/lib.js'></script>"
            "<script>fetch('https://api.example.com/data')</script>")
UI = "ui://fake/view.html"

if VARIANT == "good":
    TOOLS = [
        {"name": "show", "description": "Shows a view", "inputSchema": {"type": "object", "properties": {"n": {"type": "integer", "default": 3}}},
         "_meta": {"ui": {"resourceUri": UI, "visibility": ["model", "app"]}}},
        {"name": "refresh", "description": "App-only refresh", "inputSchema": {"type": "object"},
         "_meta": {"ui": {"resourceUri": UI, "visibility": ["app"]}}},
        {"name": "plain", "description": "No view", "inputSchema": {"type": "object"}},
    ]
    RESOURCES = {UI: {"uri": UI, "mimeType": "text/html;profile=mcp-app", "text": GOOD_HTML, "_meta": {"ui": {"prefersBorder": True}}}}
else:
    TOOLS = [
        {"name": "wrong_scheme", "inputSchema": {"type": "object"}, "_meta": {"ui": {"resourceUri": "https://example.com/v"}}},
        {"name": "missing", "inputSchema": {"type": "object"}, "_meta": {"ui/resourceUri": "ui://fake/missing.html"}},
        {"name": "bad_visibility", "inputSchema": {"type": "object"}, "_meta": {"ui": {"resourceUri": UI, "visibility": ["agent"]}}},
        {"name": "no_fallback", "inputSchema": {"type": "object"}, "_meta": {"ui": {"resourceUri": UI}}},
    ]
    RESOURCES = {UI: {"uri": UI, "mimeType": "text/html", "text": BAD_HTML,
                      "_meta": {"ui": {"csp": {"resourceDomains": ["cdn.example.com/path"]}, "prefersBorder": "yes"}}}}


def respond(ident, result=None, error=None):
    message = {"jsonrpc": "2.0", "id": ident}
    message.update({"error": error} if error else {"result": result})
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


for line in sys.stdin:
    request = json.loads(line)
    method, ident, params = request.get("method"), request.get("id"), request.get("params") or {}
    if ident is None:
        continue
    if method == "initialize":
        respond(ident, {"protocolVersion": "2025-11-25", "serverInfo": {"name": "fake-" + VARIANT, "version": "1"},
                        "capabilities": {"tools": {}, "resources": {}}})
    elif method == "tools/list":
        respond(ident, {"tools": TOOLS})
    elif method == "resources/list":
        respond(ident, {"resources": [{"uri": r["uri"], "name": "view", "mimeType": r["mimeType"]} for r in RESOURCES.values()]})
    elif method == "resources/read":
        item = RESOURCES.get(params.get("uri"))
        respond(ident, {"contents": [item]}) if item else respond(ident, error={"code": -32002, "message": "Resource not found"})
    elif method == "tools/call":
        name = params.get("name")
        if VARIANT == "bad" and name == "no_fallback":
            respond(ident, {"content": [], "structuredContent": {"x": 1}})
        else:
            respond(ident, {"content": [{"type": "text", "text": f"called {name}"}], "structuredContent": {"args": params.get("arguments")}})
    else:
        respond(ident, error={"code": -32601, "message": "Method not found"})
