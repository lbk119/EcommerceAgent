"""
JSONL trace 查询与聚合。

写入仍由 JsonlTracer 负责；这里提供只读视图，供 FastAPI 暴露任务 trace、时间线和 Agent 指标。
先基于本地 JSONL 实现，未来迁移到 ClickHouse/MySQL 时保持返回结构即可。
"""

import json
from collections import Counter, defaultdict
from datetime import datetime
from typing import Any, Dict, List

from agent.observability.tracer import DEFAULT_TRACE_PATH, tracer


def list_task_traces(task_id: str) -> List[Dict[str, Any]]:
    """读取单个 task_id 的原始 trace 事件，按文件顺序返回。"""
    return [event for event in _iter_trace_events() if event.get("task_id") == task_id or event.get("trace_id") == task_id]


def build_task_timeline(task_id: str) -> Dict[str, Any]:
    """构造前端时间线视图：事件类型、Agent、工具、workflow 关键字段、耗时、错误和时间戳。"""
    events = list_task_traces(task_id)
    timeline = []
    for event in events:
        metadata = event.get("metadata") or {}
        timeline.append({
            "timestamp": event.get("timestamp"),
            "event_type": event.get("event_type"),
            "agent_name": event.get("agent_name"),
            "tool_name": metadata.get("tool_name"),
            "node_name": metadata.get("node_name"),
            "workflow_name": metadata.get("workflow_name"),
            "executor": metadata.get("executor"),
            "structured": metadata.get("structured"),
            "step_count": metadata.get("step_count"),
            "profile": metadata.get("profile"),
            "attempted_workflow": metadata.get("attempted_workflow"),
            "workflow_failed": metadata.get("workflow_failed"),
            "fallback_reason": metadata.get("fallback_reason"),
            "step_name": metadata.get("step_name"),
            "required": metadata.get("required"),
            "time_range": metadata.get("time_range"),
            "section_errors": metadata.get("section_errors"),
            "critic_status": metadata.get("critic_status"),
            "latency_ms": event.get("latency_ms") or metadata.get("latency_ms"),
            "token_input": event.get("token_input"),
            "token_output": event.get("token_output"),
            "error": event.get("error"),
        })
    return {"task_id": task_id, "events": timeline}


def build_agent_metrics() -> Dict[str, Any]:
    """汇总 Agent/工具维度的调用次数、失败次数、token 和耗时。"""
    agent_counter: Counter[str] = Counter()
    tool_counter: Counter[str] = Counter()
    failures: Counter[str] = Counter()
    latency_by_agent: Dict[str, List[float]] = defaultdict(list)
    latency_by_stage: Dict[str, List[float]] = defaultdict(list)
    workflow_latencies: List[float] = []
    llm_latencies: List[float] = []
    deepagent_latencies: List[float] = []
    tokens_by_agent: Dict[str, Dict[str, int]] = defaultdict(lambda: {"input": 0, "output": 0})
    task_timestamps: Dict[str, List[datetime]] = defaultdict(list)

    for event in _iter_trace_events():
        agent_name = event.get("agent_name") or "unknown"
        metadata = event.get("metadata") or {}
        stage_name = _stage_name(event)
        agent_counter[agent_name] += 1
        if metadata.get("tool_name"):
            tool_counter[str(metadata["tool_name"])] += 1
        if event.get("error"):
            failures[agent_name] += 1
        if event.get("latency_ms") is not None:
            latency = float(event["latency_ms"])
            latency_by_agent[agent_name].append(latency)
            latency_by_stage[stage_name].append(latency)
            event_type = str(event.get("event_type") or "")
            if event_type.startswith("workflow"):
                workflow_latencies.append(latency)
            if event_type.startswith("llm_call"):
                llm_latencies.append(latency)
            if "deep" in agent_name.lower() or "deepagent" in event_type.lower():
                deepagent_latencies.append(latency)
        task_id = event.get("task_id") or event.get("trace_id")
        timestamp = _parse_timestamp(event.get("timestamp"))
        if task_id and timestamp:
            task_timestamps[str(task_id)].append(timestamp)
        tokens_by_agent[agent_name]["input"] += int(event.get("token_input") or 0)
        tokens_by_agent[agent_name]["output"] += int(event.get("token_output") or 0)

    task_durations = [
        (max(timestamps) - min(timestamps)).total_seconds() * 1000
        for timestamps in task_timestamps.values()
        if len(timestamps) >= 2
    ]

    return {
        "agents": [
            {
                "agent_name": agent_name,
                "event_count": count,
                "failure_count": failures[agent_name],
                "avg_latency_ms": _avg(latency_by_agent[agent_name]),
                "p50_latency_ms": _percentile(latency_by_agent[agent_name], 50),
                "p95_latency_ms": _percentile(latency_by_agent[agent_name], 95),
                "token_input": tokens_by_agent[agent_name]["input"],
                "token_output": tokens_by_agent[agent_name]["output"],
            }
            for agent_name, count in agent_counter.most_common()
        ],
        "tools": [{"tool_name": tool_name, "call_count": count} for tool_name, count in tool_counter.most_common()],
        "task_latency": {
            "count": len(task_durations),
            "avg_ms": _avg(task_durations),
            "p50_ms": _percentile(task_durations, 50),
            "p95_ms": _percentile(task_durations, 95),
        },
        "chat_latency": _latency_bucket(task_durations),
        "workflow_latency": _latency_bucket(workflow_latencies),
        "llm_latency": _latency_bucket(llm_latencies),
        "deepagent_latency": _latency_bucket(deepagent_latencies),
        "slow_stages": [
            {
                "stage": stage,
                "sample_count": len(values),
                "avg_latency_ms": _avg(values),
                "p50_latency_ms": _percentile(values, 50),
                "p95_latency_ms": _percentile(values, 95),
            }
            for stage, values in sorted(latency_by_stage.items(), key=lambda item: _percentile(item[1], 95) or 0, reverse=True)[:10]
        ],
        "slow_tasks": build_slow_tasks(limit=20)["tasks"],
        "dropped_trace_events": tracer.dropped_count,
    }


