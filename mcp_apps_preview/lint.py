"""Conformance checks for MCP Apps servers (SEP-1865, stable 2026-01-26).

Each finding has a severity (error, warning, info), a rule id, a subject (tool or
resource) and a message with the spec requirement it relates to. Errors are
spec violations that break rendering in compliant hosts; warnings are likely
breakage (for example external scripts the host CSP will block); info notes
are advisory.
"""

import base64
import json
import re
from urllib.parse import urlparse

from .client import UI_MIME, MCPError, ui_link

VISIBILITY = {"model", "app"}
CSP_KEYS = ("connectDomains", "resourceDomains", "frameDomains", "baseUriDomains")
_SRC = re.compile(r"""(?:src|href)\s*=\s*["'](https?://[^"'\s>]+)""", re.I)
_IMPORT = re.compile(r"""(?:import\s[^;]*?from\s*|import\s*\(\s*|@import\s+(?:url\()?)\s*["'](https?://[^"'\s)]+)""", re.I)
_FETCH = re.compile(r"""(?:fetch\s*\(|new\s+WebSocket\s*\(|new\s+EventSource\s*\()\s*["'`](https?://|wss?://)([^"'`/\s]+)""", re.I)


class Report:
    def __init__(self):
        self.findings = []

    def add(self, severity, rule, subject, message):
        self.findings.append({"severity": severity, "rule": rule, "subject": subject, "message": message})

    @property
    def errors(self):
        return [f for f in self.findings if f["severity"] == "error"]

    def to_json(self):
        counts = {s: sum(1 for f in self.findings if f["severity"] == s) for s in ("error", "warning", "info")}
        return {"ok": not self.errors, "counts": counts, "findings": self.findings}


def _origin(url):
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else None


def _allowed(origin, domains):
    for domain in domains or []:
        if domain == origin:
            return True
        if "*." in domain:
            scheme, _, host = domain.partition("://")
            suffix = host.replace("*.", ".", 1)
            if origin.startswith(scheme + "://") and origin.split("://", 1)[1].endswith(suffix):
                return True
    return False


def check_csp(report, subject, csp):
    if csp is None:
        return {}
    if not isinstance(csp, dict):
        report.add("error", "csp-shape", subject, "_meta.ui.csp must be an object")
        return {}
    for key, value in csp.items():
        if key not in CSP_KEYS:
            report.add("warning", "csp-unknown-key", subject, f"Unknown CSP key {key!r}; hosts ignore it")
            continue
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            report.add("error", "csp-shape", subject, f"csp.{key} must be a list of origin strings")
            continue
        for origin in value:
            if not re.match(r"^(https|wss)://(\*\.)?[A-Za-z0-9.-]+(:\d+)?$", origin):
                report.add("warning", "csp-origin", subject,
                           f"csp.{key} entry {origin!r} is not an https/wss origin (no paths); hosts may reject it")
    return csp


def check_html(report, subject, html, csp):
    head = html.lstrip()[:512].lower()
    if not (head.startswith("<!doctype html") or "<html" in head):
        report.add("error", "html-document", subject, "Content MUST be a valid HTML5 document (<!doctype html> / <html>)")
    resources = csp.get("resourceDomains") or []
    blocked = set()
    for url in _SRC.findall(html) + _IMPORT.findall(html):
        origin = _origin(url)
        if origin and not _allowed(origin, resources):
            blocked.add(origin)
    for origin in sorted(blocked):
        report.add("warning", "csp-resource-undeclared", subject,
                   f"Loads {origin} but it is not in _meta.ui.csp.resourceDomains; the host CSP will block it")
    connects = csp.get("connectDomains") or []
    for origin in sorted({scheme + host for scheme, host in _FETCH.findall(html)}):
        if not _allowed(origin, connects):
            report.add("warning", "csp-connect-undeclared", subject,
                       f"Connects to {origin} but it is not in csp.connectDomains (default connect-src 'none')")
    uses_sdk = "@modelcontextprotocol/ext-apps" in html
    if not uses_sdk and "ui/initialize" not in html:
        report.add("info", "no-handshake", subject,
                   "No ui/initialize handshake or ext-apps import found; the view may never receive tool results")
    if uses_sdk and ("unpkg.com" in html or "cdn.jsdelivr.net" in html):
        report.add("info", "cdn-sdk", subject, "View loads its SDK from a CDN; strict-CSP or offline hosts cannot run it")


