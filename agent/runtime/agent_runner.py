"""
DeepAgents 运行器。

这个模块只负责“如何跑主 Agent 图”：
- 处理 DeepAgents astream 输出；
- 把工具/子 Agent 调用推给前端 monitor；
- 用 AgentLoopGuard 做重复调用检测；
- 需要人工介入时暂停任务，等待 resume；
- 在循环检测后按配置注入反思提示重试。

它不处理任务目录、长期记忆写入、Critic、策略反思等后处理逻辑，那些属于 task_context 和
result_pipeline。
"""

import os
import time
from typing import Any, Dict, List

from api.monitor import monitor
from api.task_runtime import task_runtime
from agent.core.runtime_context import current_runtime_context
from agent.core.tool_registry import tool_registry
from agent.loop_guard import AgentLoopGuard, evaluate_loop_with_supervisor
from agent.memory.evolution_memory import append_task_event
from agent.observability.tracer import tracer


# tool_call_started 和 tool_call_finished 来自 DeepAgents 流里的不同节点：
# - model 节点给出 tool_calls，此时能拿到 tool_call id 和调用参数；
# - tools 节点返回 ToolMessage，此时能拿到 tool_call_id 和工具结果。
# 用这个轻量内存表把二者关联起来，便于计算单个工具调用耗时。
_tool_call_starts = {}
_task_tool_calls: Dict[str, List[Dict[str, Any]]] = {}


class LoopDetectedError(Exception):
    """用异常打断当前 LangGraph 流，由外层决定反思重试、人工恢复或失败。"""

    def __init__(self, summary: str, decision: str = "reflect"):
        super().__init__("检测到可能的重复调用")
        self.summary = summary
        self.decision = decision


def build_reflection_prompt(summary: str) -> str:
    """把循环检测摘要转换成下一轮重试时注入给主 Agent 的反思提示。"""
    return f"""

【循环检测反思提示】
系统检测到你可能正在重复调用相同工具或助手，或者连续调用过多但还没有形成最终答案。

{summary}

请立即执行以下策略：
1. 不要重复最近这些相同的调用。
2. 优先基于已有信息给出阶段性结论。
3. 如果确实缺少关键信息，只能再选择一个最有价值的助手或工具进行一次补充。
4. 若补充后仍不足，请明确告诉用户缺少什么信息，而不是继续循环调用。
"""


async def run_agent_with_reflection(agent, task_query: str, path_instruction: str, config: dict, task_id: str) -> str:
    """
    执行主 Agent，并在循环检测后按配置进行有限次反思重试。

    这里返回最终文本结果。若人工选择 abort 或超过重试次数，会抛 LoopDetectedError，交给
    result_pipeline 统一记录失败、生成反思和策略建议。
    """
    reflection_prompt = ""
    max_reflection_retries = int(os.getenv("AGENT_LOOP_REFLECTION_RETRIES", "1"))
    for attempt in range(max_reflection_retries + 1):
        try:
            return await _run_agent_stream(agent, task_query + path_instruction + reflection_prompt, config, task_id)
        except LoopDetectedError as loop_error:
            if loop_error.decision == "abort" or attempt >= max_reflection_retries:
                raise

            reflection_prompt = build_reflection_prompt(loop_error.summary)
            append_task_event("loop_reflection", task_id, {"summary": loop_error.summary, "conversation_id": config["configurable"]["thread_id"]})
            monitor._emit("loop_reflection", "已根据人工决策恢复任务", {
                "summary": loop_error.summary,
                "decision": loop_error.decision,
            })
    return ""


def get_tool_calls_for_task(task_id: str) -> List[Dict[str, Any]]:
    """返回本轮任务已观测到的工具调用摘要，供 Critic 策略消费。"""
    return list(_task_tool_calls.get(task_id, []))


def clear_tool_calls_for_task(task_id: str) -> None:
    """任务结束后清理工具调用摘要，避免长进程内存无限增长。"""
    _task_tool_calls.pop(task_id, None)


async def wait_for_human_interrupt(task_id: str, summary: str, suggested_decision: str = "reflect"):
    """暂停当前任务，等待 resume 接口写入 continue/revise/abort 决策。"""
    monitor._emit("human_interrupt", "检测到可能的循环，等待人工决策", {
        "summary": summary,
        "suggested_decision": suggested_decision,
        "options": ["continue", "revise", "abort"],
    })
    append_task_event("human_interrupt", task_id, {"summary": summary, "suggested_decision": suggested_decision})
    return await task_runtime.interrupt(task_id, "loop_detected", summary)


async def _resolve_loop_detection(task_id: str, summary: str, suggested_decision: str):
    """把人工恢复决策转换为继续执行、反思重试或终止异常。"""
    decision_payload = await wait_for_human_interrupt(task_id, summary, suggested_decision)
    decision = decision_payload.get("decision", "abort")
    instruction = decision_payload.get("instruction", "")

    if decision == "continue":
        monitor._emit("loop_resume", "人工选择继续当前执行", {"instruction": instruction})
        return

    if decision == "revise":
        revised_summary = summary
        if instruction:
            revised_summary += f"\n\n人工补充策略：{instruction}"
        raise LoopDetectedError(revised_summary, "reflect")

    raise LoopDetectedError(summary, "abort")


