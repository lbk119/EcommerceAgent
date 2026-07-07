"""
AgentRuntime：主 Agent 的阶段化运行时。

run_deep_agent 是 API 层历史入口；真正的执行阶段放在这里，便于后续把 DeepAgent、deterministic
DAG、Planner、Critic retry 和 trace 收尾替换为不同实现。
"""

from __future__ import annotations

import asyncio
import os
import time
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Sequence

from agent.core.agent_spec import AgentSpec
from agent.observability.tracer import tracer
from agent.planning.planner_agent import planner_agent
from agent.planning.schemas import TaskPlan
from agent.runtime.profiles import get_runtime_profile, normalize_runtime_profile
from agent.security.prompt_guard import PromptGuardResult, inspect_user_prompt
from agent.security.redaction import redact_secrets

if TYPE_CHECKING:
    from agent.runtime.execution_result import ExecutionResult
    from agent.runtime.task_context import TaskRunContext


@dataclass
class AgentRuntime:
    """
    单个主 Agent 图的运行时编排器。

    每个方法对应一个架构阶段：prepare_context、retrieve_memory、execute_agent、run_critic、
    persist_result、write_memory、finalize_trace。方法名本身就是后续接 DAG/Planner 的扩展点。
    """

    agent: Any
    agent_specs: Sequence[AgentSpec]

    async def run(
        self,
        task_query: str,
        conversation_id: str,
        task_id: str | None = None,
        tenant_id: str = "default_tenant",
        user_id: str = "local_user",
        shop_id: str = "default_shop",
        runtime_profile: str = "full",
        task_plan_override: TaskPlan | None = None,
    ) -> str:
        """执行一次完整任务，并保持 run_deep_agent 原有异常语义。"""
        task_id = task_id or str(uuid.uuid4())
        context: TaskRunContext | None = None

        try:
            runtime_profile_config = get_runtime_profile(runtime_profile)
            # prepare_context 会完成 prompt guard、任务分类、工作目录和 ContextVar 设置。
            context, guard_result, task_plan = self.prepare_context(task_query, conversation_id, task_id, tenant_id, user_id, shop_id, task_plan_override=task_plan_override, runtime_profile=runtime_profile_config.name)
            if task_plan_override is None:
                task_plan = await planner_agent.plan_async(guard_result.sanitized_query, profile=runtime_profile_config.name, context={"tenant_id": tenant_id, "shop_id": shop_id, "user_id": user_id}, trace_id=task_id, task_id=task_id, conversation_id=conversation_id)
                tracer.emit("task_classified", trace_id=task_id, task_id=task_id, conversation_id=conversation_id, agent_name="planner_agent", metadata={"stage": "task_planning", "status": "completed", **task_plan.to_trace_metadata(), "planned_from": "planner_agent"})
            # 预算对象放入 LangGraph config，runner 在模型/工具/子 Agent 调用前从这里取出并计数。
            context.config["configurable"]["runtime_profile"] = runtime_profile_config.name
            context.config["configurable"]["execution_budget"] = runtime_profile_config.budget
            tracer.emit("runtime_budget_configured", trace_id=task_id, task_id=task_id, conversation_id=conversation_id, agent_name="agent_runtime", metadata=runtime_profile_config.budget.snapshot())
            self.retrieve_memory(context, task_plan)
            execution_result = await self.execute_agent(context, guard_result.sanitized_query, task_plan)
            final_result, execution_metadata = await self.run_critic(context, execution_result, task_plan, runtime_profile=runtime_profile_config.name)
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
            if error.__class__.__name__ == "LoopDetectedError":
                if context:
                    from agent.runtime.result_pipeline import process_loop_failure

                    process_loop_failure(context, error)
                raise RuntimeError(f"检测到重复调用，反思重试后仍未收敛，已停止任务。{getattr(error, 'summary', '')}")
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
            from agent.runtime.agent_runner import clear_tool_calls_for_task

            # 每个任务结束都清理进程内工具调用摘要和 ContextVar，避免长进程串任务污染。
            clear_tool_calls_for_task(task_id)
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
        task_plan_override: TaskPlan | None = None,
        runtime_profile: str = "standard",
    ) -> tuple[TaskRunContext, PromptGuardResult, TaskPlan]:
        """
        prepare_context 阶段：输入安全、任务规划、ContextVar 和工作目录准备。

        这里故意先规划、再 build_task_context。计划结果会进入 trace；build_task_context 仍负责现有
        文件复制和 LangGraph config，避免一次重构同时改动工具上下文边界。
        """
        from agent.memory.evolution_memory import append_task_event
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
        tracer.emit("task_classified", trace_id=task_id, task_id=task_id, conversation_id=conversation_id, agent_name="planner_agent", latency_ms=planner_latency_ms, metadata={"stage": "task_planning", "status": "completed", **task_plan.to_trace_metadata(include_plan=False), "planned_from": "deterministic_acceptance_fallback"})

        stage_started = time.perf_counter()
        context = build_task_context(guard_result.sanitized_query, conversation_id, task_id, tenant_id, user_id, shop_id)
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

    def retrieve_memory(self, context: TaskRunContext, task_plan: TaskPlan) -> None:
        """
        retrieve_memory 阶段：当前由 build_task_context 内部完成长期记忆召回。

        这个显式空阶段是有意保留的架构边界。下一步如果要按 task_type 调整 top_k、按 DAG 节点召回
        或做向量召回重排，只需要把逻辑从 task_context 搬到这里，不影响执行和持久化阶段。
        """
        tracer.emit(
            "runtime_stage_completed",
            trace_id=context.task_id,
            task_id=context.task_id,
            conversation_id=context.conversation_id,
            agent_name="main_agent",
            metadata={"stage": "retrieve_memory", **task_plan.to_trace_metadata(include_plan=False)},
        )

    async def execute_agent(self, context: TaskRunContext, sanitized_query: str, task_plan: TaskPlan) -> ExecutionResult:
        """
        execute_agent 阶段：优先调度 deterministic workflow，未覆盖时回落 DeepAgent。

        这让 PlannerAgent 的 execution_mode 真正影响执行路径；同时保留 DeepAgent fallback，避免
        新增 workflow 覆盖不完整时影响普通任务可用性。
        """
        from agent.runtime.agent_runner import run_agent_with_reflection
        from agent.workflows.workflow_runner import WorkflowRunner

        async def run_deepagent(query: str) -> str:
            return await run_agent_with_reflection(self.agent, query, context.path_instruction, context.config, context.task_id)

        runtime_profile = normalize_runtime_profile(context.config.get("configurable", {}).get("runtime_profile", "standard"))
        # standard 默认不回落完整 DeepAgent，避免 deterministic workflow 失败后拖慢普通后台任务。
        allow_deepagent_fallback = runtime_profile == "deep" or (runtime_profile == "standard" and os.getenv("STANDARD_AGENT_ALLOW_DEEPAGENT_FALLBACK", "false").lower() in {"1", "true", "yes", "on"})

        return await WorkflowRunner().run_or_fallback(
            query=sanitized_query,
            task_plan=task_plan,
            trace_id=context.task_id,
            task_id=context.task_id,
            conversation_id=context.conversation_id,
            fallback=run_deepagent,
            allow_deepagent_fallback=allow_deepagent_fallback,
            runtime_profile=runtime_profile,
        )

    async def run_critic(self, context: TaskRunContext, execution_result: ExecutionResult, task_plan: TaskPlan, *, runtime_profile: str = "full") -> tuple[str, dict]:
        """run_critic 阶段：执行 Critic policy、Critic 调用和最多一次 fix_instruction 修正。"""
        from agent.runtime.agent_runner import run_agent_with_reflection
        from agent.runtime.result_pipeline import run_critic_stage
        from agent.workflows.workflow_runner import WorkflowRunner

        normalized_profile = normalize_runtime_profile(runtime_profile)
        # 非 deep profile 默认跳过 Critic，把 AI Chat/standard 的热路径成本压低；可用 env 临时开启。
        if normalized_profile != "deep" and os.getenv("AI_CHAT_ENABLE_CRITIC", "false").lower() not in {"1", "true", "yes", "on"}:
            tracer.emit("critic_skipped", trace_id=context.task_id, task_id=context.task_id, conversation_id=context.conversation_id, agent_name="critic_agent", metadata={"stage": "critic", "status": "skipped", "reason": "non_deep_profile", "runtime_profile": normalized_profile})
            return execution_result.content, {
                "source": execution_result.source,
                "workflow_name": execution_result.workflow_name,
                "attempted_workflow": execution_result.attempted_workflow,
                "time_range": execution_result.time_range,
                "section_errors": execution_result.section_errors(),
                "fallback_reason": execution_result.fallback_reason,
                "workflow_failed": execution_result.workflow_failed,
                "critic_status": "skipped",
                "runtime_profile": normalized_profile,
                "plan_id": task_plan.plan_id,
                "plan_steps_count": len(task_plan.steps),
            }

        async def rerun_with_critic_fix(fix_instruction: str) -> str:
            # workflow 结果优先基于已有结构化数据重合成；DeepAgent 结果才重新走 Agent 反思链路。
            if execution_result.source == "workflow":
                return await WorkflowRunner().resynthesize_from_result(context.query, execution_result, fix_instruction)
            return await run_agent_with_reflection(self.agent, fix_instruction, context.path_instruction, context.config, context.task_id)

        critic_stage_result = await run_critic_stage(
            context,
            execution_result.content,
            agent_specs=self.agent_specs,
            rerun_with_fix=rerun_with_critic_fix,
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
            "critic_status": critic_stage_result.critic_status,
            "plan_id": task_plan.plan_id,
            "plan_steps_count": len(task_plan.steps),
        }
        return critic_stage_result.content, execution_metadata

    def persist_result(self, context: TaskRunContext, final_result: str, *, runtime_profile: str = "deep") -> dict:
        """persist_result 阶段：写 task_events/reflection/policy proposal。"""
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
