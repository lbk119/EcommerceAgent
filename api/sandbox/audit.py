"""Sandbox audit event writer."""

from __future__ import annotations

from typing import Any

from agent.sandbox.models import SandboxTask
from agent.trace.tracer import tracer


def emit_sandbox_event(event_type: str, task: SandboxTask, *, error: str | None = None, **metadata: Any) -> None:
    safe_metadata = {
        "task_id": task.task_id,
        "conversation_id": task.conversation_id,
        "tenant_id": task.tenant_id,
        "shop_id": task.shop_id,
        "user_id": task.user_id,
        "profile": task.profile,
        "agent_id": task.agent_id,
        "tool_name": task.tool_name,
        "runtime": task.runtime,
        "network_mode": task.network_policy.mode,
        "timeout_seconds": task.timeout_seconds,
        "memory_mb": task.resource_limits.memory_mb,
        "cpu_count": task.resource_limits.cpu_count,
        **metadata,
    }
    safe_metadata.pop("code", None)
    safe_metadata.pop("env", None)
    tracer.emit(
        event_type,
        trace_id=task.task_id,
        task_id=task.task_id,
        conversation_id=task.conversation_id,
        agent_name="sandbox_server",
        error=error,
        metadata=safe_metadata,
    )