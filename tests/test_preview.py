"""Lint rules, the stdio client, and the preview host's HTTP API (standard library only)."""

import json
from pathlib import Path
import subprocess
import sys
import threading
import unittest
import urllib.error
import urllib.request

from mcp_apps_preview.client import StdioClient, ui_link
from mcp_apps_preview.lint import lint
from mcp_apps_preview.server import serve

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent


def fake(variant):
    client = StdioClient([sys.executable, str(HERE / "fake_server.py")], env={"FAKE_VARIANT": variant})
    client.connect()
    return client


class LintTests(unittest.TestCase):
    def test_conforming_server_passes(self):
        client = fake("good")
        try:
            report = lint(client, {"show": {"n": 2}})
        finally:
            client.close()
        self.assertEqual(report.errors, [])
        rules = {f["rule"] for f in report.findings}
        self.assertIn("structured-content", rules)
        self.assertNotIn("no-handshake", rules)
        self.assertTrue(report.to_json()["ok"])

    def test_violations_are_reported_with_spec_rules(self):
        client = fake("bad")
        try:
            report = lint(client, {"no_fallback": {}})
        finally:
            client.close()
        by_rule = {}
        for f in report.findings:
            by_rule.setdefault(f["rule"], []).append(f)
        for rule in ("resource-scheme", "resource-missing", "visibility", "resource-mime", "html-document",
                     "text-fallback", "listed-mime"):
            self.assertEqual(by_rule[rule][0]["severity"], "error", rule)
        for rule in ("deprecated-resource-uri", "csp-origin", "csp-resource-undeclared", "csp-connect-undeclared",
                     "prefers-border"):
            self.assertEqual(by_rule[rule][0]["severity"], "warning", rule)
        self.assertIn("https://cdn.example.com", by_rule["csp-resource-undeclared"][0]["message"])
        self.assertFalse(report.to_json()["ok"])

    def test_ui_link_reads_nested_and_deprecated_keys(self):
        self.assertEqual(ui_link({"_meta": {"ui": {"resourceUri": "ui://a", "visibility": ["app"]}}}), ("ui://a", ["app"], False))
        self.assertEqual(ui_link({"_meta": {"ui/resourceUri": "ui://b"}}), ("ui://b", ["model", "app"], True))
        self.assertEqual(ui_link({}), (None, ["model", "app"], False))

    def test_cli_exit_codes(self):
        base = [sys.executable, "-m", "mcp_apps_preview", "lint", "--stdio", f"{sys.executable} {HERE / 'fake_server.py'}"]
        good = subprocess.run(base, cwd=ROOT, capture_output=True, text=True, env={"FAKE_VARIANT": "good", "PATH": ""})
        self.assertEqual(good.returncode, 0, good.stdout + good.stderr)
        self.assertIn("PASS", good.stdout)
        bad = subprocess.run(base + ["--json"], cwd=ROOT, capture_output=True, text=True, env={"FAKE_VARIANT": "bad", "PATH": ""})
        self.assertEqual(bad.returncode, 1)
        self.assertFalse(json.loads(bad.stdout)["ok"])


class PreviewHostTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = fake("good")
        cls.server = serve(cls.client, "test", 0)
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.client.close()

    def request(self, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=20) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def test_static_pages_and_csp(self):
        with urllib.request.urlopen(self.base + "/") as response:
            self.assertIn("frame-src http://127.0.0.1:* http://localhost:*", response.headers["Content-Security-Policy"])
        for page, marker in (("/sandbox.html", b"sandbox-proxy-ready"), ("/view-client.js", b"McpAppLite"), ("/host.js", b"ui/initialize")):
            with urllib.request.urlopen(self.base + page) as response:
                self.assertIn(marker, response.read())

    def test_describe_resource_and_visibility_enforcement(self):
        status, info = self.request("/api/server")
        self.assertEqual(status, 200)
        self.assertEqual([t["name"] for t in info["tools"]][:2], ["refresh", "show"])
        status, resource = self.request("/api/resource?uri=ui%3A%2F%2Ffake%2Fview.html")
        self.assertEqual(resource["mimeType"], "text/html;profile=mcp-app")
        self.assertEqual(self.request("/api/resource?uri=https%3A%2F%2Fx")[0], 403)
        self.assertEqual(self.request("/api/call", {"name": "show", "arguments": {"n": 1}, "caller": "host"})[0], 200)
        self.assertEqual(self.request("/api/call", {"name": "refresh", "arguments": {}, "caller": "app"})[0], 200)
        status, denied = self.request("/api/call", {"name": "refresh", "arguments": {}, "caller": "host"})
        self.assertEqual(status, 403)
        self.assertIn("does not include 'model'", denied["error"])
        self.assertEqual(self.request("/api/call", {"name": "plain", "arguments": {}, "caller": "nobody"})[0], 400)
        status, report = self.request("/api/lint")
        self.assertTrue(report["ok"])


if __name__ == "__main__":
    unittest.main()
