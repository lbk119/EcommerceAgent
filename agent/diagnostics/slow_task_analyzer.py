"""慢任务诊断器。

基于 JSONL trace 给单个任务生成工程可读的慢因结论。它不依赖数据库状态，适合本地开发、线上应急和
前端“为什么慢”面板复用。
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Any

from agent.observability.trace_reader import list_task_traces


def diagnose_task(task_id: str) -> dict[str, Any]:
    """返回单任务慢因诊断。"""
    events = list_task_traces(task_id)
    if not events:
        return {"taskId": task_id, "found": False, "recommendations": ["未找到 trace 事件，请确认 taskId 是否正确或 trace 写入是否开启。"]}

    timestamps = [_parse_timestamp(event.get("timestamp")) for event in events]
    timestamps = [timestamp for timestamp in timestamps if timestamp]
    total_latency_ms = round((max(timestamps) - min(timestamps)).total_seconds() * 1000, 2) if len(timestamps) >= 2 else 0
    event_types = [str(event.get("event_type") or "") for event in events]
    metadata_list = [event.get("metadata") or {} for event in events]
    model_calls = sum(1 for event_type in event_types if event_type == "llm_call_started")
    tool_calls = sum(1 for event_type in event_types if event_type == "tool_call_started")
    subagent_calls = sum(1 for metadata in metadata_list if metadata.get("tool_name") == "task")
    critic_events = [event for event in events if str(event.get("event_type") or "").startswith("critic")]
    memory_events = [event for event in events if "memory" in str(event.get("event_type") or "")]
    slowest = _slowest_events(events)
    bottleneck = slowest[0]["stage"] if slowest else "unknown"
    recommendations = _recommendations(events, model_calls, tool_calls, subagent_calls, critic_events)

    return {
        "taskId": task_id,
        "found": True,
        "totalLatencyMs": total_latency_ms,
        "bottleneck": bottleneck,
        "modelCalls": model_calls,
        "toolCalls": tool_calls,
        "subagentCalls": subagent_calls,
        "criticEnabled": bool(critic_events and not any((event.get("metadata") or {}).get("status") == "skipped" for event in critic_events)),
        "criticEventCount": len(critic_events),
        "memoryEventCount": len(memory_events),
        "memoryWriteMs": _sum_latency(memory_events),
        "retryTriggered": any("retry" in event_type or "revision" in event_type for event_type in event_types),
        "fallbackTriggered": any((event.get("metadata") or {}).get("fallback_reason") or "fallback" in str(event.get("event_type") or "") for event in events),
        "budgetExceeded": any(event.get("event_type") == "budget_exceeded" for event in events),
        "slowestStages": slowest,
        "eventTypeCounts": dict(Counter(event_types).most_common(20)),
        "recommendations": recommendations,
    }


def _slowest_events(events: list[dict[str, Any]], limit: int = 5) -> list[dict[str, Any]]:
    candidates = []
    for event in events:
        metadata = event.get("metadata") or {}
        latency_ms = event.get("latency_ms") or metadata.get("latency_ms")
        if latency_ms is None:
            continue
        candidates.append({
            "eventType": event.get("event_type"),
            "agentName": event.get("agent_name"),
            "stage": _stage_name(event),
            "latencyMs": float(latency_ms),
            "timestamp": event.get("timestamp"),
        })
    candidates.sort(key=lambda item: item["latencyMs"], reverse=True)
    return candidates[:limit]


def _recommendations(events: list[dict[str, Any]], model_calls: int, tool_calls: int, subagent_calls: int, critic_events: list[dict[str, Any]]) -> list[str]:
    recommendations: list[str] = []
    event_types = [str(event.get("event_type") or "") for event in events]
    metadata_list = [event.get("metadata") or {} for event in events]
    if any(metadata.get("workflow_name") for metadata in metadata_list) and any("deepagent" in event_type.lower() for event_type in event_types):
        recommendations.append("该任务已命中 deterministic workflow，不应再 fallback 完整 DeepAgent。")
    if model_calls > 2:
        recommendations.append(f"模型调用次数为 {model_calls}，超过 standard profile 建议上限 2，可改为 workflow + 一次总结。")
    if tool_calls > 6:
        recommendations.append(f"工具调用次数为 {tool_calls}，超过 standard profile 建议上限 6，需要收紧工具预算或固定查询节点。")
    if subagent_calls > 1:
        recommendations.append(f"子 Agent 调用次数为 {subagent_calls}，建议只在 deep profile 允许多 subagent。")
    if any((event.get("metadata") or {}).get("tool_name") == "internet_search" for event in events):
        recommendations.append("任务调用了网络搜索；如果用户未明确要求外部信息，应在 standard/realtime 禁用。")
    if any(event.get("event_type") == "critic_revision_started" for event in critic_events):
        recommendations.append("Critic revision 触发了额外重跑，普通分析建议可关闭 Critic rerun。")
    if any(event.get("event_type") == "budget_exceeded" for event in events):
        recommendations.append("任务触发了执行预算，建议拆分问题或转 deep 后台任务。")
    if not recommendations:
        recommendations.append("未发现明显超预算调用；请重点查看 slowestStages 中最高耗时阶段。")
    return recommendations


def _sum_latency(events: list[dict[str, Any]]) -> float:
    total = 0.0
    for event in events:
        metadata = event.get("metadata") or {}
        total += float(event.get("latency_ms") or metadata.get("latency_ms") or 0)
    return round(total, 2)


def _stage_name(event: dict[str, Any]) -> str:
    metadata = event.get("metadata") or {}
    event_type = str(event.get("event_type") or "unknown")
    for key in ("stage", "step_name", "tool_name", "workflow_name"):
        if metadata.get(key):
            return f"{event_type}:{metadata[key]}"
    return event_type


def _parse_timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
