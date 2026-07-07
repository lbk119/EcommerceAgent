"""
业务 Workflow 路由与执行器。

这里不是通用 Planner，而是把 PlannerAgent 已经生成的 TaskPlan 接到固定业务 DAG。这样高价值、
结构稳定的任务先走确定性流程，普通闲聊或未覆盖任务仍回落 DeepAgent。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

from agent.observability.tracer import tracer
from agent.planning.schemas import TaskPlan
from agent.runtime.execution_result import ExecutionResult


DeepAgentExecutor = Callable[[str], Awaitable[str]]


@dataclass(frozen=True)
class WorkflowRoute:
    """WorkflowRouter 的路由结果。"""

    workflow_name: str
    reason: str
    plan: TaskPlan | None = None


class WorkflowRouter:
    """
    根据 PlannerAgent 的 TaskPlan 选择固定业务 workflow。

    路由只看 plan，不重新理解用户意图。这样规划层和执行层职责清晰，后续 PlannerAgent 的实现
    可以替换为模型、规则或混合策略，WorkflowRouter 不需要跟着改。
    """

    _TASK_TO_WORKFLOW = {
        "seasonal_selection": "seasonal_selection",
        "daily_report": "daily_report",
        "inventory_analysis": "inventory_warning",
        "campaign_review": "campaign_review",
        "hot_product_analysis": "hot_product_analysis",
        "product_optimization": "product_optimization",
    }

    def route(self, task_plan: TaskPlan) -> Optional[WorkflowRoute]:
        """返回可执行 workflow；没有覆盖的 deterministic_dag 会交给 DeepAgent 兜底。"""
        if task_plan.execution_mode in {"deterministic_dag", "hybrid_plan", "boundary", "deepagent"}:
            return WorkflowRoute(workflow_name=task_plan.intent.primary_goal or task_plan.primary_task_type, reason=f"planner:{task_plan.execution_mode}", plan=task_plan)
        if task_plan.execution_mode not in {"deterministic_dag", "hybrid_plan"}:
            return None
        workflow_name = self._TASK_TO_WORKFLOW.get(task_plan.primary_task_type)
        if not workflow_name:
            return None
        return WorkflowRoute(workflow_name=workflow_name, reason=f"task_type:{task_plan.primary_task_type}")


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
        task_plan: TaskPlan,
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
        route = self.router.route(task_plan)
        if not route:
            self._trace_route(trace_id, task_id, conversation_id, task_plan, selected=False, reason="no_matching_workflow")
            if not allow_deepagent_fallback:
                return ExecutionResult(content=_deepagent_disabled_message(task_plan.primary_task_type), source="workflow", fallback_reason="deepagent_disabled_for_ai_chat")
            return ExecutionResult.from_deepagent(await fallback(query), fallback_reason="no_matching_workflow")

        self._trace_route(trace_id, task_id, conversation_id, task_plan, selected=True, reason=route.reason, workflow_name=route.workflow_name)
        if route.plan:
            if route.plan.requires_clarification:
                content = _clarification_answer(route.plan)
                return ExecutionResult(content=content, source="planner_clarification", workflow_name=route.workflow_name, workflow_definition={"task_plan": route.plan.to_dict()}, structured_result=_planner_structured(route.plan, content))
            if route.plan.execution_mode == "boundary":
                content = _planner_boundary_answer(route.plan)
                return ExecutionResult(content=content, source="planner_boundary", workflow_name=route.workflow_name, workflow_definition={"task_plan": route.plan.to_dict()}, structured_result=_planner_structured(route.plan, content))
            if route.plan.execution_mode == "deepagent":
                if not allow_deepagent_fallback:
                    return ExecutionResult(content=_deepagent_disabled_message(task_plan.primary_task_type, route.workflow_name), source="planner_boundary", attempted_workflow=route.workflow_name, fallback_reason="deepagent_disabled_by_profile", workflow_definition={"task_plan": route.plan.to_dict()}, structured_result=_planner_structured(route.plan, _deepagent_disabled_message(task_plan.primary_task_type, route.workflow_name)))
                tracer.emit(
                    "deepagent_fallback_started",
                    trace_id=trace_id,
                    task_id=task_id,
                    conversation_id=conversation_id,
                    agent_name="workflow_runner",
                    metadata={"stage": "deep_agent", "status": "running", "workflow_name": route.workflow_name, "reason": "planner_deepagent", "plan_id": route.plan.plan_id},
                )
                return ExecutionResult.from_deepagent(await fallback(query), fallback_reason="planner_deepagent", attempted_workflow=route.workflow_name)
        try:
            result = await self._run_workflow(route.workflow_name, query, trace_id, task_id, conversation_id, message_id=message_id, runtime_profile=runtime_profile, task_type=task_plan.primary_task_type, allow_deepagent_fallback=allow_deepagent_fallback, task_plan=route.plan)
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
                return ExecutionResult(content=_deepagent_disabled_message(task_plan.primary_task_type, route.workflow_name), source="workflow", attempted_workflow=route.workflow_name, workflow_failed=True, fallback_reason="workflow_failed_deepagent_disabled")
            tracer.emit(
                "deepagent_fallback_started",
                trace_id=trace_id,
                task_id=task_id,
                conversation_id=conversation_id,
                agent_name="workflow_runner",
                metadata={"stage": "deep_agent", "status": "running", "workflow_name": route.workflow_name, "reason": "workflow_failed"},
            )
            return ExecutionResult.from_deepagent(await fallback(query), fallback_reason="workflow_failed", attempted_workflow=route.workflow_name, workflow_failed=True)

    async def _run_workflow(self, workflow_name: str, query: str, trace_id: str, task_id: str, conversation_id: str, *, message_id: str = "", runtime_profile: str = "standard", task_type: str = "", allow_deepagent_fallback: bool = True, task_plan: TaskPlan | None = None) -> ExecutionResult:
        """执行一个已命中的 workflow。

        当前所有 workflow 都走 PlanRegistry + ParallelExecutor + Reducer；如果未来保留 legacy workflow，
        可以在这里按 workflow_name 分流。
        """
        plan_result = await self._run_plan_workflow(workflow_name, query, trace_id, task_id, conversation_id, message_id=message_id, runtime_profile=runtime_profile, task_type=task_type, allow_deepagent_fallback=allow_deepagent_fallback, task_plan=task_plan)
        if plan_result:
            return plan_result
        raise ValueError(f"PlanRegistry 未覆盖 workflow: {workflow_name}")

    async def _run_plan_workflow(self, workflow_name: str, query: str, trace_id: str, task_id: str, conversation_id: str, *, message_id: str = "", runtime_profile: str = "standard", task_type: str = "", allow_deepagent_fallback: bool = True, task_plan: TaskPlan | None = None) -> ExecutionResult | None:
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

        registry = PlanRegistry()
        plan = registry.from_task_plan(task_plan) if task_plan else registry.plan(task_type or workflow_name, query, workflow_name=workflow_name)
        if not plan:
            return None
        profile = get_task_execution_profile(runtime_profile)
        # 固定计划一旦生成，就交给并行执行器；不在执行过程中动态追加 step，保证耗时可预测。
        executor = ParallelExecutor(profile)
        run_result = await executor.execute(plan, trace_id=trace_id, task_id=task_id, conversation_id=conversation_id)
        if run_result.has_critical_failure and allow_deepagent_fallback:
            failed_steps = [result.step for result in run_result.step_results if result.critical and result.status != "ok"]
            raise RuntimeError(f"关键计划节点失败，允许 fallback DeepAgent: {failed_steps}")
        reduced = await Reducer(profile).reduce(run_result, query=query, trace_id=trace_id, task_id=task_id, conversation_id=conversation_id, message_id=message_id)
        # workflow_definition 是本次执行计划的快照，后续 trace/报告/排障都可以还原执行路径。
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
        task_plan: TaskPlan,
        *,
        selected: bool,
        reason: str,
        workflow_name: str = "",
    ) -> None:
        """记录 workflow 路由决策，前端 timeline 和 smoke 都依赖该事件判断是否命中固定 DAG。"""
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
                **task_plan.to_trace_metadata(),
            },
        )


def _deepagent_disabled_message(task_type: str, attempted_workflow: str = "") -> str:
    """AI Chat 禁用 DeepAgent fallback 时返回的边界说明。"""
    workflow_note = f"，尝试的 workflow 为 `{attempted_workflow}`" if attempted_workflow else ""
    return (
        f"当前问题类型 `{task_type}`{workflow_note} 需要更开放的深度推理。"
        "AI Chat 默认使用快速 workflow 与 fast model，完整 DeepAgent 已按配置关闭，"
        "以避免普通对话长时间等待。你可以把问题拆成具体的商品、库存、活动或报告任务后重新发送，"
        "或在后台深度任务中启用 DeepAgent。"
    )


def _clarification_answer(plan: TaskPlan) -> str:
    questions = plan.clarification_questions or ["请补充要分析的对象或时间范围。"]
    lines = ["## 需要补充信息", "为了避免误读你的需求，我需要先确认："]
    lines.extend([f"- {question}" for question in questions])
    return "\n".join(lines)


def _planner_boundary_answer(plan: TaskPlan) -> str:
    return (
        "## 当前能力边界\n"
        "我现在只接入了电商经营数据、商品、库存、活动、导入和报告相关能力，不能可靠处理这个问题。\n\n"
        "## 可以继续这样问\n"
        "- 推荐我最近爆品\n"
        "- 帮我看看店铺最近怎么样\n"
        "- 这个月哪些商品适合加大投放，同时看库存和活动风险"
    )


def _planner_structured(plan: TaskPlan, content: str) -> dict:
    return {
        "conclusion": content,
        "evidence": [],
        "actions": [],
        "risks": [],
        "missingData": plan.missing_context,
        "planSummary": plan.to_dict(),
        "stepResults": [],
        "nextQuestions": plan.clarification_questions,
        "confidence": plan.confidence,
    }