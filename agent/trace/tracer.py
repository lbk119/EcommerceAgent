"""Agent 运行时 JSONL trace 写入器。

trace 写入必须 best-effort、非阻塞：业务线程只负责把事件放入队列，后台线程负责落盘。
写入失败、队列满或实时推送失败都不能影响 Agent 主流程，只累计 dropped_count 供排障使用。
"""

from __future__ import annotations

import json
import os
import queue
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from agent.security.redaction import redact_secrets
from agent.trace.events import TraceEvent


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TRACE_PATH = PROJECT_ROOT / "data" / "memory" / "agent_traces.jsonl"


class JsonlTracer:
    """API、runtime、tools 和测试共享的非阻塞 JSONL writer。"""

    def __init__(self, path: Optional[Path] = None, max_queue_size: int = 5000):
        self.path = path or Path(os.getenv("AGENT_TRACE_PATH", DEFAULT_TRACE_PATH))
        self.queue: queue.Queue[TraceEvent] = queue.Queue(maxsize=max_queue_size)
        self.dropped_count = 0
        self._started = False
        self._lock = threading.Lock()

    def emit(
        self,
        event_type: str,
        *,
        trace_id: str,
        task_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
        agent_name: Optional[str] = None,
        latency_ms: Optional[float] = None,
        token_input: Optional[int] = None,
        token_output: Optional[int] = None,
        cost: Optional[float] = None,
        error: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        event = TraceEvent(
            trace_id=trace_id,
            event_type=event_type,
            task_id=task_id,
            conversation_id=conversation_id,
            agent_name=agent_name,
            latency_ms=latency_ms,
            token_input=token_input,
            token_output=token_output,
            cost=cost,
            error=error,
            metadata=redact_secrets(metadata or {}),
        )
        self.record(event)

    def record(self, event: TraceEvent) -> None:
        try:
            self._ensure_started()
            self.queue.put_nowait(event)
        except Exception:
            self.dropped_count += 1
        self._emit_live_progress(event)

    def _ensure_started(self) -> None:
        if self._started:
            return
        with self._lock:
            if self._started:
                return
            self.path.parent.mkdir(parents=True, exist_ok=True)
            thread = threading.Thread(target=self._writer_loop, name="agent-jsonl-tracer", daemon=True)
            thread.start()
            self._started = True

    def _writer_loop(self) -> None:
        while True:
            event = self.queue.get()
            try:
                self._write_event(event)
            except Exception:
                self.dropped_count += 1
            finally:
                self.queue.task_done()

    def _write_event(self, event: TraceEvent) -> None:
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "trace_id": event.trace_id,
            "task_id": event.task_id,
            "conversation_id": event.conversation_id,
            "event_type": event.event_type,
            "agent_name": event.agent_name,
            "latency_ms": event.latency_ms,
            "token_input": event.token_input,
            "token_output": event.token_output,
            "cost": event.cost,
            "error": event.error,
            "metadata": event.metadata,
        }
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def _emit_live_progress(self, event: TraceEvent) -> None:
        if not event.conversation_id or event.event_type not in _LIVE_PROGRESS_EVENTS:
            return
        try:
            from api.monitor import monitor

            metadata = dict(event.metadata or {})
            metadata.update(
                {
                    "task_id": event.task_id,
                    "conversation_id": event.conversation_id,
                    "agent_name": event.agent_name,
                    "latency_ms": event.latency_ms,
                    "trace_id": event.trace_id,
                    "error": event.error,
                }
            )
            monitor._emit(event.event_type, _progress_message(event.event_type, metadata), metadata, thread_id=event.conversation_id)
        except Exception:
            return


_LIVE_PROGRESS_EVENTS = {
    "queued",
    "prompt_guard_started",
    "prompt_guard_finished",
    "task_classified",
    "context_prepared",
    "deepagents_native_started",
    "deepagents_native_finished",
    "tool_call_started",
    "tool_call_finished",
    "tool_call_failed",
    "critic_started",
    "critic_finished",
    "critic_failed",
    "critic_skipped",
    "persistence_started",
    "persistence_finished",
    "memory_write_started",
    "memory_write_finished",
    "memory_write_skipped",
    "agent_finished",
}


def _progress_message(event_type: str, metadata: Dict[str, Any]) -> str:
    messages = {
        "queued": "任务已进入运行队列",
        "prompt_guard_started": "正在进行提示词安全检查",
        "prompt_guard_finished": "提示词安全检查完成",
        "task_classified": "PlannerAgent 已生成任务计划",
        "context_prepared": "运行上下文已准备完成",
        "deepagents_native_started": "DeepAgents 主链路开始执行",
        "deepagents_native_finished": "DeepAgents 主链路执行完成",
        "tool_call_started": f"正在调用工具 {metadata.get('tool_name') or ''}".strip(),
        "tool_call_finished": f"工具调用完成 {metadata.get('tool_name') or ''}".strip(),
        "tool_call_failed": f"工具调用失败 {metadata.get('tool_name') or ''}".strip(),
        "critic_started": "Critic 开始质量校验",
        "critic_finished": "Critic 质量校验完成",
        "critic_failed": "Critic 质量校验未通过",
        "critic_skipped": "本次任务跳过 Critic",
        "persistence_started": "正在持久化分析结果",
        "persistence_finished": "分析结果已持久化",
        "memory_write_started": "正在沉淀长期记忆",
        "memory_write_finished": "长期记忆沉淀完成",
        "memory_write_skipped": "本次任务跳过长期记忆写入",
        "agent_finished": "Agent 任务已结束",
    }
    return messages.get(event_type, event_type)


tracer = JsonlTracer()
