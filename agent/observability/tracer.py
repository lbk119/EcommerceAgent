"""
JSONL Tracer。

这是第一阶段的生产观测底座：先把结构化事件写到本地 JSONL，成本低、依赖少、方便排查。
后续如果要写 MySQL、OpenTelemetry、Loki 或 ClickHouse，可以保持 TraceEvent 不变，替换 writer。
"""

import json
import os
import queue
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from agent.observability.events import TraceEvent
from agent.security.redaction import redact_secrets


PROJECT_ROOT = Path(__file__).resolve().parents[2]
# 默认写到 data/memory，和现有 task_events/reflections 等运行数据放在一起。
DEFAULT_TRACE_PATH = PROJECT_ROOT / "data" / "memory" / "agent_traces.jsonl"


class JsonlTracer:
    """
    失败不阻断的 JSONL 事件写入器。

    业务线程只负责把事件放进内存队列，后台 writer 线程负责落盘。
    如果队列满、路径异常、磁盘写失败，都只丢弃 trace 并记录 dropped_count，不把异常抛回业务链路。
    """

    def __init__(self, path: Optional[Path] = None):
        # 允许通过 AGENT_TRACE_PATH 把 trace 输出到其他位置，便于本地调试或容器挂载。
        self.path = path or Path(os.getenv("AGENT_TRACE_PATH", DEFAULT_TRACE_PATH))
        self.queue_size = int(os.getenv("AGENT_TRACE_QUEUE_SIZE", "1000"))
        self.dropped_count = 0
        self._queue: queue.Queue[TraceEvent] = queue.Queue(maxsize=self.queue_size)
        self._writer_started = False
        self._writer_lock = threading.Lock()

    def emit(self, event_type: str, **fields: Any) -> None:
        """便捷入口：调用方只传事件类型和字段，内部组装 TraceEvent 并非阻塞入队。"""
        try:
            event = TraceEvent(event_type=event_type, **fields)
            self.write(event)
            self._emit_live_progress(event)
        except Exception:
            self._drop_event()

    def write(self, event: TraceEvent) -> None:
        """把 TraceEvent 放入队列；队列满时直接丢弃，保证业务路径不阻塞。"""
        try:
            self._ensure_writer_started()
            self._queue.put_nowait(event)
        except Exception:
            self._drop_event()

    def _ensure_writer_started(self) -> None:
        """懒启动后台 writer，避免导入模块时就创建线程。"""
        if self._writer_started:
            return
        with self._writer_lock:
            if self._writer_started:
                return
            thread = threading.Thread(target=self._writer_loop, name="agent-trace-writer", daemon=True)
            thread.start()
            self._writer_started = True

    def _writer_loop(self) -> None:
        """后台循环写 JSONL。任何写入异常都吞掉并继续消费后续事件。"""
        while True:
            event = self._queue.get()
            try:
                self._write_sync(event)
            except Exception:
                self._drop_event()
            finally:
                self._queue.task_done()

    def _write_sync(self, event: TraceEvent) -> None:
        """实际同步写文件，只允许后台 writer 调用。"""
        payload: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "trace_id": event.trace_id,
            "task_id": event.task_id,
            "conversation_id": event.conversation_id,
            "agent_name": event.agent_name,
            "event_type": event.event_type,
            "latency_ms": event.latency_ms,
            "token_input": event.token_input,
            "token_output": event.token_output,
            "cost": event.cost,
            "error": redact_secrets(event.error),
            "metadata": redact_secrets(event.metadata),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")

    def _drop_event(self) -> None:
        """记录丢弃计数。计数本身只做排障参考，不参与业务判断。"""
        self.dropped_count += 1

    def _emit_live_progress(self, event: TraceEvent) -> None:
        """把部分 trace 事件同步转成 WebSocket 进度。

        JSONL 写入和实时进度共用同一份 TraceEvent，但实时推送必须 best-effort：WebSocket 断开、
        monitor 未初始化或序列化失败都不能影响 Agent 主流程。
        """
        if not event.conversation_id or event.event_type not in _LIVE_PROGRESS_EVENTS:
            return
        try:
            from api.monitor import monitor

            monitor._emit(
                event.event_type,
                _live_progress_message(event),
                {
                    "trace_id": event.trace_id,
                    "task_id": event.task_id,
                    "conversation_id": event.conversation_id,
                    "agent_name": event.agent_name,
                    "latency_ms": event.latency_ms,
                    "token_input": event.token_input,
                    "token_output": event.token_output,
                    "error": redact_secrets(event.error),
                    **redact_secrets(event.metadata),
                },
                thread_id=event.conversation_id,
            )
        except Exception:
            pass


