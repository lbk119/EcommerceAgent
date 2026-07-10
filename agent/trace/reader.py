"""JSONL trace 查询与聚合。

写入仍由 `JsonlTracer` 负责；这里提供只读视图，供 FastAPI 暴露任务 timeline、Agent 指标和慢任务诊断。
AI Chat timeline 是热路径，因此单任务查询默认只扫描最近 trace 尾部，避免本地 JSONL 文件变大后阻塞请求。
"""

import json
from collections import Counter, defaultdict
from datetime import datetime
from time import perf_counter
from typing import Any, Dict, List

from agent.trace.tracer import DEFAULT_TRACE_PATH, tracer


def list_task_traces(task_id: str, *, max_events: int = 8000, max_bytes: int = 4_000_000) -> List[Dict[str, Any]]:
    """读取单个任务最近的 trace 事件，不全量扫描 JSONL 文件。"""
    return [event for event in _iter_recent_trace_events(max_events=max_events, max_bytes=max_bytes) if event.get("task_id") == task_id or event.get("trace_id") == task_id]


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
            "evaluation_status": metadata.get("evaluation_status"),
            "latency_ms": event.get("latency_ms") or metadata.get("latency_ms"),
            "token_input": event.get("token_input"),
            "token_output": event.get("token_output"),
            "error": event.get("error"),
        })
    return {"task_id": task_id, "events": timeline}


def build_agent_metrics() -> Dict[str, Any]:
    """从 JSONL trace 聚合 Agent、工具、延迟、token 和失败指标。"""
    agent_counter: Counter[str] = Counter()
    tool_counter: Counter[str] = Counter()
    failures: Counter[str] = Counter()
    latency_by_agent: Dict[str, List[float]] = defaultdict(list)
    latency_by_stage: Dict[str, List[float]] = defaultdict(list)
    workflow_latencies: List[float] = []
    llm_latencies: List[float] = []
    deepagents_latencies: List[float] = []
    tokens_by_agent: Dict[str, Dict[str, int]] = defaultdict(lambda: {"input": 0, "output": 0})
    task_timestamps: Dict[str, List[datetime]] = defaultdict(list)

    for event in _iter_trace_events():
        agent_name = event.get("agent_name") or "unknown"
        metadata = event.get("metadata") or {}
        stage_name = _stage_name(event)
        # event_count 统计所有事件，不等同于真实调用次数；调用次数由 tool/llm started 事件单独统计。
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
            if "deepagents" in agent_name.lower() or "deepagents" in event_type.lower():
                deepagents_latencies.append(latency)
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
        "deepagents_latency": _latency_bucket(deepagents_latencies),
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


def build_slow_tasks(limit: int = 20, *, max_events: int = 5000, max_bytes: int = 2_000_000, max_scan_seconds: float = 1.5) -> Dict[str, Any]:
    """返回最近的慢任务，供运营和工程侧定位 15 秒以上的 AI Chat/AgentRuntime 问题。"""
    task_events: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    started_at = perf_counter()
    scanned_events = 0
    truncated = False
    for event in _iter_recent_trace_events(max_events=max_events, max_bytes=max_bytes):
        scanned_events += 1
        if perf_counter() - started_at > max_scan_seconds:
            truncated = True
            break
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
    return {
        "tasks": tasks[:limit],
        "diagnostic": {
            "source": "trace_tail",
            "scanned_events": scanned_events,
            "task_count": len(task_events),
            "truncated": truncated,
            "max_events": max_events,
            "max_bytes": max_bytes,
            "max_scan_seconds": max_scan_seconds,
        },
    }


def _iter_trace_events():
    """逐行读取 JSONL trace，坏行直接跳过。"""
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


def _iter_recent_trace_events(*, max_events: int, max_bytes: int):
    """读取 JSONL trace 文件尾部，避免诊断接口全量扫描大文件。"""
    path = tracer.path if getattr(tracer, "path", None) else DEFAULT_TRACE_PATH
    if not path.exists():
        return
    file_size = path.stat().st_size
    with path.open("rb") as file:
        if file_size > max_bytes:
            file.seek(file_size - max_bytes)
            file.readline()
        lines = file.readlines()
    if max_events > 0:
        lines = lines[-max_events:]
    for raw_line in lines:
        line = raw_line.decode("utf-8", errors="replace").strip()
        if not line:
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue


def _avg(values: List[float]) -> float | None:
    """计算平均值；空列表返回 None 以便 JSON 清楚表达“无样本”。"""
    if not values:
        return None
    return round(sum(values) / len(values), 2)


def _percentile(values: List[float], percentile: int) -> float | None:
    """计算简单百分位值，样本少时使用最近邻索引。"""
    if not values:
        return None
    ordered = sorted(values)
    index = round((len(ordered) - 1) * percentile / 100)
    return round(ordered[index], 2)


def _latency_bucket(values: List[float]) -> Dict[str, Any]:
    """把一组耗时压成 count/avg/p50/p95 的统一结构。"""
    return {"count": len(values), "avg_ms": _avg(values), "p50_ms": _percentile(values, 50), "p95_ms": _percentile(values, 95)}


def _slowest_event(events: List[Dict[str, Any]]) -> Dict[str, Any] | None:
    """返回单个任务中 latency_ms 最大的事件。"""
    candidates = [event for event in events if event.get("latency_ms") is not None]
    if not candidates:
        return None
    event = max(candidates, key=lambda item: float(item.get("latency_ms") or 0))
    return {"event_type": event.get("event_type"), "agent_name": event.get("agent_name"), "latency_ms": event.get("latency_ms"), "stage": _stage_name(event)}


def _stage_name(event: Dict[str, Any]) -> str:
    """生成阶段聚合名，保证 metrics 和 slow-task 面板使用同一口径。"""
    metadata = event.get("metadata") or {}
    event_type = str(event.get("event_type") or "unknown")
    for key in ("stage", "step_name", "tool_name", "workflow_name"):
        if metadata.get(key):
            return f"{event_type}:{metadata[key]}"
    return event_type


def _parse_timestamp(value: Any) -> datetime | None:
    """解析 JSONL 中的 ISO 时间戳，兼容 Z 结尾。"""
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None

