"""Standard-library MCP client for Streamable HTTP and stdio servers.

It advertises MCP Apps support (extension io.modelcontextprotocol/ui) so servers
that check client capabilities expose their UI-linked tools.
"""

import itertools
import json
import os
import shlex
import subprocess
import threading
import urllib.error
import urllib.request

PROTOCOL = "2025-11-25"
UI_EXTENSION = "io.modelcontextprotocol/ui"
UI_MIME = "text/html;profile=mcp-app"


class MCPError(RuntimeError):
    """A transport or JSON-RPC error. `code` is the JSON-RPC error code when known."""

    def __init__(self, message, code=None):
        super().__init__(message)
        self.code = code


def _unwrap(data):
    if "error" in data:
        error = data["error"] or {}
        raise MCPError(error.get("message", "MCP error"), error.get("code"))
    return data.get("result")


class _Base:
    client_info = {"name": "mcp-apps-preview", "version": "0.1.0"}

    def __init__(self):
        self._ids = itertools.count(1)
        self._lock = threading.Lock()
        self.server_info, self.instructions, self.protocol, self.capabilities = None, "", None, {}

    def request(self, method, params=None):
        raise NotImplementedError

    def notify(self, method, params=None):
        raise NotImplementedError

    def connect(self):
        result = self.request("initialize", {
            "protocolVersion": PROTOCOL, "clientInfo": self.client_info,
            "capabilities": {"extensions": {UI_EXTENSION: {"mimeTypes": [UI_MIME]}}}})
        self.server_info = result.get("serverInfo")
        self.instructions = result.get("instructions") or ""
        self.protocol = result.get("protocolVersion")
        self.capabilities = result.get("capabilities") or {}
        self.notify("notifications/initialized")
        return result

    def list_all(self, method, key):
        items, cursor = [], None
        for _ in range(100):
            result = self.request(method, {"cursor": cursor} if cursor else {})
            items += result.get(key, [])
            cursor = result.get("nextCursor")
            if not cursor:
                return items
        raise MCPError(f"{method} pagination did not terminate")

    def tools(self):
        return self.list_all("tools/list", "tools")

    def resources(self):
        if "resources" not in self.capabilities:
            return []
        return self.list_all("resources/list", "resources")

    def read(self, uri):
        return self.request("resources/read", {"uri": uri})

    def call(self, name, arguments):
        return self.request("tools/call", {"name": name, "arguments": arguments})

    def close(self):
        pass


class HTTPClient(_Base):
    """Streamable HTTP: JSON-RPC over POST; accepts JSON or SSE responses."""

    def __init__(self, url, headers=None, timeout=60):
        super().__init__()
        self.url, self.timeout, self.extra = url, timeout, dict(headers or {})
        self.session_id = None

    def _post(self, message, expect):
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
                   "MCP-Protocol-Version": PROTOCOL, **self.extra}
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        request = urllib.request.Request(self.url, data=json.dumps(message).encode(), headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                self.session_id = response.headers.get("Mcp-Session-Id") or self.session_id
                body = response.read().decode("utf-8")
                kind = response.headers.get("Content-Type", "")
        except urllib.error.HTTPError as exc:
            raise MCPError(f"HTTP {exc.code}: {exc.read()[:300].decode(errors='replace')}") from exc
        except urllib.error.URLError as exc:
            raise MCPError(f"Server unreachable at {self.url}: {exc.reason}") from exc
        if not expect:
            return None
        if "text/event-stream" in kind:
            for line in body.splitlines():
                if line.startswith("data:"):
                    data = json.loads(line[5:].strip())
                    if data.get("id") == message["id"]:
                        return _unwrap(data)
            raise MCPError("No response event in SSE stream")
        return _unwrap(json.loads(body))

    def request(self, method, params=None):
        with self._lock:
            return self._post({"jsonrpc": "2.0", "id": next(self._ids), "method": method, "params": params or {}}, True)

    def notify(self, method, params=None):
        with self._lock:
            self._post({"jsonrpc": "2.0", "method": method, "params": params or {}}, False)


class StdioClient(_Base):
    """stdio: newline-delimited JSON-RPC with a child process."""

    def __init__(self, command, timeout=60, env=None, cwd=None):
        super().__init__()
        argv = shlex.split(command) if isinstance(command, str) else list(command)
        self.process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                        text=True, bufsize=1, env={**os.environ, **(env or {})}, cwd=cwd)
        self.timeout = timeout
        self._responses = {}
        self._arrived = threading.Condition()
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._reader.start()

    def _read(self):
        for line in self.process.stdout:
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "id" in message and "method" not in message:
                with self._arrived:
                    self._responses[message["id"]] = message
                    self._arrived.notify_all()
        with self._arrived:
            self._arrived.notify_all()

    def _write(self, message):
        if self.process.poll() is not None:
            raise MCPError("stdio server exited")
        self.process.stdin.write(json.dumps(message) + "\n")
        self.process.stdin.flush()

    def request(self, method, params=None):
        with self._lock:
            ident = next(self._ids)
            self._write({"jsonrpc": "2.0", "id": ident, "method": method, "params": params or {}})
        with self._arrived:
            done = self._arrived.wait_for(lambda: ident in self._responses or self.process.poll() is not None,
                                          timeout=self.timeout)
            if ident not in self._responses:
                raise MCPError(f"No response to {method}" + ("" if done else " (timeout)"))
            return _unwrap(self._responses.pop(ident))

    def notify(self, method, params=None):
        with self._lock:
            self._write({"jsonrpc": "2.0", "method": method, "params": params or {}})

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        for stream in (self.process.stdin, self.process.stdout):
            try:
                stream.close()
            except OSError:
                pass


def open_client(url=None, stdio=None, headers=None):
    if bool(url) == bool(stdio):
        raise ValueError("Give exactly one of --url or --stdio")
    client = HTTPClient(url, headers) if url else StdioClient(stdio)
    client.connect()
    return client


def ui_link(tool):
    """(resourceUri, visibility, used_deprecated_key) for a tool per SEP-1865."""
    meta = tool.get("_meta") or {}
    ui = meta.get("ui") if isinstance(meta.get("ui"), dict) else {}
    uri = ui.get("resourceUri")
    deprecated = "ui/resourceUri" in meta
    return uri or meta.get("ui/resourceUri"), ui.get("visibility", ["model", "app"]), deprecated


__all__ = ["HTTPClient", "StdioClient", "MCPError", "open_client", "ui_link", "UI_MIME"]
