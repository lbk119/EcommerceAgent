import datetime
import asyncio
import os
from typing import Any, Dict, Optional
from fastapi import WebSocket
from api.context import get_thread_context

# 尝试导入全局运行时（用于脚本模式下的流式输出）
try:
    import builtins
except ImportError:
    builtins = None


class ToolMonitor:
    """
    工具监控类，用于在工具执行过程中上报进度和状态。
    设计为单例模式，可在任何工具中直接导入使用。
    兼容 FastAPI WebSocket 和 脚本运行时的 stream_writer。

    使用示例:
    from api.monitor import monitor

    def my_tool(arg1):
        monitor.report_start("my_tool", {"arg1": arg1})
        ...
        monitor.report_running("my_tool", "正在处理数据...", progress=0.5)
        ...
        monitor.report_end("my_tool", result)
    """
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(ToolMonitor, cls).__new__(cls)
            cls._instance.websocket_manager = None  # 预留给 FastAPI WebSocketManager
        return cls._instance

    def set_websocket_manager(self, manager):
        """设置 FastAPI 的 WebSocket 管理器"""
        self.websocket_manager = manager

    def _emit(self, event_type: str, message: str, data: Optional[Dict[str, Any]] = None, thread_id: Optional[str] = None):
        """内部发送方法"""
        event_data = data or {}
        timestamp = datetime.datetime.now(datetime.UTC).isoformat()
        target_thread_id = thread_id or get_thread_context()
        stage = event_data.get("stage") or _stage_for_event(event_type)
        status = event_data.get("status") or _status_for_event(event_type)
        display = _display_for_event(event_type, stage, event_data)
        payload = {
            # 商业化前端使用 agent_progress 协议渲染阶段卡片；下面的 event/data 等旧字段保留给脚本模式和历史页面兼容。
            "type": "agent_progress",
            "legacy_type": "monitor_event",
            "event": event_type,
            "event_type": event_type,
            "eventType": event_type,
            "message": message,
            "title": display["title"],
            "detail": display["detail"],
            "task_id": event_data.get("task_id"),
            "taskId": event_data.get("task_id"),
            "messageId": event_data.get("message_id"),
            "conversation_id": event_data.get("conversation_id") or target_thread_id,
            "conversationId": event_data.get("conversation_id") or target_thread_id,
            "stage": stage,
            "status": status,
            "latency_ms": event_data.get("latency_ms"),
            "latencyMs": event_data.get("latency_ms"),
            "metadata": event_data,
            "data": event_data,
            "display": display,
            "timestamp": timestamp,
        }

        # 1. 优先尝试通过 FastAPI WebSocket 发送 (定向推送)
        if self.websocket_manager:
            try:
                # 确保 loop 已加载 [fastapi的事件循环]
                manager_loop = self.websocket_manager.loop

                if manager_loop:
                    if target_thread_id:
                        # 检查当前是否在同一个事件循环中
                        try:
                            # 当前的循环事件
                            current_loop = asyncio.get_running_loop()
                        except RuntimeError:
                            current_loop = None

                        if current_loop and current_loop == manager_loop:
                            # 如果在同一个循环中（例如在 create_task 中运行），直接创建任务
                            current_loop.create_task(
                                self.websocket_manager.send_to_thread(payload, target_thread_id)
                            )
                        else:
                            #  FastAPI 的 WebSocket 依赖异步事件循环，且协程必须在创建它的循环中运行：
                            #  如果当前线程和 WebSocket 管理器在同一个循环（比如在 FastAPI 的接口 / 任务中运行）：直接 create_task 效率最高；
                            #  如果在不同循环 / 不同线程（比如同步线程调用）：必须用 asyncio.run_coroutine_threadsafe（线程安全的方式），否则会报错 “协程在错误的循环中运行”。
                            # 如果在不同线程，使用 threadsafe 方法
                            asyncio.run_coroutine_threadsafe(
                                self.websocket_manager.send_to_thread(payload, target_thread_id),
                                manager_loop
                            )
                    else:
                        # 如果没有 thread_id，说明可能是系统级消息，或者未上下文环境
                        pass
            except Exception as e:
                print(f"[Monitor] WebSocket send failed: {e}")

        # 2. 尝试通过全局 runtime 输出 (DeepAgents 脚本模式)
        # 这使得 simple_agents.py 中的 MockRuntime 能接收到数据
        if builtins and hasattr(builtins, 'runtime') and hasattr(builtins.runtime, 'stream_writer'):
            try:
                builtins.runtime.stream_writer(payload)
            except Exception:
                pass

        # 3. 控制台保底输出默认关闭；Windows 控制台同步 print 会拖慢 realtime 热路径。
        if os.getenv("MONITOR_CONSOLE_LOG_ENABLED", "false").lower() in {"1", "true", "yes", "on"}:
            print(f"\n[Monitor:{event_type}] {message}")

    def report_tool(self, tool_name: str, args: Dict[str, Any] = None):
        """报告工具开始执行"""
        self._emit("tool_start", f"开始执行工具: {tool_name}", {"tool_name": tool_name, "args": args})

    def report_assistant(self, assistant_name: str, args: Dict[str, Any] = None):
        """报告正在调用的子智能体进度"""
        self._emit("assistant_call", f"正在调用助手: {assistant_name}",
                   {"assistant_name": assistant_name, "args": args})

    def report_task_result(self, result: str):
        """报告任务最终结果"""
        self._emit("task_result", "任务执行完成", {"result": result})

    def report_session_dir(self, path: str):
        """报告任务工作目录"""
        self._emit("session_created", f"工作目录已创建: {path}", {"path": path})

    def emit_assistant_delta(self, *, task_id: str, conversation_id: str, message_id: str, delta: str):
        """推送 assistant 增量文本；模型暂不支持真 token streaming 时，也可用于 agent draft 的分段输出。"""
        self._send_chat_event({
            "type": "assistant_delta",
            "taskId": task_id,
            "task_id": task_id,
            "messageId": message_id,
            "message_id": message_id,
            "conversationId": conversation_id,
            "conversation_id": conversation_id,
            "delta": delta,
            "timestamp": datetime.datetime.now(datetime.UTC).isoformat(),
        }, conversation_id)

    def emit_assistant_final(self, *, task_id: str, conversation_id: str, message_id: str, content: str, source: str, total_latency_ms: float | None = None):
        """推送最终答案，前端收到后可立即完成占位消息，不必等待下一次轮询。"""
        self._send_chat_event({
            "type": "assistant_final",
            "taskId": task_id,
            "task_id": task_id,
            "messageId": message_id,
            "message_id": message_id,
            "conversationId": conversation_id,
            "conversation_id": conversation_id,
            "content": content,
            "assistantContent": content,
            "assistant_content": content,
            "source": source,
            "totalLatencyMs": total_latency_ms,
            "total_latency_ms": total_latency_ms,
            "timestamp": datetime.datetime.now(datetime.UTC).isoformat(),
        }, conversation_id)

    def emit_agent_error(self, *, task_id: str, conversation_id: str, message_id: str, error_message: str, recoverable: bool = True):
        """推送真实失败原因，禁止前端把失败伪装成成功回答。"""
        self._send_chat_event({
            "type": "agent_error",
            "taskId": task_id,
            "task_id": task_id,
            "messageId": message_id,
            "message_id": message_id,
            "conversationId": conversation_id,
            "conversation_id": conversation_id,
            "errorMessage": error_message,
            "error_message": error_message,
            "recoverable": recoverable,
            "timestamp": datetime.datetime.now(datetime.UTC).isoformat(),
        }, conversation_id)

    def _send_chat_event(self, payload: Dict[str, Any], thread_id: str):
        if not self.websocket_manager or not self.websocket_manager.loop:
            return
        try:
            asyncio.run_coroutine_threadsafe(self.websocket_manager.send_to_thread(payload, thread_id), self.websocket_manager.loop)
        except Exception as e:
            print(f"[Monitor] chat event send failed: {e}")


