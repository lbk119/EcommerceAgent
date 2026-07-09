"""deepagents-native profile 配置。

这里把 realtime、standard、deep 三档运行配置集中定义，包括可用 subagents、预算、MCP、filesystem、
HITL、memory 和 guard 开关。profile 是运行策略，不是业务 Agent；业务能力只通过 subagent registry 注册。
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from agent.runtime.profiles import normalize_runtime_profile


TRUE_VALUES = {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class DeepAgentsProfileConfig:
    """deepagents-native 单个 profile 的完整运行配置。"""

    name: str
    enabled: bool
    realtime_chat_enabled: bool
    subagents: tuple[str, ...]
    max_runtime_seconds: float
    max_model_calls: int
    max_tool_calls: int
    max_subagent_calls: int
    same_tool_same_args_limit: int
    same_subagent_limit: int
    recursion_limit: int
    allow_business_tools: bool
    allow_network_search: bool
    allow_database_query: bool
    allow_mcp: bool
    allow_filesystem: bool
    allow_shell: bool
    allow_memory_write: bool
    enable_hitl: bool
    enable_guard: bool


def deepagents_enabled(profile: str | None = None) -> bool:
    """判断指定 profile 的 deepagents-native 主路径是否启用。"""
    normalized = normalize_runtime_profile(profile)
    if not flag("ENABLE_DEEPAGENTS_RUNTIME", True):
        return False
    if normalized == "realtime":
        return flag("ENABLE_DEEPAGENTS_REALTIME_CHAT", True)
    if normalized == "deep":
        return flag("ENABLE_DEEPAGENTS_DEEP", True)
    return flag("ENABLE_DEEPAGENTS_STANDARD", True)


def get_deepagents_profile(profile: str | None) -> DeepAgentsProfileConfig:
    """按 profile 名称返回预算、能力和安全开关的不可变配置。"""
    name = normalize_runtime_profile(profile)
    if name == "realtime":
        return DeepAgentsProfileConfig(
            name="realtime",
            enabled=deepagents_enabled("realtime"),
            realtime_chat_enabled=flag("ENABLE_DEEPAGENTS_REALTIME_CHAT", True),
            subagents=(),
            max_runtime_seconds=float_env("DEEPAGENTS_REALTIME_MAX_RUNTIME_SECONDS", 5),
            max_model_calls=int_env("DEEPAGENTS_REALTIME_MAX_MODEL_CALLS", 2),
            max_tool_calls=0,
            max_subagent_calls=0,
            same_tool_same_args_limit=1,
            same_subagent_limit=0,
            recursion_limit=int_env("DEEPAGENTS_REALTIME_RECURSION_LIMIT", 8),
            allow_business_tools=False,
            allow_network_search=False,
            allow_database_query=False,
            allow_mcp=False,
            allow_filesystem=False,
            allow_shell=False,
            allow_memory_write=False,
            enable_hitl=False,
            enable_guard=flag("ENABLE_AGENT_SAFETY_MIDDLEWARE", True),
        )
    if name == "deep":
        return DeepAgentsProfileConfig(
            name="deep",
            enabled=deepagents_enabled("deep"),
            realtime_chat_enabled=False,
            subagents=("product_analysis", "inventory", "campaign", "report", "data_quality", "knowledge_base", "network_search", "database_query"),
            max_runtime_seconds=float_env("DEEPAGENTS_DEEP_MAX_RUNTIME_SECONDS", 900),
            max_model_calls=int_env("DEEPAGENTS_DEEP_MAX_MODEL_CALLS", 40),
            max_tool_calls=int_env("DEEPAGENTS_DEEP_MAX_TOOL_CALLS", 80),
            max_subagent_calls=int_env("DEEPAGENTS_DEEP_MAX_SUBAGENT_CALLS", 30),
            same_tool_same_args_limit=int_env("DEEPAGENTS_DEEP_SAME_TOOL_ARGS_LIMIT", 3),
            same_subagent_limit=int_env("DEEPAGENTS_DEEP_SAME_SUBAGENT_LIMIT", 3),
            recursion_limit=int_env("DEEPAGENTS_DEEP_RECURSION_LIMIT", 80),
            allow_business_tools=True,
            allow_network_search=flag("DEEPAGENTS_DEEP_ENABLE_NETWORK_SEARCH", True),
            allow_database_query=True,
            allow_mcp=flag("ENABLE_DEEPAGENTS_MCP", False),
            allow_filesystem=flag("ENABLE_DEEPAGENTS_FILESYSTEM", True),
            allow_shell=flag("ENABLE_DEEPAGENTS_SHELL", False),
            allow_memory_write=flag("ENABLE_DEEPAGENTS_MEMORY", True),
            enable_hitl=flag("ENABLE_DEEPAGENTS_HITL", True),
            enable_guard=flag("ENABLE_AGENT_SAFETY_MIDDLEWARE", True),
        )
    return DeepAgentsProfileConfig(
        name="standard",
        enabled=deepagents_enabled("standard"),
        realtime_chat_enabled=False,
        subagents=("product_analysis", "inventory", "campaign", "report", "data_quality", "knowledge_base", "database_query"),
        max_runtime_seconds=float_env("DEEPAGENTS_STANDARD_MAX_RUNTIME_SECONDS", 60),
        max_model_calls=int_env("DEEPAGENTS_STANDARD_MAX_MODEL_CALLS", 8),
        max_tool_calls=int_env("DEEPAGENTS_STANDARD_MAX_TOOL_CALLS", 15),
        max_subagent_calls=int_env("DEEPAGENTS_STANDARD_MAX_SUBAGENT_CALLS", 6),
        same_tool_same_args_limit=int_env("DEEPAGENTS_STANDARD_SAME_TOOL_ARGS_LIMIT", 2),
        same_subagent_limit=int_env("DEEPAGENTS_STANDARD_SAME_SUBAGENT_LIMIT", 3),
        recursion_limit=int_env("DEEPAGENTS_STANDARD_RECURSION_LIMIT", 30),
        allow_business_tools=True,
        allow_network_search=flag("DEEPAGENTS_STANDARD_ENABLE_NETWORK_SEARCH", False),
        allow_database_query=True,
        allow_mcp=flag("ENABLE_DEEPAGENTS_MCP", False) and flag("DEEPAGENTS_STANDARD_ENABLE_MCP", False),
        allow_filesystem=flag("ENABLE_DEEPAGENTS_FILESYSTEM", True),
        allow_shell=False,
        allow_memory_write=flag("ENABLE_DEEPAGENTS_MEMORY", True),
        enable_hitl=flag("ENABLE_DEEPAGENTS_HITL", True),
        enable_guard=flag("ENABLE_AGENT_SAFETY_MIDDLEWARE", True),
    )


def flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in TRUE_VALUES


def int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def float_env(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default
