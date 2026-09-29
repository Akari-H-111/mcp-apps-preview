"""mcp-apps-preview: lint an MCP Apps server, or open a local preview host for its views.

  mcp-apps-preview lint  --url http://127.0.0.1:8000/mcp [--call TOOL '{"x": 1}'] [--json]
  mcp-apps-preview lint  --stdio "python server.py"
  mcp-apps-preview serve --url http://127.0.0.1:8000/mcp [--port 8790] [--open]
"""

import argparse
import json
import sys
import webbrowser

from .client import MCPError, open_client
from .lint import format_text, lint
from .server import serve


def main(argv=None):
    parser = argparse.ArgumentParser(prog="mcp-apps-preview", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("lint", "serve"):
        p = sub.add_parser(name)
        target = p.add_mutually_exclusive_group(required=True)
        target.add_argument("--url", help="Streamable HTTP endpoint, e.g. http://127.0.0.1:8000/mcp")
        target.add_argument("--stdio", help="Command that starts a stdio MCP server")
        p.add_argument("--header", action="append", default=[], metavar="NAME:VALUE", help="Extra HTTP header")
        if name == "lint":
            p.add_argument("--call", nargs=2, action="append", default=[], metavar=("TOOL", "JSON_ARGS"),
                           help="Also call a tool and check its result (text fallback, structuredContent)")
            p.add_argument("--json", action="store_true", help="Print the report as JSON")
        else:
            p.add_argument("--port", type=int, default=8790)
            p.add_argument("--open", action="store_true", help="Open the preview in a browser")
    args = parser.parse_args(argv)
    headers = dict(h.split(":", 1) for h in args.header)
    try:
        client = open_client(args.url, args.stdio, {k.strip(): v.strip() for k, v in headers.items()})
    except (MCPError, OSError, ValueError) as exc:
        print(f"Cannot connect: {exc}", file=sys.stderr)
        return 2
    label = args.url or f"stdio: {args.stdio}"
    try:
        if args.command == "lint":
            calls = {}
            for tool, raw in args.call:
                calls[tool] = json.loads(raw)
            report = lint(client, calls)
            print(json.dumps(report.to_json(), indent=2) if args.json else
                  format_text(report, (client.server_info or {}).get("name", "")))
            return 1 if report.errors else 0
        server = serve(client, label, args.port)
        url = f"http://127.0.0.1:{args.port}/"
        print(f"MCP Apps Preview for {label}\n  {url}  (views render from http://localhost:{args.port})")
        if args.open:
            webbrowser.open(url)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    sys.exit(main())
