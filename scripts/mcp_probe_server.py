"""Minimal stdio MCP server for the local-stdio cold-start probe.

Exposes one tool: `probe_ping` — returns a fixed string.
Used by mcp_cold_start_spike.py Scenario D to test whether a locally-
configured stdio MCP server at user-global level reaches SDK teammates.

Run directly (for manual testing):
    uv --directory /home/jerome/dev/claude-crew run python scripts/mcp_probe_server.py
"""

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("probe")


@mcp.tool()
def probe_ping() -> str:
    """Return a fixed probe string. Used to verify MCP tool reachability."""
    return "probe_pong"


if __name__ == "__main__":
    mcp.run(transport="stdio")
