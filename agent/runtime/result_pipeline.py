"""任务结果后处理流水线。

Agent 得到 final_result 之后，还需要完成一组“质量和记忆闭环”：
- 按需运行 Evaluation；
- 生成任务反思；
- 创建策略建议；
- 写入长期记忆候选；
- 写入 task_events 和 trace。

这些步骤不属于 DeepAgents 图本身。拆出来后，主入口不再混杂执行流、反思、记忆和观测逻辑。
"""

import asyncio
import os
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Literal, Optional, Sequence

from api.monitor import monitor
from agent.evaluation.evaluation_agent import EvaluationResult, run_evaluation
from agent.evaluation.evaluation_policy import evaluate_evaluation_policy
from agent.reflection.policy_review import create_policy_proposal
from agent.reflection.reflection import build_task_reflection
from agent.reflection.evolution_log import append_reflection, append_task_event
from agent.plan.models import AgentTaskPlan
from agent.trace.tracer import tracer
from agent.runtime.task_context import TaskRunContext
from agent.security.redaction import redact_secrets


EvaluationRevisionRunner = Callable[[str], Awaitable[str]]
EvaluationStatus = Literal["skipped", "passed", "failed"]


@dataclass(frozen=True)
class EvaluationStageResult:
    """Evaluation 阶段输出，包含最终文本和质量校验状态。"""

    content: str
    evaluation_status: EvaluationStatus


async def run_evaluation_stage(
    context: TaskRunContext,
    final_result: str,
    *,
    agent_specs: Optional[Sequence[Any]] = None,
    rerun_with_fix: Optional[EvaluationRevisionRunner] = None,
    task_plan: Optional[AgentTaskPlan] = None,
) -> EvaluationStageResult:
    """
    质量校验阶段。

    AgentRuntime 会直接调用这个阶段。这里统一承接 Evaluation retry、未来多维质量校验器，
    都可以在一个边界内替换，而不会影响结果持久化和记忆写入。
    """
    content, evaluation_status = await _apply_evaluation_if_needed(
        context,
        final_result,
        agent_specs=agent_specs or [],
        rerun_with_fix=rerun_with_fix,
        task_plan=task_plan,
    )
    return EvaluationStageResult(content=content, evaluation_status=evaluation_status)


def persist_result(context: TaskRunContext, final_result: str, *, runtime_profile: str = "deep") -> dict:
    """
    结果持久化阶段。

    这里写 task_events、reflection 和 policy proposal。它不写长期记忆，也不结束 trace，保持每个
    阶段的副作用单一，DeepAgents 主链路和后台任务共用同一套落盘逻辑。
    """
    started_at = time.perf_counter()
    tracer.emit("persistence_started", trace_id=context.task_id, task_id=context.task_id, conversation_id=context.conversation_id, agent_name="result_pipeline", metadata={"stage": "persistence", "status": "running"})
    safe_result = redact_secrets(final_result)
    append_task_event("task_completed", context.task_id, {"result": safe_result, "conversation_id": context.conversation_id})
    if runtime_profile == "deep":
        # 反思和策略进化只放在 deep 路径，避免 AI Chat/standard 每次任务都写重型副产物。
        reflection = build_task_reflection(context.query, result=safe_result)
        append_reflection(context.task_id, context.query, reflection["status"], reflection["summary"], reflection["lessons"])
        create_policy_proposal(context.task_id, context.query, reflection)
    else:
        reflection = {"status": "deferred", "summary": "reflection and policy proposal moved out of hot path", "lessons": []}
        tracer.emit("result_enrichment_deferred", trace_id=context.task_id, task_id=context.task_id, conversation_id=context.conversation_id, agent_name="result_pipeline", metadata={"runtime_profile": runtime_profile, "deferred": ["reflection", "policy_proposal"]})
    tracer.emit("persistence_finished", trace_id=context.task_id, task_id=context.task_id, conversation_id=context.conversation_id, agent_name="result_pipeline", latency_ms=round((time.perf_counter() - started_at) * 1000, 2), metadata={"stage": "persistence", "status": "completed"})
    return reflection


def write_memory(context: TaskRunContext, final_result: str, reflection: dict, execution_metadata: Optional[dict] = None) -> dict:
    """外层记忆写入占位。

    DeepAgents store 是唯一长期记忆后端。外层 runtime 不再把抽取候选写入单独的 MySQL memory store。
    """
    result = {"backend": "deepagents_store", "written": 0, "skipped": 1, "reason": "outer_mysql_memory_removed"}
    tracer.emit(
        "memory_write_skipped",
        trace_id=context.task_id,
        task_id=context.task_id,
        conversation_id=context.conversation_id,
        agent_name="memory_writer",
        metadata={"stage": "memory_write", "status": "skipped", **result},
    )
    return result


def finalize_success_trace(context: TaskRunContext) -> None:
    """成功收尾阶段：只负责把主 Agent trace 标记为 succeeded。"""
    tracer.emit("agent_finished", trace_id=context.task_id, task_id=context.task_id, conversation_id=context.conversation_id, agent_name="main_agent", metadata={"status": "succeeded"})


def process_loop_failure(context: TaskRunContext, loop_error: Exception) -> None:
    """循环检测终止后的统一失败处理。"""
    error_message = f"检测到重复调用，已停止任务。{getattr(loop_error, 'summary', str(loop_error))}"
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


