"""
JSONL trace 查询与聚合。

写入仍由 JsonlTracer 负责；这里提供只读视图，供 FastAPI 暴露任务 trace、时间线和 Agent 指标。
先基于本地 JSONL 实现，未来迁移到 ClickHouse/MySQL 时保持返回结构即可。
"""

import json
from collections import Counter, defaultdict
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
    tokens_by_agent: Dict[str, Dict[str, int]] = defaultdict(lambda: {"input": 0, "output": 0})

    for event in _iter_trace_events():
        agent_name = event.get("agent_name") or "unknown"
        metadata = event.get("metadata") or {}
        agent_counter[agent_name] += 1
        if metadata.get("tool_name"):
            tool_counter[str(metadata["tool_name"])] += 1
        if event.get("error"):
            failures[agent_name] += 1
        if event.get("latency_ms") is not None:
            latency_by_agent[agent_name].append(float(event["latency_ms"]))
        tokens_by_agent[agent_name]["input"] += int(event.get("token_input") or 0)
        tokens_by_agent[agent_name]["output"] += int(event.get("token_output") or 0)

    return {
        "agents": [
            {
                "agent_name": agent_name,
                "event_count": count,
                "failure_count": failures[agent_name],
                "avg_latency_ms": _avg(latency_by_agent[agent_name]),
                "token_input": tokens_by_agent[agent_name]["input"],
                "token_output": tokens_by_agent[agent_name]["output"],
            }
            for agent_name, count in agent_counter.most_common()
        ],
        "tools": [{"tool_name": tool_name, "call_count": count} for tool_name, count in tool_counter.most_common()],
        "dropped_trace_events": tracer.dropped_count,
    }


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