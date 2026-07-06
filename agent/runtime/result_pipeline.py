"""
任务结果后处理流水线。

主 Agent 得到 final_result 之后，还需要完成一组“质量和记忆闭环”：
- 按需运行 Critic；
- 生成任务反思；
- 创建策略建议；
- 写入长期记忆候选；
- 写入 task_events 和 trace。

这些步骤不属于 DeepAgents 图本身，拆出来后 main_agent.py 不再混杂执行流、反思、记忆和观测。
"""

import asyncio
import os
import time
from dataclasses import dataclass
from typing import Awaitable, Callable, Literal, Optional, Sequence

from api.monitor import monitor
from agent.core.agent_spec import AgentSpec
from agent.critic.critic_agent import CriticResult, run_critic
from agent.critic.policy import evaluate_critic_policy
from agent.evolution.policy_review import create_policy_proposal
from agent.evolution.reflection import build_task_reflection
from agent.memory.evolution_memory import append_reflection, append_task_event
from agent.memory.writer import write_memories_after_task
from agent.observability.tracer import tracer
from agent.planning.task_classifier import TaskClassification
from agent.runtime.agent_runner import LoopDetectedError, get_tool_calls_for_task
from agent.runtime.task_context import TaskRunContext
from agent.security.redaction import redact_secrets


CriticRevisionRunner = Callable[[str], Awaitable[str]]
CriticStatus = Literal["skipped", "passed", "failed"]


@dataclass(frozen=True)
class CriticStageResult:
    """Critic 阶段输出，包含最终文本和质量校验状态。"""

    content: str
    critic_status: CriticStatus


async def run_critic_stage(
    context: TaskRunContext,
    final_result: str,
    *,
    agent_specs: Optional[Sequence[AgentSpec]] = None,
    rerun_with_fix: Optional[CriticRevisionRunner] = None,
    task_classification: Optional[TaskClassification] = None,
) -> CriticStageResult:
    """
    质量校验阶段。

    AgentRuntime 会直接调用这个阶段。这样 Critic retry、未来多 Critic 或不同任务类型的校验器，
    都可以在一个边界内替换，而不会影响结果持久化和记忆写入。
    """
    content, critic_status = await _apply_critic_if_needed(
        context,
        final_result,
        agent_specs=agent_specs or [],
        rerun_with_fix=rerun_with_fix,
        task_classification=task_classification,
    )
    return CriticStageResult(content=content, critic_status=critic_status)


def persist_result(context: TaskRunContext, final_result: str, *, runtime_profile: str = "deep") -> dict:
    """
    结果持久化阶段。

    这里写 task_events、reflection 和 policy proposal。它不写长期记忆，也不结束 trace，保持每个
    阶段的副作用单一，后续接 DAG 时可以复用同一套落盘逻辑。
    """
    started_at = time.perf_counter()
    tracer.emit("persistence_started", trace_id=context.task_id, task_id=context.task_id, conversation_id=context.conversation_id, agent_name="result_pipeline", metadata={"stage": "persistence", "status": "running"})
    safe_result = redact_secrets(final_result)
    append_task_event("task_completed", context.task_id, {"result": safe_result, "conversation_id": context.conversation_id})
    if runtime_profile == "deep":
        reflection = build_task_reflection(context.query, result=safe_result)
        append_reflection(context.task_id, context.query, reflection["status"], reflection["summary"], reflection["lessons"])
        create_policy_proposal(context.task_id, context.query, reflection)
    else:
        reflection = {"status": "deferred", "summary": "reflection and policy proposal moved out of hot path", "lessons": []}
        tracer.emit("result_enrichment_deferred", trace_id=context.task_id, task_id=context.task_id, conversation_id=context.conversation_id, agent_name="result_pipeline", metadata={"runtime_profile": runtime_profile, "deferred": ["reflection", "policy_proposal"]})
    tracer.emit("persistence_finished", trace_id=context.task_id, task_id=context.task_id, conversation_id=context.conversation_id, agent_name="result_pipeline", latency_ms=round((time.perf_counter() - started_at) * 1000, 2), metadata={"stage": "persistence", "status": "completed"})
    return reflection