# 全局单例实例
monitor = ToolMonitor()


def _stage_for_event(event_type: str) -> str:
    if event_type.startswith("prompt_guard"):
        return "prompt_guard"
    if event_type == "task_classified":
        return "task_classified"
    if event_type in {"context_prepared", "memory_retrieval_started", "memory_retrieval_finished", "memory_retrieved"}:
        return "context_prepared" if event_type == "context_prepared" else "memory"
    if event_type.startswith("workflow"):
        return "workflow"
    if event_type.startswith("tool_call"):
        return "tool"
    if event_type.startswith("llm_call"):
        return "llm"
    if event_type.startswith("critic"):
        return "critic"
    if event_type.startswith("persistence"):
        return "persistence"
    if event_type.startswith("memory_write") or event_type == "memory_written":
        return "memory_write"
    if event_type == "agent_finished":
        return "agent_finished"
    if event_type == "agent_failed":
        return "agent_failed"
    return event_type


def _status_for_event(event_type: str) -> str:
    if event_type == "queued" or event_type.endswith("_started"):
        return "running"
    if event_type.endswith("_finished") or event_type in {"task_classified", "context_prepared", "memory_retrieved", "memory_written"}:
        return "completed"
    if event_type.endswith("_failed") or event_type == "agent_failed":
        return "failed"
    if event_type.endswith("_skipped"):
        return "skipped"
    if event_type == "agent_finished":
        return "completed"
    return "running"


