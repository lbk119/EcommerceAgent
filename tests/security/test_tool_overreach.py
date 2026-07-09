from __future__ import annotations

import pytest

from agent.security.permissions import decide_tool_permission


@pytest.mark.security
def test_agent_cannot_use_tool_without_declared_permission() -> None:
    decision = decide_tool_permission("cross_tenant_query", ["tenant:admin"], ["orders:read"], risk="high", actor="data_expert")

    assert decision.allowed is False
    assert decision.reason == "missing required permissions"