def lint(client, calls=None):
    """Lint a connected client. `calls` is an optional {tool_name: arguments} to exercise."""
    report = Report()
    if client.protocol:
        report.add("info", "protocol", "server", f"Negotiated protocol {client.protocol}")
    try:
        tools = client.tools()
    except MCPError as exc:
        report.add("error", "tools-list", "server", f"tools/list failed: {exc}")
        return report
    listed = {}
    try:
        listed = {r.get("uri"): r for r in client.resources()}
    except MCPError as exc:
        report.add("warning", "resources-list", "server", f"resources/list failed: {exc}")
    ui_tools = 0
    read_cache = {}
    for tool in tools:
        name = f"tool {tool.get('name')}"
        meta = tool.get("_meta") or {}
        if "ui" in meta and not isinstance(meta["ui"], dict):
            report.add("error", "tool-meta-shape", name, "_meta.ui must be an object")
            continue
        uri, visibility, deprecated = ui_link(tool)
        if deprecated:
            report.add("warning", "deprecated-resource-uri", name,
                       'Flat _meta["ui/resourceUri"] is deprecated; use _meta.ui.resourceUri (removed before GA)')
        if not isinstance(visibility, list) or not visibility or not set(visibility) <= VISIBILITY:
            report.add("error", "visibility", name, f"_meta.ui.visibility must be a non-empty subset of model/app, got {visibility!r}")
        elif visibility == ["app"] and not uri:
            report.add("info", "app-only-without-view", name, "App-only tool without resourceUri; only views of this server can call it")
        if not uri:
            continue
        ui_tools += 1
        if not uri.startswith("ui://"):
            report.add("error", "resource-scheme", name, f"resourceUri {uri!r} MUST use the ui:// scheme")
            continue
        if uri in listed and listed[uri].get("mimeType") not in (None, UI_MIME):
            report.add("error", "listed-mime", f"resource {uri}", f"Listed mimeType {listed[uri].get('mimeType')!r} is not {UI_MIME}")
        if uri not in read_cache:
            try:
                read_cache[uri] = client.read(uri)
            except MCPError as exc:
                read_cache[uri] = exc
        result = read_cache[uri]
        if isinstance(result, MCPError):
            report.add("error", "resource-missing", name, f"resources/read {uri} failed: {result}. Resource MUST exist on the server")
            continue
    for uri, result in read_cache.items():
        subject = f"resource {uri}"
        if isinstance(result, MCPError):
            continue
        contents = (result or {}).get("contents") or []
        if not contents:
            report.add("error", "resource-empty", subject, "resources/read returned no contents")
            continue
        item = contents[0]
        if item.get("mimeType") != UI_MIME:
            report.add("error", "resource-mime", subject, f"mimeType MUST be {UI_MIME!r}, got {item.get('mimeType')!r}")
        if item.get("uri") not in (None, uri):
            report.add("warning", "resource-uri-echo", subject, f"Contents uri {item.get('uri')!r} does not match {uri!r}")
        html = item.get("text")
        if html is None and item.get("blob"):
            try:
                html = base64.b64decode(item["blob"]).decode("utf-8")
            except (ValueError, UnicodeDecodeError):
                report.add("error", "resource-blob", subject, "blob is not base64-encoded UTF-8 HTML")
                continue
        if not html:
            report.add("error", "resource-content", subject, "Content MUST be provided via text or blob")
            continue
        ui_meta = ((item.get("_meta") or {}).get("ui")) or ((listed.get(uri) or {}).get("_meta") or {}).get("ui") or {}
        if "prefersBorder" in ui_meta and not isinstance(ui_meta["prefersBorder"], bool):
            report.add("warning", "prefers-border", subject, "prefersBorder should be a boolean")
        csp = check_csp(report, subject, ui_meta.get("csp"))
        check_html(report, subject, html, csp or {})
        report.add("info", "resource-size", subject, f"{len(html.encode('utf-8'))} bytes of HTML")
    if not ui_tools:
        report.add("warning", "no-ui-tools", "server", "No tool declares _meta.ui.resourceUri; nothing will render as an MCP App")
    for tool_name, arguments in (calls or {}).items():
        subject = f"call {tool_name}"
        try:
            result = client.call(tool_name, arguments)
        except MCPError as exc:
            report.add("error", "call-failed", subject, f"tools/call failed: {exc}")
            continue
        content = result.get("content")
        if not isinstance(content, list) or not content:
            report.add("error", "text-fallback", subject, "Tools MUST return a meaningful content array even when UI is available")
        if result.get("structuredContent") is not None:
            size = len(json.dumps(result["structuredContent"]))
            report.add("info", "structured-content", subject, f"structuredContent present ({size} bytes)")
        if result.get("isError"):
            report.add("warning", "call-is-error", subject, "The call returned isError=true")
    return report


def format_text(report, server_name=""):
    marks = {"error": "✗", "warning": "!", "info": "·"}
    lines = [f"MCP Apps lint{(' · ' + server_name) if server_name else ''}"]
    order = {"error": 0, "warning": 1, "info": 2}
    for f in sorted(report.findings, key=lambda f: (order[f["severity"]], f["subject"])):
        lines.append(f"  {marks[f['severity']]} {f['severity']:<7} {f['rule']:<26} {f['subject']}: {f['message']}")
    summary = report.to_json()["counts"]
    lines.append(f"{'PASS' if not report.errors else 'FAIL'}: {summary['error']} errors, {summary['warning']} warnings, {summary['info']} notes")
    return "\n".join(lines)