async def _apply_evaluation_if_needed(
    context: TaskRunContext,
    final_result: str,
    *,
    agent_specs: Sequence[Any],
    rerun_with_fix: Optional[EvaluationRevisionRunner],
    task_plan: Optional[AgentTaskPlan],
) -> tuple[str, EvaluationStatus]:
    """
    对高价值任务执行 Evaluation。

    策略由 agent.evaluation.evaluation_policy 统一判断；Evaluation 未通过时最多触发一次受控修正重跑，避免形成
    Evaluation -> Agent -> Evaluation 的无限循环。
    """
    if not _evaluation_enabled() or not final_result:
        # 没有内容时跳过 Evaluation，否则只能检查空文本，反而制造噪声。
        tracer.emit("evaluation_skipped", trace_id=context.task_id, task_id=context.task_id, conversation_id=context.conversation_id, agent_name="evaluation_agent", metadata={"stage": "evaluation", "status": "skipped", "reason": "disabled_or_empty"})
        return final_result, "skipped"

    policy_decision = evaluate_evaluation_policy(
        context.query,
        agent_specs=agent_specs,
        tool_calls=get_tool_calls_for_task(context.task_id),
        task_plan=task_plan,
    )
    tracer.emit(
        "evaluation_policy_evaluated",
        trace_id=context.task_id,
        task_id=context.task_id,
        conversation_id=context.conversation_id,
        agent_name="evaluation_agent",
        metadata=policy_decision.to_metadata(),
    )
    if not policy_decision.required:
        tracer.emit("evaluation_skipped", trace_id=context.task_id, task_id=context.task_id, conversation_id=context.conversation_id, agent_name="evaluation_agent", metadata={"stage": "evaluation", "status": "skipped", "reason": "policy_not_required"})
        return final_result, "skipped"

    evaluation_result = await run_evaluation(context.query, final_result, trace_id=context.task_id, task_id=context.task_id, conversation_id=context.conversation_id)
    if evaluation_result.passed:
        return final_result, "passed"

    max_revisions = int(os.getenv("EVALUATION_MAX_REVISIONS", "1"))
    if rerun_with_fix and max_revisions > 0:
        # 只允许一次受控修正，防止 Evaluation 与 Agent 形成互相重跑的循环。
        revised_result = await _run_single_evaluation_revision(context, final_result, evaluation_result, rerun_with_fix)
        if revised_result:
            revised_evaluation_result = await run_evaluation(context.query, revised_result, trace_id=context.task_id, task_id=context.task_id, conversation_id=context.conversation_id)
            if revised_evaluation_result.passed:
                return revised_result, "passed"
            return _append_evaluation_issues(revised_result, revised_evaluation_result), "failed"

    return _append_evaluation_issues(final_result, evaluation_result), "failed"


async def _run_single_evaluation_revision(
    context: TaskRunContext,
    final_result: str,
    evaluation_result: EvaluationResult,
    rerun_with_fix: EvaluationRevisionRunner,
) -> str:
    """按 Evaluation 给出的修复指令重跑一次主 Agent，失败时回退到原始结果。"""
    fix_prompt = _build_evaluation_fix_prompt(context.query, final_result, evaluation_result)
    tracer.emit(
        "evaluation_revision_started",
        trace_id=context.task_id,
        task_id=context.task_id,
        conversation_id=context.conversation_id,
        agent_name="evaluation_agent",
        metadata={"max_revisions": int(os.getenv("EVALUATION_MAX_REVISIONS", "1"))},
    )
    try:
        revised_result = await rerun_with_fix(fix_prompt)
        tracer.emit(
            "evaluation_revision_finished",
            trace_id=context.task_id,
            task_id=context.task_id,
            conversation_id=context.conversation_id,
            agent_name="evaluation_agent",
            metadata={"revised": bool(revised_result)},
        )
        return revised_result
    except Exception as error:
        tracer.emit(
            "evaluation_revision_failed",
            trace_id=context.task_id,
            task_id=context.task_id,
            conversation_id=context.conversation_id,
            agent_name="evaluation_agent",
            error=str(error)[:1000],
        )
        return final_result


def _build_evaluation_fix_prompt(task_query: str, final_result: str, evaluation_result: EvaluationResult) -> str:
    """把 Evaluation 发现的问题转换成一次性修复提示。"""
    issue_lines = [f"- {issue.type}: {issue.message}" for issue in evaluation_result.issues]
    fix_instruction = evaluation_result.fix_instruction or "请补充缺失的数据来源、指标或结论依据。"
    return f"""
用户原始任务：
{task_query}

你上一次输出未通过 Evaluation 质量校验。请基于已有上下文和必要工具调用，最多做一次补充修正，输出完整的最终答案。

Evaluation 发现的问题：
{chr(10).join(issue_lines)}

修复指令：
{fix_instruction}

上一次输出：
{final_result[:6000]}
"""


def _append_evaluation_issues(final_result: str, evaluation_result: EvaluationResult) -> str:
    """修正不可用或修正后仍未通过时，把质量缺口显式追加到最终答案。"""
    issue_lines = [f"- {issue.type}: {issue.message}" for issue in evaluation_result.issues]
    final_result = (
        f"{final_result}\n\n"
        "【质量校验提示】\n"
        "Evaluation 检测到当前结果仍有待补充：\n"
        f"{chr(10).join(issue_lines)}\n"
        f"建议修复：{evaluation_result.fix_instruction or '请补充缺失的数据来源、指标或结论依据。'}"
    )
    monitor.report_task_result(final_result)
    return final_result


def _evaluation_enabled() -> bool:
    """Evaluation 默认启用；本地调试或压测时可通过 EVALUATION_ENABLED=false 暂时关闭。"""
    return os.getenv("EVALUATION_ENABLED", "true").lower() == "true"


def get_tool_calls_for_task(task_id: str) -> list[dict[str, Any]]:
    """deepagents-native 通过 trace 输出工具元数据；进程内旧缓存已移除。"""
    return []
