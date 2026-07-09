from __future__ import annotations

from pathlib import Path

from agent.sandbox.models import SandboxResult
from agent.tools.registry import tool_registry
from api.context import reset_sandbox_context, reset_session_context, set_identity_context, set_sandbox_context, set_session_context, set_thread_context
from agent.memory import MemoryIdentity


def test_read_file_content_routes_through_sandbox_client(tmp_path, monkeypatch):
    session_dir = tmp_path / "session"
    session_dir.mkdir()
    (session_dir / "orders.csv").write_text("id,amount\n1,12\n", encoding="utf-8")
    captured = {}

    class FakeSandboxClient:
        def execute(self, task):
            captured["task"] = task
            return SandboxResult(ok=True, stdout="sandbox parsed csv")

    monkeypatch.setattr("agent.sandbox.client.SandboxClient", FakeSandboxClient)
    monkeypatch.setenv("TOOL_PERMISSION_ENFORCEMENT", "true")
    session_token = set_session_context(str(session_dir))
    thread_token = set_thread_context("conv-1")
    identity_token = set_identity_context(MemoryIdentity(tenant_id="tenant-1", user_id="user-1", shop_id="shop-1", conversation_id="conv-1", task_id="task-1"))
    sandbox_token = set_sandbox_context({"tenant_id": "tenant-1", "user_id": "user-1", "shop_id": "shop-1", "task_id": "task-1", "conversation_id": "conv-1", "profile": "standard", "agent_id": "test-agent"})
    try:
        tool = tool_registry.guarded_tool("read_file_content", ["file:read_uploaded"], "test-agent")
        result = tool.invoke({"filename": "orders.csv", "instruction": "summarize"})
    finally:
        reset_sandbox_context(sandbox_token)
        reset_session_context(session_token, thread_token, identity_token)

    assert result == "sandbox parsed csv"
    assert captured["task"].tool_name == "read_file_content"
    assert captured["task"].runtime == "python"
    assert captured["task"].input_files[0].relative_path == "input/orders.csv"