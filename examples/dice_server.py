"""A tiny MCP Apps server: roll dice, see the result in an interactive view.

    pip install "mcp>=1.26,<2"
    python examples/dice_server.py            # Streamable HTTP on http://127.0.0.1:8001/mcp
    python examples/dice_server.py --stdio    # for desktop hosts
    mcp-apps-preview serve --url http://127.0.0.1:8001/mcp --open

The view inlines mcp_apps_preview/web/view-client.js, so it works under the
default restrictive CSP with no network access at all.
"""

import random
import sys
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult, TextContent

VIEW = "ui://dice/view.html"
CLIENT = (Path(__file__).resolve().parents[1] / "mcp_apps_preview/web/view-client.js").read_text()
HTML = """<!doctype html>
<html><head><meta charset="utf-8"><style>
:root{--bg:var(--color-background-primary,#fff);--fg:var(--color-text-primary,#16181d);--muted:var(--color-text-secondary,#667085)}
body{margin:0;background:var(--bg);color:var(--fg);font:15px system-ui,sans-serif}
.box{padding:20px;text-align:center}.die{font-size:72px;font-weight:800;letter-spacing:-.03em}
button{font:inherit;border-radius:10px;border:1px solid var(--muted);background:transparent;color:var(--fg);padding:8px 14px;cursor:pointer}
p{color:var(--muted)}
</style></head><body><div class="box"><div class="die" id="die">…</div><p id="meta"></p>
<button id="again">Roll again</button></div>
<script>__CLIENT__</script>
<script>
let sides = 6;
function show(result) {
  const data = result.structuredContent || {};
  document.getElementById('die').textContent = data.value ?? '?';
  document.getElementById('meta').textContent = data.sides ? `d${data.sides}` : '';
}
McpAppLite.connect({name: 'dice-view', version: '1.0.0'}, {
  onToolInput: args => { sides = args.sides || 6; },
  onToolResult: show,
}).then(app => {
  document.getElementById('again').onclick = async () => show(await app.callTool('roll', {sides}));
});
</script></body></html>""".replace("__CLIENT__", CLIENT)

server = FastMCP("Dice", stateless_http=True, json_response=True, port=8001)


@server.resource(VIEW, name="dice_view", mime_type="text/html;profile=mcp-app", meta={"ui": {"prefersBorder": True}})
def view() -> str:
    return HTML


@server.tool(meta={"ui": {"resourceUri": VIEW, "visibility": ["model", "app"]}})
def roll(sides: int = 6) -> CallToolResult:
    """Roll one die with the given number of sides."""
    value = random.randint(1, max(2, min(sides, 1000)))
    return CallToolResult(content=[TextContent(type="text", text=f"Rolled {value} on a d{sides}")],
                          structuredContent={"value": value, "sides": sides})


if __name__ == "__main__":
    server.run(transport="stdio" if "--stdio" in sys.argv else "streamable-http")