def write_memory(context: TaskRunContext, final_result: str, reflection: dict, execution_metadata: Optional[dict] = None) -> dict:
    """
    长期记忆写入阶段。

    Store/Milvus 不应该保存密钥、token 或连接串，所以进入 memory writer 前先做脱敏。脱敏只影响
    持久化内容，不改用户最终看到的回答。
    """
    started_at = time.perf_counter()
    tracer.emit("memory_write_started", trace_id=context.task_id, task_id=context.task_id, conversation_id=context.conversation_id, agent_name="memory_writer", metadata={"stage": "memory_write", "status": "running"})
    safe_result = redact_secrets(final_result)
    execution_metadata = execution_metadata or {}
    memory_write_result = write_memories_after_task(context.identity, context.query, safe_result, reflection.get("lessons", []), execution_metadata=execution_metadata)
    memory_event_metadata = {"conversation_id": context.conversation_id, **memory_write_result, "execution_metadata": execution_metadata}
    append_task_event("memory_write_completed", context.task_id, memory_event_metadata)
    tracer.emit("memory_write_finished", trace_id=context.task_id, task_id=context.task_id, conversation_id=context.conversation_id, agent_name="memory_writer", latency_ms=round((time.perf_counter() - started_at) * 1000, 2), metadata={"stage": "memory_write", "status": "completed", **memory_event_metadata})
    tracer.emit("memory_written", trace_id=context.task_id, task_id=context.task_id, conversation_id=context.conversation_id, agent_name="main_agent", metadata=memory_event_metadata)
    return memory_write_result


def finalize_success_trace(context: TaskRunContext) -> None:
    """成功收尾阶段：只负责把主 Agent trace 标记为 succeeded。"""
    tracer.emit("agent_finished", trace_id=context.task_id, task_id=context.task_id, conversation_id=context.conversation_id, agent_name="main_agent", metadata={"status": "succeeded"})


def process_loop_failure(context: TaskRunContext, loop_error: LoopDetectedError) -> None:
    """循环检测终止后的统一失败处理。"""
    error_message = f"检测到重复调用，反思重试后仍未收敛，已停止任务。{loop_error.summary}"
    process_failure(context, error_message, reason="loop_detected")


def process_graph_recursion_error(context: TaskRunContext) -> None:
    """LangGraph 达到递归上限时的统一失败处理。"""
    error_message = f"任务执行达到图递归上限（{context.config['recursion_limit']} 步），可能陷入重复调用，已自动停止。请缩小问题范围，或改用更明确的单一信息来源。"
    process_failure(context, error_message, reason="graph_recursion_limit")


def process_cancelled(context: TaskRunContext) -> None:
    """用户取消任务时，仍然生成反思和策略候选，便于后续优化。"""
    reflection = build_task_reflection(context.query, error="任务已被用户取消")
    append_task_event("task_cancelled", context.task_id, {"reason": "user_cancelled", "conversation_id": context.conversation_id})
    append_reflection(context.task_id, context.query, reflection["status"], reflection["summary"], reflection["lessons"])
    create_policy_proposal(context.task_id, context.query, reflection)
    monitor._emit("task_cancelled", "任务已取消")
    tracer.emit("agent_finished", trace_id=context.task_id, task_id=context.task_id, conversation_id=context.conversation_id, agent_name="main_agent", metadata={"status": "cancelled"})


def process_failure(context: TaskRunContext, error_message: str, reason: str = "runtime_error") -> None:
    """所有非取消失败的统一落盘和 trace 入口。"""
    reflection = build_task_reflection(context.query, error=error_message)
    payload = {"error": error_message, "conversation_id": context.conversation_id}
    if reason:
        payload["reason"] = reason
    append_task_event("task_failed", context.task_id, payload)
    append_reflection(context.task_id, context.query, reflection["status"], reflection["summary"], reflection["lessons"])
    create_policy_proposal(context.task_id, context.query, reflection)
    monitor._emit("error", error_message if reason != "runtime_error" else f"执行主智能发生异常信息：{error_message}")
    tracer.emit("agent_finished", trace_id=context.task_id, task_id=context.task_id, conversation_id=context.conversation_id, agent_name="main_agent", error=error_message, metadata={"status": "failed", "reason": reason})


