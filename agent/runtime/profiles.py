"""Agent runtime profile 定义。

realtime / standard / deep 是商业化运行时的三个档位。调用方只传 profile 名，底层统一拿预算、模型和
热路径开关，避免每个入口各自散落 timeout、critic、memory 等判断。
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from agent.runtime.budget import AgentExecutionBudget


PROFILE_ALIASES = {
    # 兼容历史命名，避免 API/脚本里旧 profile 直接失效。
    "lightweight": "realtime",
    "chat": "realtime",
    "fast": "realtime",
    "full": "deep",
    "reasoning": "deep",
}


@dataclass(frozen=True)
class AgentRuntimeProfile:
    """商业化运行时 profile。"""

    name: str
    agent_mode: str
    budget: AgentExecutionBudget
    enable_checkpointer: bool
    enable_critic: bool
    enable_memory_write: bool
    enable_policy_evolution: bool


def normalize_runtime_profile(profile: str | None) -> str:
    """兼容旧 runtime_profile 名称。

    未识别值统一回退 standard，而不是抛错；这样外部 API 参数错误不会误触 deep 重路径。
    """
    value = (profile or "standard").strip().lower()
    return PROFILE_ALIASES.get(value, value if value in {"realtime", "standard", "deep"} else "standard")


def build_execution_budget(profile: str | None) -> AgentExecutionBudget:
    """按 profile 构建单任务预算；环境变量可覆盖默认值。

    三个 profile 的核心差异：
    - realtime：AI Chat 热路径，短时间、少调用、无子 Agent、无记忆写入；
    - standard：普通后台任务，允许少量工具/模型，但禁止网络搜索和长期记忆写入；
    - deep：显式深度任务，允许更长时间、更多子 Agent、记忆写入和策略进化。
    """
    name = normalize_runtime_profile(profile)
    if name == "realtime":
        return AgentExecutionBudget(
            profile="realtime",
            max_wall_time_seconds=_float_env("REALTIME_AGENT_MAX_WALL_TIME_SECONDS", 15),
            max_model_calls=_int_env("REALTIME_AGENT_MAX_MODEL_CALLS", 1),
            max_tool_calls=_int_env("REALTIME_AGENT_MAX_TOOL_CALLS", 3),
            max_subagent_calls=_int_env("REALTIME_AGENT_MAX_SUBAGENT_CALLS", 0),
            max_reflection_retries=_int_env("REALTIME_AGENT_MAX_REFLECTION_RETRIES", 0),
            max_critic_revisions=_int_env("REALTIME_AGENT_MAX_CRITIC_REVISIONS", 0),
            allow_network_search=False,
            allow_memory_write=False,
            allow_policy_evolution=False,
            allow_human_interrupt=False,
            model_profile=os.getenv("AI_CHAT_MODEL_PROFILE", "fast"),
        )
    if name == "deep":
        return AgentExecutionBudget(
            profile="deep",
            max_wall_time_seconds=_float_env("DEEP_AGENT_MAX_WALL_TIME_SECONDS", 180),
            max_model_calls=_int_env("DEEP_AGENT_MAX_MODEL_CALLS", 6),
            max_tool_calls=_int_env("DEEP_AGENT_MAX_TOOL_CALLS", 12),
            max_subagent_calls=_int_env("DEEP_AGENT_MAX_SUBAGENT_CALLS", 3),
            max_reflection_retries=_int_env("DEEP_AGENT_MAX_REFLECTION_RETRIES", 1),
            max_critic_revisions=_int_env("DEEP_AGENT_MAX_CRITIC_REVISIONS", 1),
            allow_network_search=os.getenv("DEEPAGENTS_DEEP_ENABLE_NETWORK_SEARCH", "true").lower() in {"1", "true", "yes", "on"},
            allow_memory_write=True,
            allow_policy_evolution=True,
            allow_human_interrupt=True,
            model_profile=os.getenv("DEEP_AGENT_MODEL_PROFILE", "deep"),
        )
    return AgentExecutionBudget(
        profile="standard",
        max_wall_time_seconds=_float_env("STANDARD_AGENT_MAX_WALL_TIME_SECONDS", 45),
        max_model_calls=_int_env("STANDARD_AGENT_MAX_MODEL_CALLS", 2),
        max_tool_calls=_int_env("STANDARD_AGENT_MAX_TOOL_CALLS", 6),
        max_subagent_calls=_int_env("STANDARD_AGENT_MAX_SUBAGENT_CALLS", 1),
        max_reflection_retries=_int_env("STANDARD_AGENT_MAX_REFLECTION_RETRIES", 0),
        max_critic_revisions=_int_env("STANDARD_AGENT_MAX_CRITIC_REVISIONS", 0),
        allow_network_search=False,
        allow_memory_write=False,
        allow_policy_evolution=False,
        allow_human_interrupt=False,
        model_profile=os.getenv("STANDARD_AGENT_MODEL_PROFILE", "standard"),
    )


def get_runtime_profile(profile: str | None) -> AgentRuntimeProfile:
    """返回完整 profile 配置。

    agent_mode 决定是否构建 DeepAgent：realtime 为 none，standard 为 slim，deep 为 full。
    """
    budget = build_execution_budget(profile)
    return AgentRuntimeProfile(
        name=budget.profile,
        agent_mode="none" if budget.profile == "realtime" else ("full" if budget.profile == "deep" else "slim"),
        budget=budget,
        enable_checkpointer=budget.profile == "deep",
        enable_critic=budget.profile == "deep",
        enable_memory_write=budget.allow_memory_write,
        enable_policy_evolution=budget.allow_policy_evolution,
    )


def _int_env(name: str, default: int) -> int:
    """读取整数环境变量。"""
    return int(os.getenv(name, str(default)))


def _float_env(name: str, default: float) -> float:
    """读取浮点环境变量。"""
    return float(os.getenv(name, str(default)))
