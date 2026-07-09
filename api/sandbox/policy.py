"""Authoritative sandbox policy engine."""

from __future__ import annotations

import os
from dataclasses import dataclass

from agent.sandbox.models import SandboxDecision, SandboxResourceLimits, SandboxTask
from agent.tools.registry import tool_registry
from api.sandbox.network import validate_network_policy


SECRET_ENV_NAMES = {
    "OPENAI_API_KEY",
    "MYSQL_PASSWORD",
    "GATEWAY_JWT_SECRET",
    "RAGFLOW_API_KEY",
    "TAVILY_API_KEY",
    "SANDBOX_SERVER_INTERNAL_TOKEN",
    "REDIS_URL",
    "NATS_URL",
}


@dataclass(frozen=True)
class SandboxProfileDefaults:
    timeout_seconds: int
    memory_mb: int
    cpu_count: float


class SandboxPolicyEngine:
    """Validates every SandboxTask before Docker is touched."""

    def __init__(self):
        self.defaults = {
            "standard": SandboxProfileDefaults(
                timeout_seconds=int(os.getenv("SANDBOX_DEFAULT_TIMEOUT_SECONDS", "30")),
                memory_mb=int(os.getenv("SANDBOX_DEFAULT_MEMORY_MB", "512")),
                cpu_count=float(os.getenv("SANDBOX_DEFAULT_CPU_COUNT", "1")),
            ),
            "deep": SandboxProfileDefaults(
                timeout_seconds=int(os.getenv("SANDBOX_MAX_TIMEOUT_SECONDS", "120")),
                memory_mb=int(os.getenv("SANDBOX_DEEP_MEMORY_MB", "1024")),
                cpu_count=float(os.getenv("SANDBOX_DEEP_CPU_COUNT", "2")),
            ),
        }
        self.max_timeout_seconds = int(os.getenv("SANDBOX_MAX_TIMEOUT_SECONDS", "120"))

    def decide(self, task: SandboxTask) -> SandboxDecision:
        if os.getenv("ENABLE_DOCKER_SANDBOX", "true").lower() not in {"1", "true", "yes", "on"}:
            return SandboxDecision(allowed=False, reason="docker sandbox is disabled", risk_level="high")
        if task.profile == "realtime":
            return SandboxDecision(allowed=False, reason="realtime profile cannot execute sandbox tasks", risk_level="high")
        if task.tool_name not in tool_registry._specs:
            return SandboxDecision(allowed=False, reason=f"unknown sandbox tool: {task.tool_name}", risk_level="high")
        spec = tool_registry._specs.get(task.tool_name)
        if spec is not None:
            if task.profile not in spec.allowed_profiles:
                return SandboxDecision(allowed=False, reason=f"tool {task.tool_name} is not allowed in {task.profile}", risk_level="high")
            if not spec.sandbox_required and spec.execution_mode != "sandbox":
                return SandboxDecision(allowed=False, reason=f"tool {task.tool_name} is not registered for sandbox execution", risk_level="high")
            if spec.sandbox_runtime and task.runtime != spec.sandbox_runtime:
                return SandboxDecision(allowed=False, reason=f"tool {task.tool_name} requires {spec.sandbox_runtime} runtime", risk_level="high")
        if task.runtime == "shell" and os.getenv("SANDBOX_ENABLE_SHELL", os.getenv("ENABLE_SANDBOX_SHELL", "false")).lower() not in {"1", "true", "yes", "on"}:
            return SandboxDecision(allowed=False, reason="shell sandbox runtime is disabled", risk_level="high", required_approval=True)
        if task.profile == "standard" and task.runtime not in {"python", "file"}:
            return SandboxDecision(allowed=False, reason="standard profile only allows python/file sandbox tasks", risk_level="high")
        if task.profile == "deep" and task.runtime not in {"python", "node", "file", "shell"}:
            return SandboxDecision(allowed=False, reason="runtime is not allowed in deep profile", risk_level="high")
        if SECRET_ENV_NAMES & {key.upper() for key in task.env}:
            return SandboxDecision(allowed=False, reason="secret environment variables cannot enter sandbox", risk_level="high")
        if task.network_policy.mode != "none":
            ok, reason = validate_network_policy(task.network_policy)
            if not ok:
                return SandboxDecision(allowed=False, reason=reason, risk_level="high")
            network_decision = self._network_decision(task)
            if not network_decision.allowed:
                return network_decision
        if task.timeout_seconds > self.max_timeout_seconds:
            return SandboxDecision(allowed=False, reason="sandbox timeout exceeds maximum", risk_level="high")
        return SandboxDecision(allowed=True, reason="sandbox policy accepted", risk_level=str(getattr(spec, "risk", "medium") if spec else "medium"))

    def normalized_limits(self, task: SandboxTask) -> SandboxResourceLimits:
        defaults = self.defaults.get(task.profile, self.defaults["standard"])
        return SandboxResourceLimits(
            cpu_count=min(float(task.resource_limits.cpu_count or defaults.cpu_count), defaults.cpu_count),
            memory_mb=min(int(task.resource_limits.memory_mb or defaults.memory_mb), defaults.memory_mb),
            pids_limit=min(int(task.resource_limits.pids_limit or os.getenv("SANDBOX_PIDS_LIMIT", "128")), int(os.getenv("SANDBOX_PIDS_LIMIT", "128"))),
            disk_mb=task.resource_limits.disk_mb,
            timeout_seconds=min(int(task.timeout_seconds or defaults.timeout_seconds), defaults.timeout_seconds, self.max_timeout_seconds),
        )

    def _network_decision(self, task: SandboxTask) -> SandboxDecision:
        if os.getenv("SANDBOX_ENABLE_NETWORK", "false").lower() not in {"1", "true", "yes", "on"}:
            return SandboxDecision(allowed=False, reason="sandbox network is globally disabled", risk_level="high")
        if task.profile != "deep":
            return SandboxDecision(allowed=False, reason="network sandbox tasks require deep profile", risk_level="high")
        if os.getenv("SANDBOX_DEEP_ENABLE_NETWORK", "false").lower() not in {"1", "true", "yes", "on"}:
            return SandboxDecision(allowed=False, reason="deep sandbox network is disabled", risk_level="high")
        configured = {item.strip().lower() for item in os.getenv("SANDBOX_ALLOWED_DOMAINS", "").split(",") if item.strip()}
        if configured and not set(task.network_policy.allowed_domains).issubset(configured):
            return SandboxDecision(allowed=False, reason="requested domains are not in SANDBOX_ALLOWED_DOMAINS", risk_level="high")
        if not task.network_policy.allowed_domains:
            return SandboxDecision(allowed=False, reason="allowlist network requires domains", risk_level="high")
        return SandboxDecision(allowed=True, reason="network policy accepted", risk_level="medium")