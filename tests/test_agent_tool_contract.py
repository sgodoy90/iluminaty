from iluminaty.agents import (
    EXECUTOR_TOOLS,
    OBSERVER_TOOLS,
    PLANNER_TOOLS,
    VERIFIER_TOOLS,
)
from iluminaty.mcp_server import ALL_MCP_TOOLS


def test_role_toolsets_are_subset_of_mcp_contract():
    all_mcp = set(ALL_MCP_TOOLS)
    role_sets = [
        OBSERVER_TOOLS,
        PLANNER_TOOLS,
        EXECUTOR_TOOLS,
        VERIFIER_TOOLS,
    ]
    for role_tools in role_sets:
        assert set(role_tools).issubset(all_mcp)