def _display_for_event(event_type: str, stage: str, data: Dict[str, Any]) -> Dict[str, Any]:
    title_map = {
        "queued": ("接收问题", "Agent 已接收任务"),
        "task_classified": ("识别意图", "已识别问题类型"),
        "context_prepared": ("读取数据", "运行上下文已准备"),
        "workflow_route_decided": ("命中工作流", "已选择快速 workflow" if data.get("workflow_name") else "未命中固定 workflow"),
        "llm_call_started": ("生成建议", "正在生成建议"),
        "llm_call_finished": ("生成建议", "建议生成完成"),
        "critic_skipped": ("质量检查", "轻量模式跳过完整 Critic"),
        "persistence_finished": ("写入结果", "结果已写入"),
        "memory_write_skipped": ("写入结果", "记忆写入后台处理"),
        "agent_finished": ("完成", "分析完成"),
    }
    group, title = title_map.get(event_type, (_group_for_stage(stage), str(event_type)))
    if event_type.startswith("workflow_step"):
        group = "读取数据"
        title = f"{data.get('step_name') or '业务数据'}{'已读取' if event_type.endswith('finished') else '读取中'}"
    return {
        "group": group,
        "importance": "high" if event_type in {"queued", "task_classified", "workflow_route_decided", "llm_call_finished", "agent_finished"} else "normal",
        "collapsible": True,
        "title": title,
        "detail": data.get("detail") or data.get("result_preview") or "",
    }


def _group_for_stage(stage: str) -> str:
    if stage in {"workflow", "context_prepared", "memory"}:
        return "读取数据"
    if stage == "llm":
        return "生成建议"
    if stage == "critic":
        return "质量检查"
    if stage in {"persistence", "memory_write"}:
        return "写入结果"
    return "执行过程"


class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[str, WebSocket] = {}
        # 延迟绑定 loop，防止初始化时 loop 不一致
        self.loop = None

    def set_loop(self, loop):
        """显式设置事件循环"""
        self.loop = loop
        monitor.set_websocket_manager(self)
        print(f"[Monitor] ConnectionManager manually bound to loop: {id(self.loop)}")

    async def connect(self, websocket: WebSocket, thread_id: str):
        await websocket.accept()
        print(f"存储当前会话id:{thread_id}对应的:{websocket}")
        self.active_connections[thread_id] = websocket
        print(f"Client connected: {thread_id}")

    def disconnect(self, websocket: WebSocket, thread_id: str):
        if thread_id in self.active_connections:
            del self.active_connections[thread_id]
        print(f"Client disconnected: {thread_id}")

    async def send_personal_message(self, message: str, websocket: WebSocket):
        await websocket.send_text(message)

    async def send_to_thread(self, message: dict, thread_id: str):
        if thread_id in self.active_connections:
            websocket = self.active_connections[thread_id]
            await websocket.send_json(message)

    def stats(self) -> dict:
        """返回 WebSocket 连接状态，避免健康检查把未连接的进度通道误报为 ok。"""
        return {
            "websocketManager": "ok" if self.loop else "not_started",
            "activeConnections": len(self.active_connections),
        }


manager = ConnectionManager()