# 只有这些事件会推送到前端时间线；过细的内部事件仍会写 JSONL，但不打扰用户界面。
_LIVE_PROGRESS_EVENTS = {
    "queued",
    "prompt_guard_started",
    "prompt_guard_finished",
    "task_classified",
    "context_prepared",
    "memory_retrieval_started",
    "memory_retrieval_finished",
    "memory_retrieved",
    "agent_started",
    "runtime_stage_completed",
    "planner_started",
    "planner_finished",
    "planner_failed",
    "plan_execution_started",
    "plan_execution_finished",
    "plan_step_started",
    "plan_step_finished",
    "plan_step_failed",
    "workflow_route_decided",
    "workflow_step_started",
    "workflow_step_finished",
    "workflow_step_failed",
    "workflow_finished",
    "workflow_failed",
    "tool_call_started",
    "tool_call_finished",
    "tool_call_failed",
    "llm_call_started",
    "llm_call_finished",
    "llm_call_failed",
    "reducer_started",
    "reducer_finished",
    "reducer_polish_started",
    "reducer_polish_finished",
    "reducer_polish_failed",
    "critic_policy_evaluated",
    "critic_started",
    "critic_finished",
    "critic_skipped",
    "critic_failed",
    "persistence_started",
    "persistence_finished",
    "memory_write_started",
    "memory_write_finished",
    "memory_write_skipped",
    "memory_written",
    "agent_finished",
}


