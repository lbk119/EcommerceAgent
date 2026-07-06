"""计划式任务执行 profile。

这里的 profile 只服务“计划一次、并行执行、统一汇总”的执行架构，和 DeepAgent 的预算 profile
互相配合但职责不同：DeepAgent budget 管模型/工具/subagent 调用上限；这里管固定 DAG 的每个 step
超时、整体超时和 fast model 润色超时。把这些参数集中起来，可以避免不同入口各自散落 magic number。
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from agent.runtime.profiles import normalize_runtime_profile


@dataclass(frozen=True)
class TaskExecutionProfile:
    """固定 DAG 执行 profile。"""

    name: str
    step_timeout_seconds: float
    global_timeout_seconds: float
    polish_timeout_seconds: float
    enable_fast_polish: bool


def get_task_execution_profile(profile: str | None) -> TaskExecutionProfile:
    """返回当前任务的计划执行 profile。

    realtime 面向 AI Chat，需要 8 秒内完成；standard 面向普通数字员工，允许 30 秒但每个数据节点仍默认
    2 秒，避免单个 SQL 或外部工具拖垮用户体验。deep profile 仍优先使用固定 DAG 覆盖常见任务，但可以
    给更多整体时间；真正未知任务才 fallback DeepAgent。
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
    return float(os.getenv(name, str(default)))


def _bool_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "on"}