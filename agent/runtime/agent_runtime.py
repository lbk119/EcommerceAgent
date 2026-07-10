"""AgentRuntime 阶段化执行器。

`run_agent_task` 会把请求交给这里完成 prompt guard、context/checkpoint 准备、deepagents-native 执行、
Evaluation、持久化、记忆写入和 trace 收尾。每个阶段都有单独方法，方便 profile 裁剪、fallback 和非功能监控。
"""

from __future__ import annotations

import asyncio
import os
import time
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Sequence

from agent.plan.models import AgentTaskPlan
from agent.trace.tracer import tracer
from agent.plan.planner import planner_agent
from agent.runtime.profiles import get_runtime_profile, normalize_runtime_profile
from agent.security.prompt_guard import PromptGuardResult, inspect_user_prompt
from agent.security.redaction import redact_secrets

if TYPE_CHECKING:
    from agent.runtime.execution_result import ExecutionResult
    from agent.runtime.task_context import TaskRunContext


@dataclass
class AgentRuntime:
    """
    单个 Agent 图的运行时编排器。

    每个方法对应一个架构阶段：prepare_context、retrieve_memory、execute_agent、run_evaluation、
    persist_result、write_memory、finalize_trace。
    """

    agent: Any
    agent_specs: Sequence[Any]

    async def run(
        self,
        task_query: str,
        conversation_id: str,
        task_id: str | None = None,
        tenant_id: str = "default_tenant",
        user_id: str = "local_user",
        shop_id: str = "default_shop",
        runtime_profile: str = "full",
        task_plan_override: AgentTaskPlan | None = None,
    ) -> str:
        """执行一次 realtime/standard/deep DeepAgents 任务。"""
        task_id = task_id or str(uuid.uuid4())
        context: TaskRunContext | None = None

        try:
            runtime_profile_config = get_runtime_profile(runtime_profile)
            # prepare_context 会完成 prompt guard、任务分类、工作目录和 ContextVar 设置。
            context, guard_result, task_plan = self.prepare_context(task_query, conversation_id, task_id, tenant_id, user_id, shop_id, task_plan_override=task_plan_override, runtime_profile=runtime_profile_config.name)
            if task_plan_override is None:
                task_plan = await planner_agent.plan_async(guard_result.sanitized_query, profile=runtime_profile_config.name, context={"tenant_id": tenant_id, "shop_id": shop_id, "user_id": user_id}, trace_id=task_id, task_id=task_id, conversation_id=conversation_id)
                tracer.emit("task_classified", trace_id=task_id, task_id=task_id, conversation_id=conversation_id, agent_name="planner_agent", metadata={"stage": "task_planning", "status": "completed", **task_plan.to_trace_metadata(), "planned_from": "planner_agent"})
            # 预算对象放入 LangGraph config，runner 在模型、工具、subagent 调用前从这里取出并计数。
            context.config["configurable"]["runtime_profile"] = runtime_profile_config.name
            context.config["configurable"]["execution_budget"] = runtime_profile_config.budget
            tracer.emit("runtime_budget_configured", trace_id=task_id, task_id=task_id, conversation_id=conversation_id, agent_name="agent_runtime", metadata=runtime_profile_config.budget.snapshot())
            self.retrieve_memory(context, task_plan)
            execution_result = await self.execute_agent(context, guard_result.sanitized_query, task_plan)
            final_result, execution_metadata = await self.run_evaluation(context, execution_result, task_plan, runtime_profile=runtime_profile_config.name)
            reflection = self.persist_result(context, final_result, runtime_profile=runtime_profile_config.name)
            self.write_memory(context, final_result, reflection, execution_metadata, runtime_profile=runtime_profile_config.name)
            self.finalize_trace(context)
            # 返回 FinalResult：字符串兼容旧接口，structured_result 供新 UI 展示。
            return execution_result.to_final_result(final_result)
        except asyncio.CancelledError:
            if context:
                from agent.runtime.result_pipeline import process_cancelled

                process_cancelled(context)
            raise
        except Exception as error:
            if error.__class__.__name__ == "GraphRecursionError":
                if context:
                    from agent.runtime.result_pipeline import process_graph_recursion_error

                    process_graph_recursion_error(context)
                    raise RuntimeError(f"任务执行达到图递归上限（{context.config['recursion_limit']} 步），可能陷入重复调用，已自动停止。请缩小问题范围，或改用更明确的单一信息来源。")
                raise
            if context:
                from agent.runtime.result_pipeline import process_failure

                process_failure(context, str(error))
            raise
        finally:
            if context:
                context.cleanup()

    def prepare_context(
        self,
        task_query: str,
        conversation_id: str,
        task_id: str,
        tenant_id: str,
        user_id: str,
        shop_id: str,
        task_plan_override: AgentTaskPlan | None = None,
        runtime_profile: str = "standard",
    ) -> tuple[TaskRunContext, PromptGuardResult, AgentTaskPlan]:
        """
        prepare_context 阶段：输入安全、任务规划、ContextVar 和工作目录准备。

        这里故意先规划、再 build_task_context。计划结果会进入 trace；build_task_context 仍负责现有
        文件复制和 LangGraph config，避免一次重构同时改动工具上下文边界。
        """
        from agent.reflection.evolution_log import append_task_event
        from api.monitor import monitor
        from agent.runtime.task_context import build_task_context

        stage_started = time.perf_counter()
        # prompt guard 不直接阻断普通业务问题，但会把风险写入 trace，供后续审计/策略升级。
        tracer.emit("prompt_guard_started", trace_id=task_id, task_id=task_id, conversation_id=conversation_id, agent_name="prompt_guard", metadata={"stage": "prompt_guard", "status": "running"})
        guard_result = inspect_user_prompt(task_query)
        tracer.emit("prompt_guard_finished", trace_id=task_id, task_id=task_id, conversation_id=conversation_id, agent_name="prompt_guard", latency_ms=round((time.perf_counter() - stage_started) * 1000, 2), metadata={"stage": "prompt_guard", "status": "completed", "prompt_guard": guard_result.to_metadata()})

        stage_started = time.perf_counter()
        # 规划优先基于脱敏/截断后的 query，避免超长输入污染下游 planner。
        task_plan = task_plan_override or planner_agent.plan(guard_result.sanitized_query, profile=runtime_profile)
        planner_latency_ms = round((time.perf_counter() - stage_started) * 1000, 2)
        tracer.emit("task_classified", trace_id=task_id, task_id=task_id, conversation_id=conversation_id, agent_name="planner_agent", latency_ms=planner_latency_ms, metadata={"stage": "task_planning", "status": "completed", **task_plan.to_trace_metadata(include_plan=False), "planned_from": "planner_acceptance_fallback"})

        stage_started = time.perf_counter()
        context = build_task_context(guard_result.sanitized_query, conversation_id, task_id, tenant_id, user_id, shop_id, runtime_profile=runtime_profile)
        tracer.emit("context_prepared", trace_id=task_id, task_id=task_id, conversation_id=conversation_id, agent_name="main_agent", latency_ms=round((time.perf_counter() - stage_started) * 1000, 2), metadata={"stage": "context_prepared", "status": "completed", "tenant_id": tenant_id, "shop_id": shop_id})

        append_task_event("task_started", task_id, {
            "query": redact_secrets(guard_result.sanitized_query),
            "conversation_id": conversation_id,
            "tenant_id": tenant_id,
            "user_id": user_id,
            "shop_id": shop_id,
            "task_plan": task_plan.to_lightweight_dict(),
        })
        monitor._emit("task_started", "任务已进入 AgentRuntime", {
            "task_id": task_id,
            "conversation_id": conversation_id,
            "task_plan": task_plan.to_lightweight_dict(),
        })
        tracer.emit(
            "agent_started",
            trace_id=task_id,
            task_id=task_id,
            conversation_id=conversation_id,
            agent_name="main_agent",
            metadata={
                "query": redact_secrets(guard_result.sanitized_query),
                "tenant_id": tenant_id,
                "user_id": user_id,
                "shop_id": shop_id,
                "prompt_guard": guard_result.to_metadata(),
                **task_plan.to_trace_metadata(include_plan=False),
            },
        )
        return context, guard_result, task_plan

    def retrieve_memory(self, context: TaskRunContext, task_plan: AgentTaskPlan) -> None:
        """
        retrieve_memory 阶段：当前由 build_task_context 内部完成长期记忆召回。

        这个显式空阶段保留给后续按 task_type 调整 top_k 或做向量召回重排。
        """
        tracer.emit(
            "runtime_stage_completed",
            trace_id=context.task_id,
            task_id=context.task_id,
            conversation_id=context.conversation_id,
            agent_name="main_agent",
            metadata={"stage": "retrieve_memory", **task_plan.to_trace_metadata(include_plan=False)},
        )

    async def execute_agent(self, context: TaskRunContext, sanitized_query: str, task_plan: AgentTaskPlan) -> ExecutionResult:
        """
        execute_agent 阶段：执行 Planner 分派的 deepagents-native 业务 subagents。
        """
        from agent.subagent.config import deepagents_enabled
        from agent.subagent.runtime import DeepAgentsNativeRuntime
        from agent.runtime.execution_result import ExecutionResult

        runtime_profile = normalize_runtime_profile(context.config.get("configurable", {}).get("runtime_profile", "standard"))
        if runtime_profile == "realtime":
            if self.agent is None or not deepagents_enabled(runtime_profile):
                raise RuntimeError("deepagents-native runtime is required for realtime profile.")
            return await DeepAgentsNativeRuntime(self.agent).run(context, sanitized_query, task_plan)
        if task_plan.requires_clarification:
            content = _clarification_answer(task_plan)
            return ExecutionResult(content=content, source="planner_clarification", workflow_name="agent_orchestration", workflow_definition={"task_plan": task_plan.to_dict()}, structured_result=_planner_structured(task_plan, content))
        if task_plan.boundary or not task_plan.assignments:
            content = _planner_boundary_answer(task_plan)
            return ExecutionResult(content=content, source="planner_boundary", workflow_name="agent_orchestration", workflow_definition={"task_plan": task_plan.to_dict()}, structured_result=_planner_structured(task_plan, content))
        if runtime_profile not in {"standard", "deep"}:
            raise RuntimeError(f"Business assignments require standard/deep profile, got {runtime_profile}.")
        if self.agent is None or not deepagents_enabled(runtime_profile):
            raise RuntimeError(f"deepagents-native runtime is required for {runtime_profile} business assignments.")
        return await DeepAgentsNativeRuntime(self.agent).run(context, sanitized_query, task_plan)

    async def run_evaluation(self, context: TaskRunContext, execution_result: ExecutionResult, task_plan: AgentTaskPlan, *, runtime_profile: str = "full") -> tuple[str, dict]:
        """run_evaluation 阶段：执行 Evaluation policy、Evaluation 调用和最多一次 fix_instruction 修正。"""
        from agent.runtime.result_pipeline import run_evaluation_stage

        normalized_profile = normalize_runtime_profile(runtime_profile)
        # 非 deep profile 默认跳过 Evaluation，把 AI Chat/standard 的热路径成本压低；可通过 env 临时开启。
        if normalized_profile != "deep" and os.getenv("AI_CHAT_ENABLE_EVALUATION", "false").lower() not in {"1", "true", "yes", "on"}:
            tracer.emit("evaluation_skipped", trace_id=context.task_id, task_id=context.task_id, conversation_id=context.conversation_id, agent_name="evaluation_agent", metadata={"stage": "evaluation", "status": "skipped", "reason": "non_deep_profile", "runtime_profile": normalized_profile})
            return execution_result.content, {
                "source": execution_result.source,
                "workflow_name": execution_result.workflow_name,
                "attempted_workflow": execution_result.attempted_workflow,
                "time_range": execution_result.time_range,
                "section_errors": execution_result.section_errors(),
                "fallback_reason": execution_result.fallback_reason,
                "workflow_failed": execution_result.workflow_failed,
                "evaluation_status": "skipped",
                "runtime_profile": normalized_profile,
                "plan_id": task_plan.plan_id,
                "assignment_count": len(task_plan.assignments),
            }

        async def rerun_with_evaluation_fix(fix_instruction: str) -> str:
            tracer.emit(
            "evaluation_revision_skipped",
                trace_id=context.task_id,
                task_id=context.task_id,
                conversation_id=context.conversation_id,
                agent_name="evaluation_agent",
                metadata={"reason": "deepagents_native_revision_disabled", "instruction_preview": fix_instruction[:500]},
            )
            return execution_result.content

        evaluation_stage_result = await run_evaluation_stage(
            context,
            execution_result.content,
            agent_specs=self.agent_specs,
            rerun_with_fix=rerun_with_evaluation_fix,
            task_plan=task_plan,
        )
        execution_metadata = {
            "source": execution_result.source,
            "workflow_name": execution_result.workflow_name,
            "attempted_workflow": execution_result.attempted_workflow,
            "time_range": execution_result.time_range,
            "section_errors": execution_result.section_errors(),
            "fallback_reason": execution_result.fallback_reason,
            "workflow_failed": execution_result.workflow_failed,
            "evaluation_status": evaluation_stage_result.evaluation_status,
            "plan_id": task_plan.plan_id,
            "assignment_count": len(task_plan.assignments),
        }
        return evaluation_stage_result.content, execution_metadata

    def persist_result(self, context: TaskRunContext, final_result: str, *, runtime_profile: str = "deep") -> dict:
        """persist_result 阶段：写 task_events、reflection 和 policy proposal。"""
        from agent.runtime.result_pipeline import persist_result

        return persist_result(context, final_result, runtime_profile=runtime_profile)

    def write_memory(self, context: TaskRunContext, final_result: str, reflection: dict, execution_metadata: dict, *, runtime_profile: str = "full") -> dict:
        """write_memory 阶段：写长期记忆候选和 memory trace。"""
        from agent.runtime.result_pipeline import write_memory

        normalized_profile = normalize_runtime_profile(runtime_profile)
        if normalized_profile != "deep" and os.getenv("AGENT_HOT_PATH_MEMORY_WRITE", "false").lower() not in {"1", "true", "yes", "on"}:
            tracer.emit("memory_write_skipped", trace_id=context.task_id, task_id=context.task_id, conversation_id=context.conversation_id, agent_name="memory_writer", metadata={"stage": "memory_write", "status": "skipped", "reason": "lightweight_profile_deferred", "runtime_profile": runtime_profile})
            return {"candidates": 0, "written": 0, "review": 0, "skipped": 1, "degraded": 0}
        return write_memory(context, final_result, reflection, execution_metadata=execution_metadata)

    def finalize_trace(self, context: TaskRunContext) -> None:
        """finalize_trace 阶段：统一结束成功 trace。"""
        from agent.runtime.result_pipeline import finalize_success_trace

        finalize_success_trace(context)


def _clarification_answer(plan: AgentTaskPlan) -> str:
    questions = plan.clarification_questions or ["请补充要分析的对象或时间范围。"]
    lines = ["## 需要补充信息", "为了避免误读你的需求，我需要先确认："]
    lines.extend([f"- {question}" for question in questions])
    return "\n".join(lines)


def _planner_boundary_answer(plan: AgentTaskPlan) -> str:
    return (
        "## 当前能力边界\n"
        "我现在只接入了电商经营数据、商品、库存、活动、导入和报告相关能力，不能可靠处理这个问题。\n\n"
        "## 可以继续这样问\n"
        "- 推荐我最近爆品\n"
        "- 帮我看看店铺最近怎么样\n"
        "- 这个月哪些商品适合加大投放，同时看库存和活动风险"
    )


def _planner_structured(plan: AgentTaskPlan, content: str) -> dict:
    return {
        "conclusion": content,
        "evidence": [],
        "actions": [],
        "risks": [],
        "missingData": plan.missing_context,
        "planSummary": plan.to_dict(),
        "agentResults": [],
        "nextQuestions": plan.clarification_questions,
        "confidence": plan.confidence,
    }
