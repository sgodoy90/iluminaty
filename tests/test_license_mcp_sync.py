from iluminaty import licensing, mcp_server


def test_license_sets_match_mcp_server_gates() -> None:
    assert licensing.FREE_MCP_TOOLS == mcp_server.FREE_MCP_TOOLS
    assert licensing.ALL_MCP_TOOLS == mcp_server.ALL_MCP_TOOLS


def test_all_registered_mcp_tools_are_licensed() -> None:
    names = {tool.get("name") for tool in mcp_server.TOOLS}
    assert names == mcp_server.ALL_MCP_TOOLS
