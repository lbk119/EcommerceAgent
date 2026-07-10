"""Planner 与运行时之间的兼容数据协议。

这些 dataclass 描述主任务、子 Agent assignment、依赖边和 trace 输出。即使 standard/deep 主路径
迁移到 deepagents-native，API、报告、测试和 fallback executor 仍使用这些结构保持稳定契约。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


Profile = Literal["realtime", "standard", "deep"]


@dataclass(frozen=True)
class AgentDependency:
    """两个 Agent assignment 之间的依赖边。"""

    from_assignment: str
    to_assignment: str
    type: str = "optional_context"

    def to_dict(self) -> dict[str, Any]:
        return {
            "from_assignment": self.from_assignment,
            "to_assignment": self.to_assignment,
            "type": self.type,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AgentDependency":
        return cls(
            from_assignment=str(data.get("from_assignment") or ""),
            to_assignment=str(data.get("to_assignment") or ""),
            type=str(data.get("type") or "optional_context"),
        )


@dataclass(frozen=True)
class AgentAssignment:
    """Planner 分派给单个业务 subagent 的任务切片。"""

    assignment_id: str
    agent_id: str
    task: str
    intent: str
    original_query: str = ""
    assignment_scope: str = ""
    objective: str = ""
    expected_contribution: str = ""
    upstream_requirements: list[str] = field(default_factory=list)
    priority: int = 5
    reason: str = "planner_match"
    input_context: dict[str, Any] = field(default_factory=dict)
    constraints: dict[str, Any] = field(default_factory=dict)
    required_output: str = "输出结构化经营分析结论。"
    depends_on: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "assignment_id": self.assignment_id,
            "agent_id": self.agent_id,
            "task": self.task,
            "intent": self.intent,
            "original_query": self.original_query,
            "assignment_scope": self.assignment_scope,
            "objective": self.objective,
            "expected_contribution": self.expected_contribution,
            "upstream_requirements": self.upstream_requirements,
            "priority": self.priority,
            "reason": self.reason,
            "input_context": self.input_context,
            "constraints": self.constraints,
            "required_output": self.required_output,
            "depends_on": self.depends_on,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AgentAssignment":
        return cls(
            assignment_id=str(data.get("assignment_id") or data.get("agent_id") or "assignment"),
            agent_id=str(data.get("agent_id") or ""),
            task=str(data.get("task") or ""),
            intent=str(data.get("intent") or "general_business_chat"),
            original_query=str(data.get("original_query") or data.get("raw_query") or ""),
            assignment_scope=str(data.get("assignment_scope") or ""),
            objective=str(data.get("objective") or ""),
            expected_contribution=str(data.get("expected_contribution") or ""),
            upstream_requirements=[str(item) for item in data.get("upstream_requirements") or []],
            priority=int(data.get("priority") or 5),
            reason=str(data.get("reason") or "planner_match"),
            input_context=dict(data.get("input_context") or {}),
            constraints=dict(data.get("constraints") or {}),
            required_output=str(data.get("required_output") or "输出结构化经营分析结论。"),
            depends_on=[str(item) for item in data.get("depends_on") or []],
        )


@dataclass(frozen=True)
class AgentTaskPlan:
    plan_id: str
    raw_query: str
    primary_intent: str = ""
    profile: Profile = "standard"
    assignments: list[AgentAssignment] = field(default_factory=list)
    dependencies: list[AgentDependency] = field(default_factory=list)
    merge_strategy: str = "business_recommendation"
    requires_clarification: bool = False
    clarification_questions: list[str] = field(default_factory=list)
    boundary: bool = False
    boundary_reason: str = ""
    confidence: float = 0.7
    expected_output: str = "输出结构化经营分析结论。"
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def assignment_intents(self) -> list[str]:
        return [assignment.intent for assignment in sorted(self.assignments, key=lambda item: item.priority)]

    @property
    def primary_task_type(self) -> str:
        if self.boundary and not self.requires_clarification:
            return "boundary"
        if self.requires_clarification:
            return str(self.primary_intent or self.metadata.get("intent") or "clarification")
        if self.primary_intent:
            return self.primary_intent
        if self.assignment_intents:
            return self.assignment_intents[0]
        return "realtime_chat" if self.profile == "realtime" else "general_business_chat"

    @property
    def execution_mode(self) -> str:
        if self.boundary or self.requires_clarification:
            return "boundary"
        if not self.assignments:
            return "realtime_chat" if self.primary_task_type == "realtime_chat" else "boundary"
        if any(assignment.agent_id == "network_search" for assignment in self.assignments) and self.profile != "deep":
            return "boundary"
        if any(assignment.agent_id in {"data_quality", "database_query"} for assignment in self.assignments):
            return "data_agent"
        return "agent_orchestration" if len(self.assignments) > 1 else "business_agent"

    @property
    def missing_context(self) -> list[str]:
        value = self.metadata.get("missing_context")
        return [str(item) for item in value] if isinstance(value, list) else []

    @property
    def fallback_reason(self) -> str:
        return str(self.metadata.get("fallback_reason") or self.boundary_reason or "agent_assignment_planner")

    @property
    def output_format(self) -> str:
        return str(self.metadata.get("output_format") or "markdown")

    @property
    def risk_level(self) -> str:
        return str(self.metadata.get("risk_level") or "low")

    @property
    def business_domain(self) -> str:
        return str(self.metadata.get("business_domain") or "ecommerce_operations")

    @property
    def constraints(self) -> dict[str, Any]:
        return dict(self.metadata.get("constraints") or {})

    @property
    def evaluation_required(self) -> bool:
        return self.profile == "deep" or len(self.assignments) > 1 or self.risk_level in {"medium", "high"}

    def to_lightweight_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "raw_query": self.raw_query,
            "primary_intent": self.primary_intent,
            "assignment_intents": self.assignment_intents,
            "profile": self.profile,
            "execution_mode": self.execution_mode,
            "expected_output": self.expected_output,
            "missing_context": self.missing_context,
            "requires_clarification": self.requires_clarification,
            "clarification_questions": self.clarification_questions,
            "confidence": self.confidence,
            "fallback_reason": self.fallback_reason,
            "output_format": self.output_format,
            "primary_task_type": self.primary_task_type,
            "assignment_count": len(self.assignments),
            "risk_level": self.risk_level,
            "business_domain": self.business_domain,
        }

    def to_trace_metadata(self, *, include_plan: bool = True) -> dict[str, Any]:
        metadata: dict[str, Any] = {
            "plan_id": self.plan_id,
            "intent": self.primary_task_type,
            "primary_goal": self.primary_task_type,
            "execution_mode": self.execution_mode,
            "risk": self.risk_level,
            "requires_evaluation": self.evaluation_required,
            "profile": self.profile,
            "agent_assignment_count": len(self.assignments),
        }
        metadata["task_plan"] = self.to_dict() if include_plan else self.to_lightweight_dict()
        return metadata

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "raw_query": self.raw_query,
            "primary_intent": self.primary_intent,
            "profile": self.profile,
            "assignments": [assignment.to_dict() for assignment in self.assignments],
            "dependencies": [dependency.to_dict() for dependency in self.dependencies],
            "merge_strategy": self.merge_strategy,
            "requires_clarification": self.requires_clarification,
            "clarification_questions": self.clarification_questions,
            "boundary": self.boundary,
            "boundary_reason": self.boundary_reason,
            "confidence": self.confidence,
            "expected_output": self.expected_output,
            "metadata": self.metadata,
            "primary_task_type": self.primary_task_type,
            "assignment_intents": self.assignment_intents,
            "execution_mode": self.execution_mode,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AgentTaskPlan":
        return cls(
            plan_id=str(data.get("plan_id") or ""),
            raw_query=str(data.get("raw_query") or ""),
            primary_intent=str(data.get("primary_intent") or data.get("primary_task_type") or ""),
            profile=str(data.get("profile") or "standard"),
            assignments=[AgentAssignment.from_dict(item) for item in data.get("assignments") or [] if isinstance(item, dict)],
            dependencies=[AgentDependency.from_dict(item) for item in data.get("dependencies") or [] if isinstance(item, dict)],
            merge_strategy=str(data.get("merge_strategy") or "business_recommendation"),
            requires_clarification=bool(data.get("requires_clarification", False)),
            clarification_questions=[str(item) for item in data.get("clarification_questions") or []],
            boundary=bool(data.get("boundary", False)),
            boundary_reason=str(data.get("boundary_reason") or ""),
            confidence=float(data.get("confidence") or 0.7),
            expected_output=str(data.get("expected_output") or "输出结构化经营分析结论。"),
            metadata=dict(data.get("metadata") or {}),
        )
