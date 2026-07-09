"""任务执行 profile。

这里保留轻量预算配置，供 realtime/standard/deep 三类入口统一读取超时和可选润色策略。
deepagents-native 的模型、工具和 subagent 调用上限由 agent.runtime.profiles 管理。
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from agent.runtime.profiles import normalize_runtime_profile


@dataclass(frozen=True)
class TaskExecutionProfile:
    """入口级执行预算。

    step_timeout_seconds 保留给受控工具调用；global_timeout_seconds 控制入口总耗时；
    polish_timeout_seconds 控制可选 fast model 润色。
    """

    name: str
    step_timeout_seconds: float
    global_timeout_seconds: float
    polish_timeout_seconds: float
    enable_fast_polish: bool


def get_task_execution_profile(profile: str | None) -> TaskExecutionProfile:
    """返回当前任务的计划执行 profile。

    realtime 面向 AI Chat，只做快速分流和边界/后台任务引导；standard/deep 面向 deepagents-native
    后台业务任务，允许更长时间但仍保留入口级预算。
    """
    normalized = normalize_runtime_profile(profile)
    if normalized == "realtime":
        return TaskExecutionProfile(
            name="realtime",
            step_timeout_seconds=_float_env("REALTIME_PLAN_STEP_TIMEOUT_SECONDS", 2),
            global_timeout_seconds=_float_env("REALTIME_PLAN_GLOBAL_TIMEOUT_SECONDS", 8),
            polish_timeout_seconds=_float_env("REALTIME_PLAN_POLISH_TIMEOUT_SECONDS", 3),
            enable_fast_polish=_bool_env("REALTIME_PLAN_FAST_POLISH", False),
        )
    if normalized == "deep":
        return TaskExecutionProfile(
            name="deep",
            step_timeout_seconds=_float_env("DEEP_PLAN_STEP_TIMEOUT_SECONDS", 3),
            global_timeout_seconds=_float_env("DEEP_PLAN_GLOBAL_TIMEOUT_SECONDS", 60),
            polish_timeout_seconds=_float_env("DEEP_PLAN_POLISH_TIMEOUT_SECONDS", 8),
            enable_fast_polish=_bool_env("DEEP_PLAN_FAST_POLISH", True),
        )
    return TaskExecutionProfile(
        name="standard",
        step_timeout_seconds=_float_env("STANDARD_PLAN_STEP_TIMEOUT_SECONDS", 2),
        global_timeout_seconds=_float_env("STANDARD_PLAN_GLOBAL_TIMEOUT_SECONDS", 30),
        polish_timeout_seconds=_float_env("STANDARD_PLAN_POLISH_TIMEOUT_SECONDS", 6),
        enable_fast_polish=_bool_env("STANDARD_PLAN_FAST_POLISH", False),
    )


def _float_env(name: str, default: float) -> float:
    """读取浮点环境变量。"""
    return float(os.getenv(name, str(default)))


def _bool_env(name: str, default: bool) -> bool:
    """读取布尔环境变量，兼容 1/true/yes/on。"""
    value = os.getenv(name)
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "on"}