def _live_progress_message(event: TraceEvent) -> str:
    """把结构化事件转换成前端可读的一句话进度。"""
    metadata = event.metadata or {}
    event_type = event.event_type
    if event_type == "queued":
        return "Agent 已接收任务，正在进入运行队列"
    if event_type == "prompt_guard_started":
        return "正在进行提示词安全检查"
    if event_type == "prompt_guard_finished":
        return "提示词安全检查完成"
    if event_type == "task_classified":
        task_plan = metadata.get("task_plan") or {}
        intent = task_plan.get("intent") or {} if isinstance(task_plan, dict) else {}
        task_type = metadata.get("intent") or task_plan.get("primary_task_type") if isinstance(task_plan, dict) else ""
        if not task_type and isinstance(intent, dict):
            task_type = intent.get("primary_goal")
        task_type = task_type or "unknown"
        return f"已识别问题类型：{task_type}"
    if event_type == "context_prepared":
        return "运行上下文已准备完成"
    if event_type == "memory_retrieval_started":
        return "正在检索长期记忆"
    if event_type == "memory_retrieval_finished":
        return f"长期记忆检索完成：{metadata.get('count', 0)} 条"
    if event_type == "memory_retrieved":
        return f"长期记忆召回完成：{metadata.get('count', 0)} 条"
    if event_type == "agent_started":
        task_plan = metadata.get("task_plan") or {}
        task_type = metadata.get("intent") or (task_plan.get("primary_task_type") if isinstance(task_plan, dict) else "") or "unknown"
        return f"Agent 已启动，任务类型：{task_type}"
    if event_type == "runtime_stage_completed":
        return f"运行阶段完成：{metadata.get('stage', 'unknown')}"
    if event_type == "planner_started":
        return "PlannerAgent 正在解析需求并生成执行计划"
    if event_type == "planner_finished":
        step_count = metadata.get("plan_steps_count") or len((metadata.get("plan") or {}).get("steps", []))
        return f"PlannerAgent 已生成计划：{step_count} 个步骤"
    if event_type == "planner_failed":
        return "PlannerAgent 规划失败，准备使用确定性 fallback"
    if event_type == "plan_execution_started":
        return f"计划已生成，准备并行执行 {metadata.get('step_count', 0)} 个节点"
    if event_type == "plan_execution_finished":
        return "并行计划执行完成" if not metadata.get("critical_failed") else "并行计划执行完成，但关键节点失败"
    if event_type == "plan_step_started":
        return f"并行读取：{metadata.get('step_name')}"
    if event_type == "plan_step_finished":
        return f"并行节点完成：{metadata.get('step_name')}，返回 {metadata.get('row_count', 0)} 行"
    if event_type == "plan_step_failed":
        return f"并行节点失败：{metadata.get('step_name')}"
    if event_type == "workflow_route_decided":
        if metadata.get("selected"):
            return f"已选择 workflow：{metadata.get('workflow_name')}"
        return "未命中固定 workflow，回落 DeepAgent"
    if event_type == "workflow_step_started":
        return f"开始执行节点：{metadata.get('step_name')}"
    if event_type == "workflow_step_finished":
        return f"节点完成：{metadata.get('step_name')}，返回 {metadata.get('row_count', 0)} 行"
    if event_type == "workflow_step_failed":
        return f"节点失败：{metadata.get('step_name')}"
    if event_type == "workflow_finished":
        return f"workflow 完成：{metadata.get('workflow_name')}"
    if event_type == "workflow_failed":
        return f"workflow 失败：{metadata.get('workflow_name')}，回落 DeepAgent"
    if event_type == "tool_call_started":
        return f"开始调用工具：{metadata.get('tool_name', 'unknown')}"
    if event_type == "tool_call_finished":
        return f"工具调用完成：{metadata.get('tool_name', 'unknown')}"
    if event_type == "tool_call_failed":
        return f"工具调用失败：{metadata.get('tool_name', 'unknown')}"
    if event_type == "llm_call_started":
        return f"开始调用模型：{metadata.get('profile') or event.agent_name}"
    if event_type == "llm_call_finished":
        seconds = round((event.latency_ms or 0) / 1000, 1)
        return f"模型调用完成：{metadata.get('profile') or event.agent_name}，耗时 {seconds}s"
    if event_type == "llm_call_failed":
        return f"模型调用失败：{metadata.get('profile') or event.agent_name}"
    if event_type == "reducer_started":
        return "正在汇总并行结果"
    if event_type == "reducer_finished":
        return "并行结果汇总完成"
    if event_type == "reducer_polish_started":
        return "fast model 正在润色答案"
    if event_type == "reducer_polish_finished":
        return "fast model 润色完成"
    if event_type == "reducer_polish_failed":
        return "fast model 润色失败，返回确定性结论"
    if event_type == "critic_policy_evaluated":
        return "Critic 策略已评估" if metadata.get("required") else "Critic 策略已评估：本次跳过"
    if event_type == "critic_started":
        return "Critic 开始校验"
    if event_type == "critic_finished":
        return "Critic 校验完成"
    if event_type == "critic_skipped":
        return "轻量检查已跳过 Critic"
    if event_type == "critic_failed":
        return "Critic 校验失败"
    if event_type == "persistence_started":
        return "正在持久化分析结果"
    if event_type == "persistence_finished":
        return "分析结果已持久化"
    if event_type == "memory_write_started":
        return "正在沉淀长期记忆"
    if event_type == "memory_write_finished":
        return f"长期记忆沉淀完成：{metadata.get('written', 0)} 条"
    if event_type == "memory_write_skipped":
        return "轻量对话已延后长期记忆写入"
    if event_type == "memory_written":
        return f"长期记忆写入完成：{metadata.get('written', 0)} 条"
    if event_type == "agent_finished":
        return f"Agent 已结束：{metadata.get('status', 'unknown')}"
    return event_type


# 全局 tracer：平台层默认使用这个实例；测试中可以直接 new JsonlTracer(temp_path)。
tracer = JsonlTracer()