async def _run_agent_stream(agent, user_content: str, config: dict, task_id: str) -> str:
    """消费 DeepAgents astream 输出，提取最终结果并上报工具/子 Agent 事件。"""
    final_result = ""
    last_non_empty_content = ""
    loop_guard = AgentLoopGuard()
    recursion_limit = config.get("recursion_limit")

    async for chunk in agent.astream({"messages": [{"role": "user", "content": user_content}]}, config=config):
        for node_name, state in chunk.items():
            if not state or "messages" not in state:
                continue
            messages = state["messages"]
            if not messages or not isinstance(messages, list):
                continue

            last_msg = messages[-1]
            if getattr(last_msg, "content", None):
                last_non_empty_content = last_msg.content if isinstance(last_msg.content, str) else str(last_msg.content)

            if node_name == "model":
                final_result = await _handle_model_message(last_msg, loop_guard, recursion_limit, task_id, final_result)
            elif node_name == "tools":
                _trace_tool_call_finished(last_msg, node_name)

    if not final_result and last_non_empty_content:
        final_result = last_non_empty_content
        monitor.report_task_result(final_result)
    return final_result


async def _handle_model_message(last_msg, loop_guard: AgentLoopGuard, recursion_limit: int, task_id: str, final_result: str) -> str:
    """处理 model 节点输出：要么是工具调用，要么是最终回答。"""
    if getattr(last_msg, "tool_calls", None):
        for tool_call in last_msg.tool_calls:
            await _guard_tool_call(loop_guard, tool_call, recursion_limit, task_id)
            if tool_call["name"] == "task":
                monitor.report_assistant(tool_call["args"]["subagent_type"], {"description": tool_call["args"]["description"]})
            _trace_tool_call_started(tool_call, "model")
        return final_result

    if getattr(last_msg, "content", None):
        print(f"主智能体执行结果，最终结果：{last_msg.content[:100]}")
        monitor.report_task_result(last_msg.content)
        return last_msg.content
    return final_result


async def _guard_tool_call(loop_guard: AgentLoopGuard, tool_call: dict, recursion_limit: int, task_id: str) -> None:
    """对每次工具/子 Agent 调用做循环检测和监督模型兜底判断。"""
    loop_summary = loop_guard.record_tool_call(tool_call)
    if loop_summary:
        await _resolve_loop_detection(task_id, loop_summary, "reflect")

    if loop_guard.should_supervise(recursion_limit):
        supervisor_summary = loop_guard.summary("轻量监督器按调用步数触发检查")
        decision, reason = await evaluate_loop_with_supervisor(supervisor_summary)
        monitor._emit("loop_supervisor", f"监督器判断: {decision} - {reason}", {
            "decision": decision,
            "reason": reason,
            "summary": supervisor_summary,
        })
        if decision == "reflect":
            await _resolve_loop_detection(task_id, f"监督器建议反思：{reason}\n{supervisor_summary}", "reflect")
        if decision == "abort":
            await _resolve_loop_detection(task_id, f"监督器建议终止：{reason}\n{supervisor_summary}", "abort")


def _trace_tool_call_started(tool_call: dict, node_name: str) -> None:
    """
    记录模型决定调用工具/子 Agent 的时刻，并附上 ToolRegistry 中的风险和权限元数据。

    tool_call_id 是 started/finished 的关联键；如果 provider 没给 id，则退化为 name + 当前时间，
    仍然保证 trace 里有可读的 correlation id。
    """
    context = current_runtime_context()
    tool_name = tool_call.get("name", "unknown")
    started_at = time.time()
    tool_call_id = _tool_call_id(tool_call, tool_name, started_at)
    _tool_call_starts[tool_call_id] = {"started_at": started_at, "tool_name": tool_name, "node_name": node_name}
    metadata = {
        "tool_call_id": tool_call_id,
        "tool_name": tool_name,
        "node_name": node_name,
        "started_at": started_at,
        "args": tool_call.get("args", {}),
    }
    if tool_name in tool_registry._specs:
        spec = tool_registry.get_spec(tool_name)
        metadata.update({
            "category": spec.category,
            "risk": spec.risk,
            "permissions": spec.permissions,
            "requires_human_approval": spec.requires_human_approval,
        })
    if context.task_id:
        _task_tool_calls.setdefault(context.task_id, []).append(metadata.copy())
    tracer.emit("tool_call_started", trace_id=context.trace_id, task_id=context.task_id, conversation_id=context.conversation_id, agent_name="main_agent", metadata=metadata)


def _trace_tool_call_finished(message, node_name: str) -> None:
    """记录工具节点返回。只保存内容预览，避免 trace 文件沉淀完整业务数据。"""
    context = current_runtime_context()
    content = getattr(message, "content", "")
    tool_name = getattr(message, "name", None) or "unknown"
    finished_at = time.time()
    tool_call_id = getattr(message, "tool_call_id", None) or getattr(message, "id", None) or f"{tool_name}:{finished_at}"
    start_info = _tool_call_starts.pop(tool_call_id, None)
    started_at = start_info.get("started_at") if start_info else None
    latency_ms = round((finished_at - started_at) * 1000, 2) if started_at else None
    tracer.emit(
        "tool_call_finished",
        trace_id=context.trace_id,
        task_id=context.task_id,
        conversation_id=context.conversation_id,
        agent_name="main_agent",
        latency_ms=latency_ms,
        metadata={
            "tool_call_id": tool_call_id,
            "tool_name": tool_name,
            "node_name": node_name,
            "started_at": started_at,
            "finished_at": finished_at,
            "latency_ms": latency_ms,
            "content_preview": str(content)[:500],
        },
    )


def _tool_call_id(tool_call: dict, tool_name: str, started_at: float) -> str:
    """从 LangChain tool_call dict 中提取稳定 id；缺失时生成可读 fallback。"""
    return str(tool_call.get("id") or tool_call.get("tool_call_id") or f"{tool_name}:{started_at}")