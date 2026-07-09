"""Agent-side helper policy for early sandbox rejection.

This is intentionally advisory. The API sandbox server is the authority and runs
the full policy engine before any container is created.
"""

from agent.sandbox.models import SandboxDecision, SandboxTask


class AgentSandboxPolicy:
    """Small client-side guard used before posting to the sandbox server."""

    def decide(self, task: SandboxTask) -> SandboxDecision:
        if task.profile == "realtime":
            return SandboxDecision(allowed=False, reason="realtime profile cannot execute sandbox tasks", risk_level="high")
        if task.runtime == "shell":
            return SandboxDecision(allowed=False, reason="shell runtime requires server-side explicit enablement", risk_level="high", required_approval=True)
        return SandboxDecision(allowed=True, reason="client precheck passed", risk_level="medium" if task.network_policy.mode != "none" else "low")