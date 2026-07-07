"""
工具权限治理。

ToolRegistry 负责声明“工具需要什么权限”，AgentSpec 负责声明“Agent 拥有什么权限”。
这个模块把两者在运行时真正接起来：工具执行前检查权限，不满足则拒绝执行并写入 trace。

当前策略故意保持简单：
- 默认开启 TOOL_PERMISSION_ENFORCEMENT；
- 工具要求的 permissions 必须全部包含在 granted_permissions 中；
- risk / requires_human_approval 先进入 trace，后续可以扩展成更严格策略。
"""

import os
from dataclasses import dataclass, field
from typing import Iterable, List

from agent.core.runtime_context import current_runtime_context
from agent.observability.tracer import tracer


class PermissionDenied(RuntimeError):
    """工具权限不足时抛出的运行时异常。"""


@dataclass(frozen=True)
class PermissionDecision:
    """一次工具权限检查的结构化结果，便于测试、trace 和后续策略扩展。"""

    allowed: bool
    tool_name: str
    actor: str
    required_permissions: List[str] = field(default_factory=list)
    granted_permissions: List[str] = field(default_factory=list)
    missing_permissions: List[str] = field(default_factory=list)
    risk: str = "low"
    reason: str = ""


def permission_enforcement_enabled() -> bool:
    """是否启用权限拦截。默认启用，本地调试可临时设为 false。"""
    return os.getenv("TOOL_PERMISSION_ENFORCEMENT", "true").lower() == "true"


def decide_tool_permission(
    tool_name: str,
    required_permissions: Iterable[str],
    granted_permissions: Iterable[str],
    risk: str = "low",
    actor: str = "unknown_agent",
) -> PermissionDecision:
    """计算工具调用是否被允许，不产生副作用。

    这个函数只返回决策结果，不写 trace、不抛异常，便于单元测试和未来“预检工具调用”。
    真正的拦截逻辑在 assert_tool_allowed 中完成。
    """
    required = list(required_permissions or [])
    granted = list(granted_permissions or [])
    # 当前策略要求工具声明的权限全部满足；后续可在这里扩展 role/risk/approval 等更细规则。
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
    """
    执行权限检查并写入观测事件。

    这个函数会在 guarded tool 真正调用原始工具前执行，是当前运行时治理的核心拦截点。
    """
    decision = decide_tool_permission(tool_name, required_permissions, granted_permissions, risk, actor)
    context = current_runtime_context()
    # 不论允许还是拒绝都写 trace，方便排查“为什么某个工具没有执行”。
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
        # 直接抛出 PermissionDenied，让 LangChain/AgentRuntime 按普通工具异常处理并进入 trace。
        raise PermissionDenied(
            f"工具 {tool_name} 权限不足：缺少 {', '.join(decision.missing_permissions)}，调用方 {actor}"
        )
    return decision