def build_slow_tasks(limit: int = 20) -> Dict[str, Any]:
    """返回最近的慢任务，供运营和工程侧定位 15 秒以上的 AI Chat/AgentRuntime 问题。"""
    task_events: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for event in _iter_trace_events():
        task_id = event.get("task_id") or event.get("trace_id")
        if task_id:
            task_events[str(task_id)].append(event)
    tasks = []
    for task_id, events in task_events.items():
        timestamps = [_parse_timestamp(event.get("timestamp")) for event in events]
        timestamps = [timestamp for timestamp in timestamps if timestamp]
        if len(timestamps) < 2:
            continue
        duration_ms = round((max(timestamps) - min(timestamps)).total_seconds() * 1000, 2)
        slow_stage = _slowest_event(events)
        latest_event = max(events, key=lambda event: str(event.get("timestamp") or ""))
        tasks.append({
            "task_id": task_id,
            "conversation_id": latest_event.get("conversation_id"),
            "duration_ms": duration_ms,
            "event_count": len(events),
            "last_event_type": latest_event.get("event_type"),
            "last_timestamp": latest_event.get("timestamp"),
            "slowest_stage": slow_stage,
        })
    tasks.sort(key=lambda task: (task["duration_ms"], task.get("last_timestamp") or ""), reverse=True)
    return {"tasks": tasks[:limit]}


def _iter_trace_events():
    path = tracer.path if getattr(tracer, "path", None) else DEFAULT_TRACE_PATH
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as file:
        for line in file:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def _avg(values: List[float]) -> float | None:
    if not values:
        return None
    return round(sum(values) / len(values), 2)


def _percentile(values: List[float], percentile: int) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = round((len(ordered) - 1) * percentile / 100)
    return round(ordered[index], 2)


def _latency_bucket(values: List[float]) -> Dict[str, Any]:
    return {"count": len(values), "avg_ms": _avg(values), "p50_ms": _percentile(values, 50), "p95_ms": _percentile(values, 95)}


def _slowest_event(events: List[Dict[str, Any]]) -> Dict[str, Any] | None:
    candidates = [event for event in events if event.get("latency_ms") is not None]
    if not candidates:
        return None
    event = max(candidates, key=lambda item: float(item.get("latency_ms") or 0))
    return {"event_type": event.get("event_type"), "agent_name": event.get("agent_name"), "latency_ms": event.get("latency_ms"), "stage": _stage_name(event)}


def _stage_name(event: Dict[str, Any]) -> str:
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