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


# 全局 tracer：平台层默认使用这个实例；测试中可以直接 new JsonlTracer(temp_path)。
tracer = JsonlTracer()