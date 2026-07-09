"""工具权限检查。

所有挂给 deepagents 或 fallback executor 的工具都应在真正执行前调用这里，按权限点、风险等级、
运行时身份和 feature flag 判断是否允许执行。权限拒绝会写 trace，便于安全审计。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Iterable, List

from agent.runtime.runtime_context import current_runtime_context
from agent.trace.tracer import tracer


class PermissionDenied(RuntimeError):
    """工具调用被运行时权限策略拒绝时抛出。"""


@dataclass(frozen=True)
class PermissionDecision:
    """一次工具权限判断的完整审计结果。"""

    allowed: bool
    tool_name: str
    actor: str
    required_permissions: List[str] = field(default_factory=list)
    granted_permissions: List[str] = field(default_factory=list)
    missing_permissions: List[str] = field(default_factory=list)
    risk: str = "low"
    reason: str = ""


def permission_enforcement_enabled() -> bool:
    return os.getenv("TOOL_PERMISSION_ENFORCEMENT", "true").lower() == "true"


def decide_tool_permission(
    tool_name: str,
    required_permissions: Iterable[str],
    granted_permissions: Iterable[str],
    risk: str = "low",
    actor: str = "unknown_agent",
) -> PermissionDecision:
    """只计算工具是否允许执行，不产生副作用。"""
    required = list(required_permissions or [])
    granted = list(granted_permissions or [])
    missing = sorted(set(required) - set(granted))
    if not permission_enforcement_enabled():
        return PermissionDecision(True, tool_name, actor, required, granted, [], risk, "permission enforcement disabled")
    if missing:
        return PermissionDecision(False, tool_name, actor, required, granted, missing, risk, "missing required permissions")
    return PermissionDecision(True, tool_name, actor, required, granted, [], risk, "allowed")


def assert_tool_allowed(
    tool_name: str,
    required_permissions: Iterable[str],
    granted_permissions: Iterable[str],
    risk: str = "low",
    actor: str = "unknown_agent",
) -> PermissionDecision:
    """Check tool permissions, emit telemetry, and raise on denial."""
    decision = decide_tool_permission(tool_name, required_permissions, granted_permissions, risk, actor)
    context = current_runtime_context()
    tracer.emit(
        "tool_permission_checked",
        trace_id=context.trace_id,
        task_id=context.task_id,
        conversation_id=context.conversation_id,
        agent_name=actor,
        metadata={
            "tool_name": tool_name,
            "allowed": decision.allowed,
            "risk": decision.risk,
            "required_permissions": decision.required_permissions,
            "granted_permissions": decision.granted_permissions,
            "missing_permissions": decision.missing_permissions,
            "reason": decision.reason,
        },
    )
    if not decision.allowed:
        raise PermissionDenied(
            f"Tool {tool_name} permission denied: missing {', '.join(decision.missing_permissions)} for actor {actor}"
        )
    return decision
