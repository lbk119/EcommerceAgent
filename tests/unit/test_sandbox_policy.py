from __future__ import annotations

from agent.sandbox.models import SandboxNetworkPolicy, SandboxTask
from api.sandbox.policy import SandboxPolicyEngine


def make_task(**overrides):
    data = {
        "task_id": "task-1",
        "conversation_id": "conv-1",
        "tenant_id": "tenant-1",
        "user_id": "user-1",
        "shop_id": "shop-1",
        "profile": "standard",
        "agent_id": "agent-1",
        "tool_name": "read_file_content",
        "runtime": "python",
        "code": "print('ok')",
    }
    data.update(overrides)
    return SandboxTask(**data)


def test_realtime_rejects_all(monkeypatch):
    monkeypatch.setenv("ENABLE_DOCKER_SANDBOX", "true")
    decision = SandboxPolicyEngine().decide(make_task(profile="realtime"))
    assert not decision.allowed


def test_standard_allows_python_file_task(monkeypatch):
    monkeypatch.setenv("ENABLE_DOCKER_SANDBOX", "true")
    decision = SandboxPolicyEngine().decide(make_task(profile="standard", runtime="python"))
    assert decision.allowed


def test_standard_rejects_network(monkeypatch):
    monkeypatch.setenv("ENABLE_DOCKER_SANDBOX", "true")
    monkeypatch.setenv("SANDBOX_ENABLE_NETWORK", "true")
    decision = SandboxPolicyEngine().decide(make_task(network_policy=SandboxNetworkPolicy(mode="allowlist", allowed_domains=["example.com"])))
    assert not decision.allowed
    assert "deep" in decision.reason


def test_deep_allows_network_allowlist_when_enabled(monkeypatch):
    monkeypatch.setenv("ENABLE_DOCKER_SANDBOX", "true")
    monkeypatch.setenv("SANDBOX_ENABLE_NETWORK", "true")
    monkeypatch.setenv("SANDBOX_DEEP_ENABLE_NETWORK", "true")
    monkeypatch.setenv("SANDBOX_ALLOWED_DOMAINS", "example.com")

    decision = SandboxPolicyEngine().decide(make_task(profile="deep", network_policy=SandboxNetworkPolicy(mode="allowlist", allowed_domains=["example.com"])))

    assert decision.allowed


def test_shell_default_rejected(monkeypatch):
    monkeypatch.setenv("ENABLE_DOCKER_SANDBOX", "true")
    monkeypatch.delenv("SANDBOX_ENABLE_SHELL", raising=False)
    decision = SandboxPolicyEngine().decide(make_task(profile="deep", tool_name="sandbox_shell", runtime="shell", command=["sh", "-c", "echo no"], code=None))
    assert not decision.allowed


def test_unknown_tool_rejected(monkeypatch):
    monkeypatch.setenv("ENABLE_DOCKER_SANDBOX", "true")
    decision = SandboxPolicyEngine().decide(make_task(tool_name="unknown_tool"))
    assert not decision.allowed
    assert "unknown" in decision.reason