async def _apply_critic_if_needed(
    context: TaskRunContext,
    final_result: str,
    *,
    agent_specs: Sequence[AgentSpec],
    rerun_with_fix: Optional[CriticRevisionRunner],
    task_classification: Optional[TaskClassification],
) -> tuple[str, CriticStatus]:
    """
    对高价值任务执行 Critic。

    策略由 agent.critic.policy 统一判断；Critic 未通过时最多触发一次受控修正重跑，避免形成
    Critic -> Agent -> Critic 的无限循环。
    """
    if not _critic_enabled() or not final_result:
        tracer.emit("critic_skipped", trace_id=context.task_id, task_id=context.task_id, conversation_id=context.conversation_id, agent_name="critic_agent", metadata={"stage": "critic", "status": "skipped", "reason": "disabled_or_empty"})
        return final_result, "skipped"

    policy_decision = evaluate_critic_policy(
        context.query,
        agent_specs=agent_specs,
        tool_calls=get_tool_calls_for_task(context.task_id),
        task_classification=task_classification,
    )
    tracer.emit(
        "critic_policy_evaluated",
        trace_id=context.task_id,
        task_id=context.task_id,
        conversation_id=context.conversation_id,
        agent_name="critic_agent",
        metadata=policy_decision.to_metadata(),
    )
    if not policy_decision.required:
        tracer.emit("critic_skipped", trace_id=context.task_id, task_id=context.task_id, conversation_id=context.conversation_id, agent_name="critic_agent", metadata={"stage": "critic", "status": "skipped", "reason": "policy_not_required"})
        return final_result, "skipped"

    critic_result = await run_critic(context.query, final_result, trace_id=context.task_id, task_id=context.task_id, conversation_id=context.conversation_id)
    if critic_result.passed:
        return final_result, "passed"

    max_revisions = int(os.getenv("CRITIC_MAX_REVISIONS", "1"))
    if rerun_with_fix and max_revisions > 0:
        revised_result = await _run_single_critic_revision(context, final_result, critic_result, rerun_with_fix)
        if revised_result:
            revised_critic_result = await run_critic(context.query, revised_result, trace_id=context.task_id, task_id=context.task_id, conversation_id=context.conversation_id)
            if revised_critic_result.passed:
                return revised_result, "passed"
            return _append_critic_issues(revised_result, revised_critic_result), "failed"

    return _append_critic_issues(final_result, critic_result), "failed"


async def _run_single_critic_revision(
    context: TaskRunContext,
    final_result: str,
    critic_result: CriticResult,
    rerun_with_fix: CriticRevisionRunner,
) -> str:
    """用 Critic 给出的修复指令重跑一次主 Agent，失败时回退到原始结果。"""
    fix_prompt = _build_critic_fix_prompt(context.query, final_result, critic_result)
    tracer.emit(
        "critic_revision_started",
        trace_id=context.task_id,
        task_id=context.task_id,
        conversation_id=context.conversation_id,
        agent_name="critic_agent",
        metadata={"max_revisions": int(os.getenv("CRITIC_MAX_REVISIONS", "1"))},
    )
    try:
        revised_result = await rerun_with_fix(fix_prompt)
        tracer.emit(
            "critic_revision_finished",
            trace_id=context.task_id,
            task_id=context.task_id,
            conversation_id=context.conversation_id,
            agent_name="critic_agent",
            metadata={"revised": bool(revised_result)},
        )
        return revised_result
    except Exception as error:
        tracer.emit(
            "critic_revision_failed",
            trace_id=context.task_id,
            task_id=context.task_id,
            conversation_id=context.conversation_id,
            agent_name="critic_agent",
            error=str(error)[:1000],
        )
        return final_result


def _build_critic_fix_prompt(task_query: str, final_result: str, critic_result: CriticResult) -> str:
    issue_lines = [f"- {issue.type}: {issue.message}" for issue in critic_result.issues]
    fix_instruction = critic_result.fix_instruction or "请补充缺失的数据来源、指标或结论依据。"
    return f"""
用户原始任务：
{task_query}

你上一次输出未通过 Critic 质量校验。请基于已有上下文和必要工具调用，最多做一次补充修正，输出完整的最终答案。

Critic 发现的问题：
{chr(10).join(issue_lines)}

修复指令：
{fix_instruction}

上一次输出：
{final_result[:6000]}
"""


def _append_critic_issues(final_result: str, critic_result: CriticResult) -> str:
    """修正不可用或修正后仍未通过时，把质量缺口显式追加到最终答案。"""
    issue_lines = [f"- {issue.type}: {issue.message}" for issue in critic_result.issues]
    final_result = (
        f"{final_result}\n\n"
        "【质量校验提示】\n"
        "Critic 检测到当前结果仍有待补充：\n"
        f"{chr(10).join(issue_lines)}\n"
        f"建议修复：{critic_result.fix_instruction or '请补充缺失的数据来源、指标或结论依据。'}"
    )
    monitor.report_task_result(final_result)
    return final_result


def _critic_enabled() -> bool:
    """Critic 默认启用；本地调试或压测时可通过 CRITIC_ENABLED=false 暂时关闭。"""
    return os.getenv("CRITIC_ENABLED", "true").lower() == "true"