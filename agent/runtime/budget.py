"""Agent 执行预算。

预算是 DeepAgent 链路的硬闸门：每次模型、工具、subagent 调用前都必须检查预算，避免一个普通任务
无上限地跑 1-2 分钟。预算对象只在单个任务内使用，不跨任务共享。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


class BudgetExceededError(RuntimeError):
    """任务超过执行预算时抛出；runner 会把已有阶段性结果返回给用户。"""

    def __init__(self, reason: str, snapshot: dict[str, Any]):
        super().__init__(reason)
        self.reason = reason
        self.snapshot = snapshot


@dataclass
class AgentExecutionBudget:
    """单次 Agent 执行预算。

    预算对象是可变计数器：runner 在模型、工具、子 Agent 调用前登记一次；超过阈值就抛出
    BudgetExceededError，由上层把已有信息整理成阶段性结果。
    """

    profile: str
    # 最大端到端运行时间，防止长链路一直占用后台 worker。
    max_wall_time_seconds: float
    # 模型调用次数上限；realtime/standard 默认很低，deep 才允许更多。
    max_model_calls: int
    max_tool_calls: int
    max_subagent_calls: int
    max_reflection_retries: int
    max_critic_revisions: int
    allow_network_search: bool
    allow_memory_write: bool
    allow_policy_evolution: bool
    allow_human_interrupt: bool
    model_profile: str
    started_at: float = field(default_factory=time.perf_counter)
    model_calls: int = 0
    tool_calls: int = 0
    subagent_calls: int = 0

    def elapsed_ms(self) -> float:
        """返回当前任务已运行毫秒数。"""
        return round((time.perf_counter() - self.started_at) * 1000, 2)

    def snapshot(self) -> dict[str, Any]:
        """返回可写入 trace 的预算状态。"""
        return {
            "profile": self.profile,
            "max_wall_time_seconds": self.max_wall_time_seconds,
            "max_model_calls": self.max_model_calls,
            "max_tool_calls": self.max_tool_calls,
            "max_subagent_calls": self.max_subagent_calls,
            "max_reflection_retries": self.max_reflection_retries,
            "max_critic_revisions": self.max_critic_revisions,
            "allow_network_search": self.allow_network_search,
            "allow_memory_write": self.allow_memory_write,
            "allow_policy_evolution": self.allow_policy_evolution,
            "allow_human_interrupt": self.allow_human_interrupt,
            "model_profile": self.model_profile,
            "model_calls": self.model_calls,
            "tool_calls": self.tool_calls,
            "subagent_calls": self.subagent_calls,
            "elapsed_ms": self.elapsed_ms(),
        }

    def check_wall_time(self) -> None:
        """检查 wall time；任何调用前都应先过这里。"""
        elapsed_seconds = time.perf_counter() - self.started_at
        if elapsed_seconds > self.max_wall_time_seconds:
            self._raise(f"Agent 执行超过 {self.max_wall_time_seconds}s 预算")

    def record_model_call(self) -> None:
        """登记一次模型调用。"""
        self.check_wall_time()
        if self.model_calls >= self.max_model_calls:
            self._raise(f"模型调用次数超过预算：{self.max_model_calls}")
        self.model_calls += 1

    def record_tool_call(self, tool_name: str) -> None:
        """登记一次工具调用；network_search 会按预算单独拦截。"""
        self.check_wall_time()
        if tool_name == "internet_search" and not self.allow_network_search:
            self._raise("当前 profile 不允许调用 network_search_agent/internet_search")
        if self.tool_calls >= self.max_tool_calls:
            self._raise(f"工具调用次数超过预算：{self.max_tool_calls}")
        self.tool_calls += 1

    def record_subagent_call(self, subagent_name: str) -> None:
        """登记一次子 Agent 调用。"""
        self.check_wall_time()
        if self.subagent_calls >= self.max_subagent_calls:
            self._raise(f"子 Agent 调用次数超过预算：{self.max_subagent_calls}")
        self.subagent_calls += 1

    def _raise(self, reason: str) -> None:
        """统一抛出预算异常，并携带当前预算快照供 trace/诊断使用。"""
        raise BudgetExceededError(reason, self.snapshot())
