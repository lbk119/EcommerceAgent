"""
业务 Workflow 路由与执行器。

这里不是通用 Planner，而是把 task_classifier 已经识别出的电商任务接到固定业务 DAG。这样高价值、
结构稳定的任务先走确定性流程，普通闲聊或未覆盖任务仍回落 DeepAgent。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

from agent.observability.tracer import tracer
from agent.planning.task_classifier import TaskClassification
from agent.runtime.execution_result import ExecutionResult


DeepAgentExecutor = Callable[[str], Awaitable[str]]


@dataclass(frozen=True)
class WorkflowRoute:
    """WorkflowRouter 的路由结果。"""

    workflow_name: str
    reason: str


class WorkflowRouter:
    """
    根据任务分类选择固定业务 workflow。

    路由只看 classification，不重新理解用户意图。这样分类层和执行层职责清晰，后续如果分类器从
    规则升级到模型，WorkflowRouter 不需要跟着改。
    """

    _TASK_TO_WORKFLOW = {
        "seasonal_selection": "seasonal_selection",
        "daily_report": "daily_report",
        "inventory_analysis": "inventory_warning",
        "campaign_review": "campaign_review",
        "hot_product_analysis": "hot_product_analysis",
        "product_optimization": "product_optimization",
    }

    def route(self, classification: TaskClassification) -> Optional[WorkflowRoute]:
        """返回可执行 workflow；没有覆盖的 deterministic_dag 会交给 DeepAgent 兜底。"""
        if classification.preferred_workflow != "deterministic_dag":
            return None
        workflow_name = self._TASK_TO_WORKFLOW.get(classification.task_type)
        if not workflow_name:
            return None
        return WorkflowRoute(workflow_name=workflow_name, reason=f"task_type:{classification.task_type}")


class WorkflowRunner:
    """
    固定业务 DAG 执行器。

    当前版本把日报、库存和活动复盘的指标查询固定为确定性函数；LLM 只负责表达层综合。Critic 仍由
    AgentRuntime 后续阶段统一运行，避免 workflow 内部和外部重复校验。
    """

    def __init__(self):
        self.router = WorkflowRouter()

    async def run_or_fallback(
        self,
        *,
        query: str,
        classification: TaskClassification,
        trace_id: str,
        task_id: str,
        conversation_id: str,
        fallback: DeepAgentExecutor,
        allow_deepagent_fallback: bool = True,
        message_id: str = "",
        runtime_profile: str = "standard",
    ) -> ExecutionResult:
        """
        先尝试 deterministic workflow，未覆盖或核心节点失败时回落 DeepAgent。

        这里的 fallback 是一个函数而不是直接 import DeepAgent runner，是为了保持 WorkflowRunner
        与具体 Agent 框架解耦；未来切到其他 executor 也只需要换这个回调。
        """
        route = self.router.route(classification)
        if not route:
            self._trace_route(trace_id, task_id, conversation_id, classification, selected=False, reason="no_matching_workflow")
            if not allow_deepagent_fallback:
                return ExecutionResult(content=_deepagent_disabled_message(classification.task_type), source="workflow", fallback_reason="deepagent_disabled_for_ai_chat")
            return ExecutionResult.from_deepagent(await fallback(query), fallback_reason="no_matching_workflow")

        self._trace_route(trace_id, task_id, conversation_id, classification, selected=True, reason=route.reason, workflow_name=route.workflow_name)
        try:
            result = await self._run_workflow(route.workflow_name, query, trace_id, task_id, conversation_id, message_id=message_id, runtime_profile=runtime_profile, task_type=classification.task_type, allow_deepagent_fallback=allow_deepagent_fallback)
            tracer.emit(
                "workflow_finished",
                trace_id=trace_id,
                task_id=task_id,
                conversation_id=conversation_id,
                agent_name="workflow_runner",
                metadata={"workflow_name": route.workflow_name, "executor": result.workflow_definition.get("executor", "legacy_workflow"), "structured": bool(result.workflow_definition.get("structured")), "result_preview": result.content[:500]},
            )
            return result
        except Exception as error:
            tracer.emit(
                "workflow_failed",
                trace_id=trace_id,
                task_id=task_id,
                conversation_id=conversation_id,
                agent_name="workflow_runner",
                error=str(error)[:1000],
                metadata={"workflow_name": route.workflow_name, "fallback": "deepagent"},
            )
            if not allow_deepagent_fallback:
                return ExecutionResult(content=_deepagent_disabled_message(classification.task_type, route.workflow_name), source="workflow", attempted_workflow=route.workflow_name, workflow_failed=True, fallback_reason="workflow_failed_deepagent_disabled")
            tracer.emit(
                "deepagent_fallback_started",
                trace_id=trace_id,
                task_id=task_id,
                conversation_id=conversation_id,
                agent_name="workflow_runner",
                metadata={"stage": "deep_agent", "status": "running", "workflow_name": route.workflow_name, "reason": "workflow_failed"},
            )
            return ExecutionResult.from_deepagent(await fallback(query), fallback_reason="workflow_failed", attempted_workflow=route.workflow_name, workflow_failed=True)

    async def _run_workflow(self, workflow_name: str, query: str, trace_id: str, task_id: str, conversation_id: str, *, message_id: str = "", runtime_profile: str = "standard", task_type: str = "", allow_deepagent_fallback: bool = True) -> ExecutionResult:
        plan_result = await self._run_plan_workflow(workflow_name, query, trace_id, task_id, conversation_id, message_id=message_id, runtime_profile=runtime_profile, task_type=task_type, allow_deepagent_fallback=allow_deepagent_fallback)
        if plan_result:
            return plan_result
        raise ValueError(f"PlanRegistry 未覆盖 workflow: {workflow_name}")

    async def _run_plan_workflow(self, workflow_name: str, query: str, trace_id: str, task_id: str, conversation_id: str, *, message_id: str = "", runtime_profile: str = "standard", task_type: str = "", allow_deepagent_fallback: bool = True) -> ExecutionResult | None:
        """
        新商业化执行链路：规则 Planner 一次性生成固定 DAG，ParallelExecutor 并行执行，Reducer 统一汇总。

        这里是常见电商任务的默认入口。只要 PlanRegistry 覆盖了 workflow，就不会让主 Agent 自由串行地
        一步步思考和调工具；关键 step 失败时，如果调用方允许 DeepAgent fallback，就抛异常交给
        run_or_fallback，否则 Reducer 会返回带 missingData 的降级结论。
        """
        from agent.runtime.parallel_executor import ParallelExecutor
        from agent.runtime.plan_registry import PlanRegistry
        from agent.runtime.reducer import Reducer
        from agent.runtime.task_profiles import get_task_execution_profile

        plan = PlanRegistry().plan(task_type or workflow_name, query, workflow_name=workflow_name)
        if not plan:
            return None
        profile = get_task_execution_profile(runtime_profile)
        executor = ParallelExecutor(profile)
        run_result = await executor.execute(plan, trace_id=trace_id, task_id=task_id, conversation_id=conversation_id)
        if run_result.has_critical_failure and allow_deepagent_fallback:
            failed_steps = [result.step for result in run_result.step_results if result.critical and result.status != "ok"]
            raise RuntimeError(f"关键计划节点失败，允许 fallback DeepAgent: {failed_steps}")
        reduced = await Reducer(profile).reduce(run_result, query=query, trace_id=trace_id, task_id=task_id, conversation_id=conversation_id, message_id=message_id)
        workflow_definition = {**plan.to_dict(), "executor": "plan_parallel", "structured": True, "reducer": "deterministic_then_fast_polish"}
        return ExecutionResult(
            content=reduced.content,
            source="workflow",
            workflow_name=plan.workflow_name,
            sections=run_result.to_sections(),
            time_range=plan.time_range.to_metadata(),
            workflow_definition=workflow_definition,
            output_requirements=plan.output_requirements,
            fallback_reason="critical_step_degraded" if run_result.has_critical_failure else "",
            workflow_failed=run_result.has_critical_failure,
            structured_result=reduced.structured,
        )

    async def resynthesize_from_result(self, query: str, execution_result: ExecutionResult, fix_instruction: str) -> str:
        """Plan-first 结果已经由 Reducer 统一汇总；Critic 修正不再触发 legacy workflow。"""
        if execution_result.source != "workflow" or not execution_result.sections:
            raise ValueError("只有 workflow 执行结果才能基于 sections 重新综合")
        return execution_result.content

    def _trace_route(
        self,
        trace_id: str,
        task_id: str,
        conversation_id: str,
        classification: TaskClassification,
        *,
        selected: bool,
        reason: str,
        workflow_name: str = "",
    ) -> None:
        tracer.emit(
            "workflow_route_decided",
            trace_id=trace_id,
            task_id=task_id,
            conversation_id=conversation_id,
            agent_name="workflow_router",
            metadata={
                "selected": selected,
                "reason": reason,
                "workflow_name": workflow_name,
                "task_classification": classification.to_dict(),
            },
        )


def _deepagent_disabled_message(task_type: str, attempted_workflow: str = "") -> str:
    workflow_note = f"，尝试的 workflow 为 `{attempted_workflow}`" if attempted_workflow else ""
    return (
        f"当前问题类型 `{task_type}`{workflow_note} 需要更开放的深度推理。"
        "AI Chat 默认使用快速 workflow 与 fast model，完整 DeepAgent 已按配置关闭，"
        "以避免普通对话长时间等待。你可以把问题拆成具体的商品、库存、活动或报告任务后重新发送，"
        "或在后台深度任务中启用 DeepAgent。"
    )