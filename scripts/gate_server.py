"""Phase 0 gate: minimal MCP server over Streamable HTTP (mcp 2.x MCPServer, formerly FastMCP)."""

from mcp.server.mcpserver import MCPServer

mcp = MCPServer(name="threshold-gate")


@mcp.tool()
def ping(name: str) -> str:
    """Reply with a greeting to prove tools/call works."""
    return f"pong, {name}"


if __name__ == "__main__":
    mcp.run(transport="streamable-http", host="127.0.0.1", port=8000, streamable_http_path="/mcp")
