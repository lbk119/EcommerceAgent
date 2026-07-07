"""PlannerAgent 的结构化计划模型。

PlannerAgent 只负责理解需求和生成 DAG，不执行工具。这里的 dataclass 都提供 to_dict/from_dict，
便于 API、trace、runtime 和测试脚本使用同一份 JSON 结构。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class TaskIntent:
    """用户原始需求的标准化意图。"""

    raw_query: str
    normalized_query: str
    business_domain: str = "ecommerce_operations"
    primary_goal: str = "general_business_chat"
    constraints: dict[str, Any] = field(default_factory=dict)
    output_format: str = "markdown"
    urgency: str = "normal"
    risk_level: str = "low"
    confidence: float = 0.5

    def to_dict(self) -> dict[str, Any]:
        return {
            "raw_query": self.raw_query,
            "normalized_query": self.normalized_query,
            "business_domain": self.business_domain,
            "primary_goal": self.primary_goal,
            "constraints": self.constraints,
            "output_format": self.output_format,
            "urgency": self.urgency,
            "risk_level": self.risk_level,
            "confidence": self.confidence,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TaskIntent":
        return cls(
            raw_query=str(data.get("raw_query") or ""),
            normalized_query=str(data.get("normalized_query") or data.get("raw_query") or ""),
            business_domain=str(data.get("business_domain") or "ecommerce_operations"),
            primary_goal=str(data.get("primary_goal") or "general_business_chat"),
            constraints=dict(data.get("constraints") or {}),
            output_format=str(data.get("output_format") or "markdown"),
            urgency=str(data.get("urgency") or "normal"),
            risk_level=str(data.get("risk_level") or "low"),
            confidence=float(data.get("confidence") or 0.5),
        )


@dataclass(frozen=True)
class PlanEdge:
    """DAG 中两个 step 的依赖边。"""

    from_step: str
    to_step: str
    type: str = "data_dependency"

    def to_dict(self) -> dict[str, Any]:
        return {"from_step": self.from_step, "to_step": self.to_step, "type": self.type}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PlanEdge":
        return cls(from_step=str(data.get("from_step") or ""), to_step=str(data.get("to_step") or ""), type=str(data.get("type") or "data_dependency"))


@dataclass(frozen=True)
class ExpertRoute:
    """Planner 对 expert/runtime/tool 的路由声明。"""

    expert_id: str
    expert_name: str
    capability: str
    runtime: str
    allowed_tools: list[str] = field(default_factory=list)
    model_profile: str = "fast"

    def to_dict(self) -> dict[str, Any]:
        return {
            "expert_id": self.expert_id,
            "expert_name": self.expert_name,
            "capability": self.capability,
            "runtime": self.runtime,
            "allowed_tools": self.allowed_tools,
            "model_profile": self.model_profile,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ExpertRoute":
        return cls(
            expert_id=str(data.get("expert_id") or "general_deep_agent"),
            expert_name=str(data.get("expert_name") or "通用深度 Agent"),
            capability=str(data.get("capability") or "general_business_chat"),
            runtime=str(data.get("runtime") or "deepagent"),
            allowed_tools=[str(item) for item in data.get("allowed_tools") or []],
            model_profile=str(data.get("model_profile") or "fast"),
        )


@dataclass(frozen=True)
class PlanStep:
    """PlannerAgent 输出的原子执行步骤。"""

    step_id: str
    name: str
    description: str
    task_type: str
    expert: str
    tool_group: str = "business_metrics"
    can_parallel: bool = True
    depends_on: list[str] = field(default_factory=list)
    timeout_seconds: float | None = None
    critical: bool = False
    input_schema: dict[str, Any] = field(default_factory=dict)
    output_schema: dict[str, Any] = field(default_factory=dict)
    success_criteria: list[str] = field(default_factory=list)
    fallback_strategy: str = "degrade"

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "name": self.name,
            "description": self.description,
            "task_type": self.task_type,
            "expert": self.expert,
            "tool_group": self.tool_group,
            "can_parallel": self.can_parallel,
            "depends_on": self.depends_on,
            "timeout_seconds": self.timeout_seconds,
            "critical": self.critical,
            "input_schema": self.input_schema,
            "output_schema": self.output_schema,
            "success_criteria": self.success_criteria,
            "fallback_strategy": self.fallback_strategy,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PlanStep":
        return cls(
            step_id=str(data.get("step_id") or data.get("name") or "step"),
            name=str(data.get("name") or data.get("step_id") or "step"),
            description=str(data.get("description") or data.get("name") or ""),
            task_type=str(data.get("task_type") or "general_business_chat"),
            expert=str(data.get("expert") or "data_expert"),
            tool_group=str(data.get("tool_group") or "business_metrics"),
            can_parallel=bool(data.get("can_parallel", True)),
            depends_on=[str(item) for item in data.get("depends_on") or []],
            timeout_seconds=float(data["timeout_seconds"]) if data.get("timeout_seconds") is not None else None,
            critical=bool(data.get("critical", False)),
            input_schema=dict(data.get("input_schema") or {}),
            output_schema=dict(data.get("output_schema") or {}),
            success_criteria=[str(item) for item in data.get("success_criteria") or []],
            fallback_strategy=str(data.get("fallback_strategy") or "degrade"),
        )


@dataclass(frozen=True)
class TaskPlan:
    """PlannerAgent 的完整输出。"""

    plan_id: str
    raw_query: str
    intent: TaskIntent
    profile: str = "standard"
    execution_mode: str = "deterministic_dag"
    steps: list[PlanStep] = field(default_factory=list)
    dependencies: list[PlanEdge] = field(default_factory=list)
    expected_output: str = "输出结构化经营分析结论。"
    missing_context: list[str] = field(default_factory=list)
    requires_clarification: bool = False
    clarification_questions: list[str] = field(default_factory=list)
    confidence: float = 0.5
    fallback_reason: str = ""
    expert_routes: list[ExpertRoute] = field(default_factory=list)
    output_format: str = "markdown"

    @property
    def primary_task_type(self) -> str:
        """返回 runtime、trace 和持久化使用的主业务意图。"""
        if self.execution_mode == "boundary" and not self.requires_clarification:
            return "boundary"
        if self.steps:
            return self.steps[0].task_type
        return self.intent.primary_goal or "general_business_chat"

    @property
    def critic_required(self) -> bool:
        """根据计划风险和执行模式判断是否需要 Critic。"""
        return self.profile == "deep" or self.intent.risk_level in {"medium", "high"} or self.execution_mode in {"deterministic_dag", "hybrid_plan"}

    def to_lightweight_dict(self) -> dict[str, Any]:
        """返回 HTTP 受理阶段可安全透传的轻量计划摘要。"""
        return {
            "plan_id": self.plan_id,
            "raw_query": self.raw_query,
            "intent": self.intent.to_dict(),
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
            "step_count": len(self.steps),
        }

    def to_trace_metadata(self, *, include_plan: bool = True) -> dict[str, Any]:
        """返回 trace/UI 可直接消费的计划元数据。"""
        metadata: dict[str, Any] = {
            "plan_id": self.plan_id,
            "intent": self.primary_task_type,
            "primary_goal": self.intent.primary_goal,
            "execution_mode": self.execution_mode,
            "risk": self.intent.risk_level,
            "requires_critic": self.critic_required,
            "profile": self.profile,
            "plan_steps_count": len(self.steps),
        }
        metadata["task_plan"] = self.to_dict() if include_plan else self.to_lightweight_dict()
        return metadata

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "raw_query": self.raw_query,
            "intent": self.intent.to_dict(),
            "profile": self.profile,
            "execution_mode": self.execution_mode,
            "steps": [step.to_dict() for step in self.steps],
            "dependencies": [edge.to_dict() for edge in self.dependencies],
            "expected_output": self.expected_output,
            "missing_context": self.missing_context,
            "requires_clarification": self.requires_clarification,
            "clarification_questions": self.clarification_questions,
            "confidence": self.confidence,
            "fallback_reason": self.fallback_reason,
            "expert_routes": [route.to_dict() for route in self.expert_routes],
            "output_format": self.output_format,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TaskPlan":
        intent_data = data.get("intent") if isinstance(data.get("intent"), dict) else {}
        return cls(
            plan_id=str(data.get("plan_id") or ""),
            raw_query=str(data.get("raw_query") or intent_data.get("raw_query") or ""),
            intent=TaskIntent.from_dict(intent_data),
            profile=str(data.get("profile") or "standard"),
            execution_mode=str(data.get("execution_mode") or "deterministic_dag"),
            steps=[PlanStep.from_dict(item) for item in data.get("steps") or [] if isinstance(item, dict)],
            dependencies=[PlanEdge.from_dict(item) for item in data.get("dependencies") or [] if isinstance(item, dict)],
            expected_output=str(data.get("expected_output") or "输出结构化经营分析结论。"),
            missing_context=[str(item) for item in data.get("missing_context") or []],
            requires_clarification=bool(data.get("requires_clarification", False)),
            clarification_questions=[str(item) for item in data.get("clarification_questions") or []],
            confidence=float(data.get("confidence") or 0.5),
            fallback_reason=str(data.get("fallback_reason") or ""),
            expert_routes=[ExpertRoute.from_dict(item) for item in data.get("expert_routes") or [] if isinstance(item, dict)],
            output_format=str(data.get("output_format") or "markdown"),
        )
