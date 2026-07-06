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
from agent.planning.task_classifier import TaskClassification, classify_task
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
        classification_override: TaskClassification | None = None,
    ) -> str:
        """执行一次完整任务，并保持 run_deep_agent 原有异常语义。"""
        task_id = task_id or str(uuid.uuid4())
        context: TaskRunContext | None = None

        try:
            runtime_profile_config = get_runtime_profile(runtime_profile)
            context, guard_result, classification = self.prepare_context(task_query, conversation_id, task_id, tenant_id, user_id, shop_id, classification_override=classification_override)
            context.config["configurable"]["runtime_profile"] = runtime_profile_config.name
            context.config["configurable"]["execution_budget"] = runtime_profile_config.budget
            tracer.emit("runtime_budget_configured", trace_id=task_id, task_id=task_id, conversation_id=conversation_id, agent_name="agent_runtime", metadata=runtime_profile_config.budget.snapshot())
            self.retrieve_memory(context, classification)
            execution_result = await self.execute_agent(context, guard_result.sanitized_query, classification)
            final_result, execution_metadata = await self.run_critic(context, execution_result, classification, runtime_profile=runtime_profile_config.name)
            reflection = self.persist_result(context, final_result, runtime_profile=runtime_profile_config.name)
            self.write_memory(context, final_result, reflection, execution_metadata, runtime_profile=runtime_profile_config.name)
            self.finalize_trace(context)
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
        classification_override: TaskClassification | None = None,
    ) -> tuple[TaskRunContext, PromptGuardResult, TaskClassification]:
        """
        prepare_context 阶段：输入安全、任务分类、ContextVar 和工作目录准备。

        这里故意先分类、再 build_task_context。分类结果会进入 trace；build_task_context 仍负责现有
        文件复制和 LangGraph config，避免一次重构同时改动工具上下文边界。
        """
        from agent.memory.evolution_memory import append_task_event
        from api.monitor import monitor
        from agent.runtime.task_context import build_task_context

        stage_started = time.perf_counter()
        tracer.emit("prompt_guard_started", trace_id=task_id, task_id=task_id, conversation_id=conversation_id, agent_name="prompt_guard", metadata={"stage": "prompt_guard", "status": "running"})
        guard_result = inspect_user_prompt(task_query)
        tracer.emit("prompt_guard_finished", trace_id=task_id, task_id=task_id, conversation_id=conversation_id, agent_name="prompt_guard", latency_ms=round((time.perf_counter() - stage_started) * 1000, 2), metadata={"stage": "prompt_guard", "status": "completed", "prompt_guard": guard_result.to_metadata()})

        stage_started = time.perf_counter()
        classification = classification_override or classify_task(guard_result.sanitized_query)
        tracer.emit("task_classified", trace_id=task_id, task_id=task_id, conversation_id=conversation_id, agent_name="task_classifier", latency_ms=round((time.perf_counter() - stage_started) * 1000, 2), metadata={"stage": "task_classification", "status": "completed", "task_classification": classification.to_dict()})

        stage_started = time.perf_counter()
        context = build_task_context(guard_result.sanitized_query, conversation_id, task_id, tenant_id, user_id, shop_id)
        tracer.emit("context_prepared", trace_id=task_id, task_id=task_id, conversation_id=conversation_id, agent_name="main_agent", latency_ms=round((time.perf_counter() - stage_started) * 1000, 2), metadata={"stage": "context_prepared", "status": "completed", "tenant_id": tenant_id, "shop_id": shop_id})

        append_task_event("task_started", task_id, {
            "query": redact_secrets(guard_result.sanitized_query),
            "conversation_id": conversation_id,
            "tenant_id": tenant_id,
            "user_id": user_id,
            "shop_id": shop_id,
            "task_classification": classification.to_dict(),
        })
        monitor._emit("task_started", "任务已进入 AgentRuntime", {
            "task_id": task_id,
            "conversation_id": conversation_id,
            "task_classification": classification.to_dict(),
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
                "task_classification": classification.to_dict(),
            },
        )
        return context, guard_result, classification

    def retrieve_memory(self, context: TaskRunContext, classification: TaskClassification) -> None:
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
            metadata={"stage": "retrieve_memory", "task_classification": classification.to_dict()},
        )

    async def execute_agent(self, context: TaskRunContext, sanitized_query: str, classification: TaskClassification) -> ExecutionResult:
        """
        execute_agent 阶段：优先调度 deterministic workflow，未覆盖时回落 DeepAgent。

        这让 task_classifier 的 preferred_workflow 真正影响执行路径；同时保留 DeepAgent fallback，避免
        新增 workflow 覆盖不完整时影响普通任务可用性。
        """
        from agent.runtime.agent_runner import run_agent_with_reflection
        from agent.workflows.workflow_runner import WorkflowRunner

        async def run_deepagent(query: str) -> str:
            return await run_agent_with_reflection(self.agent, query, context.path_instruction, context.config, context.task_id)

        runtime_profile = normalize_runtime_profile(context.config.get("configurable", {}).get("runtime_profile", "standard"))
        allow_deepagent_fallback = runtime_profile == "deep" or (runtime_profile == "standard" and os.getenv("STANDARD_AGENT_ALLOW_DEEPAGENT_FALLBACK", "false").lower() in {"1", "true", "yes", "on"})

        return await WorkflowRunner().run_or_fallback(
            query=sanitized_query,
            classification=classification,
            trace_id=context.task_id,
            task_id=context.task_id,
            conversation_id=context.conversation_id,
            fallback=run_deepagent,
            allow_deepagent_fallback=allow_deepagent_fallback,
            runtime_profile=runtime_profile,
        )

    async def run_critic(self, context: TaskRunContext, execution_result: ExecutionResult, classification: TaskClassification, *, runtime_profile: str = "full") -> tuple[str, dict]:
        """run_critic 阶段：执行 Critic policy、Critic 调用和最多一次 fix_instruction 修正。"""
        from agent.runtime.agent_runner import run_agent_with_reflection
        from agent.runtime.result_pipeline import run_critic_stage
        from agent.workflows.workflow_runner import WorkflowRunner

        normalized_profile = normalize_runtime_profile(runtime_profile)
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
            }

        async def rerun_with_critic_fix(fix_instruction: str) -> str:
            if execution_result.source == "workflow":
                return await WorkflowRunner().resynthesize_from_result(context.query, execution_result, fix_instruction)
            return await run_agent_with_reflection(self.agent, fix_instruction, context.path_instruction, context.config, context.task_id)

        critic_stage_result = await run_critic_stage(
            context,
            execution_result.content,
            agent_specs=self.agent_specs,
            rerun_with_fix=rerun_with_critic_fix,
            task_classification=classification,
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
