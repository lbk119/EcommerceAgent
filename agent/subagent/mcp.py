"""MCP 工具白名单与 profile gating。

realtime 禁用 MCP；standard 只允许显式配置的只读工具；deep 可以加载更多 MCP 能力。
这里不负责启动 MCP server，只负责把未配置、未授权或高风险工具在 Agent 侧拦住。
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from agent.subagent.config import get_deepagents_profile


@dataclass(frozen=True)
class MCPPolicy:
    """当前 profile 的 MCP 可用状态和工具白名单。"""

    enabled: bool
    allowed_tools: tuple[str, ...]
    status: str
    message: str = ""


def mcp_policy_for_profile(profile: str) -> MCPPolicy:
    """根据 feature flag 和白名单环境变量生成 MCP 策略。"""
    config = get_deepagents_profile(profile)
    if not config.allow_mcp:
        return MCPPolicy(False, (), "disabled", "MCP is disabled for this profile.")
    allowed = tuple(item.strip() for item in os.getenv("DEEPAGENTS_MCP_TOOL_WHITELIST", "").split(",") if item.strip())
    if config.name == "standard":
        allowed = tuple(tool for tool in allowed if tool.startswith("read_") or tool.endswith("_read") or tool.endswith("_search"))
    if not allowed:
        return MCPPolicy(False, (), "not_configured", "No MCP whitelist configured. Set DEEPAGENTS_MCP_TOOL_WHITELIST and MCP server settings.")
    return MCPPolicy(True, allowed, "configured")


def assert_mcp_tool_allowed(tool_name: str, profile: str) -> None:
    """未授权 MCP 工具调用直接拒绝，防止模型绕过 profile 限制。"""
    policy = mcp_policy_for_profile(profile)
    if not policy.enabled or tool_name not in policy.allowed_tools:
        raise PermissionError(f"MCP tool {tool_name} is not allowed for profile {profile}")