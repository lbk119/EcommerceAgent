import asyncio
import json
import logging
import os
from typing import Awaitable, Callable, Dict

from agent.runtime.profiles import normalize_runtime_profile


TaskHandler = Callable[[dict], Awaitable[None]]
logger = logging.getLogger(__name__)


class TaskQueue:
    def __init__(self):
        self.backend = os.getenv("TASK_QUEUE_BACKEND", "inline").lower()
        self.redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
        self.nats_url = os.getenv("NATS_URL", "nats://localhost:4222")
        self.queue_name = os.getenv("TASK_QUEUE_NAME", "deepagent.tasks")
        self.max_concurrency = max(1, int(os.getenv("MAX_AGENT_CONCURRENCY", "10")))
        self.profile_concurrency = {
            "realtime": max(1, int(os.getenv("REALTIME_AGENT_CONCURRENCY", "8"))),
            "standard": max(1, int(os.getenv("STANDARD_AGENT_CONCURRENCY", "2"))),
            "deep": max(1, int(os.getenv("DEEP_AGENT_CONCURRENCY", "1"))),
        }
        self._inline_queue: asyncio.Queue[dict] = asyncio.Queue()
        self._worker_task: asyncio.Task | None = None
        self._semaphore: asyncio.Semaphore | None = None
        self._profile_semaphores: dict[str, asyncio.Semaphore] = {}
        self._running_tasks: set[asyncio.Task] = set()

    async def start(self, handler: TaskHandler) -> None:
        # Semaphore 绑定当前事件循环；服务启动时创建，避免模块导入阶段触碰 event loop。
        self._semaphore = asyncio.Semaphore(self.max_concurrency)
        self._profile_semaphores = {profile: asyncio.Semaphore(limit) for profile, limit in self.profile_concurrency.items()}
        if self.backend == "inline":
            self._worker_task = asyncio.create_task(self._inline_worker(handler))
            return
        if self.backend == "redis":
            self._worker_task = asyncio.create_task(self._redis_worker(handler))
            return
        if self.backend == "nats":
            self._worker_task = asyncio.create_task(self._nats_worker(handler))
            return
        raise ValueError(f"不支持的任务队列后端: {self.backend}")

    async def enqueue(self, payload: dict) -> None:
        if self.backend == "inline":
            await self._inline_queue.put(payload)
            return
        if self.backend == "redis":
            redis = await self._redis_client()
            await redis.rpush(self.queue_name, json.dumps(payload, ensure_ascii=False))
            await redis.aclose()
            return
        if self.backend == "nats":
            nats = await self._nats_client()
            await nats.publish(self.queue_name, json.dumps(payload, ensure_ascii=False).encode("utf-8"))
            await nats.drain()
            return
        raise ValueError(f"不支持的任务队列后端: {self.backend}")

    async def _inline_worker(self, handler: TaskHandler) -> None:
        while True:
            payload = await self._inline_queue.get()
            self._schedule_handler(handler, payload)

    async def _redis_worker(self, handler: TaskHandler) -> None:
        redis = await self._redis_client()
        while True:
            item = await redis.blpop(self.queue_name, timeout=5)
            if not item:
                continue
            _, body = item
            payload = json.loads(body)
            self._schedule_handler(handler, payload)

    async def _nats_worker(self, handler: TaskHandler) -> None:
        nats = await self._nats_client()

        async def on_message(message):
            payload = json.loads(message.data.decode("utf-8"))
            self._schedule_handler(handler, payload)

        await nats.subscribe(self.queue_name, cb=on_message)
        while True:
            await asyncio.sleep(3600)

    async def _redis_client(self):
        try:
            from redis.asyncio import from_url
        except ImportError as exc:
            raise RuntimeError("启用 Redis 队列需要安装 redis 包") from exc
        return from_url(self.redis_url, decode_responses=True)

    async def _nats_client(self):
        try:
            from nats import connect
        except ImportError as exc:
            raise RuntimeError("启用 NATS 队列需要安装 nats-py 包") from exc
        return await connect(self.nats_url)

    def _schedule_handler(self, handler: TaskHandler, payload: dict) -> None:
        """
        创建受控后台任务，并保留引用直到任务结束。

        直接 asyncio.create_task(handler(payload)) 会让异常只出现在事件循环日志里，并且任务对象可能被
        GC；这里统一走 _run_handler_safely，确保并发上限、异常记录和任务引用清理都有明确位置。
        """
        task = asyncio.create_task(self._run_handler_safely(handler, payload))
        self._running_tasks.add(task)
        task.add_done_callback(self._running_tasks.discard)

    def stats(self) -> dict:
        """返回任务队列的真实运行状态，供 /agent-runtime/health 展示。"""
        queued = self._inline_queue.qsize() if self.backend == "inline" else None
        return {
            "status": "ok" if self._worker_task and not self._worker_task.done() else "not_started",
            "backend": self.backend,
            "running": len(self._running_tasks),
            "queued": queued,
            "maxConcurrency": self.max_concurrency,
            "profileConcurrency": self.profile_concurrency,
        }

    async def _run_handler_safely(self, handler: TaskHandler, payload: dict) -> None:
        """
        在 MAX_AGENT_CONCURRENCY 限制下执行任务 handler。

        handler 必须等待真实 Agent 任务结束后再返回；这样 semaphore 限制的是运行中的 Agent 数量，
        不是“启动任务”的瞬时数量。任何异常都会被记录到日志和 trace，但不会杀死队列 worker。
        """
        semaphore = self._semaphore
        if semaphore is None:
            semaphore = asyncio.Semaphore(self.max_concurrency)
            self._semaphore = semaphore

        profile = self._profile_for_payload(payload)
        profile_semaphore = self._profile_semaphores.get(profile)
        if profile_semaphore is None:
            profile_semaphore = asyncio.Semaphore(self.profile_concurrency.get(profile, 1))
            self._profile_semaphores[profile] = profile_semaphore

        async with semaphore, profile_semaphore:
            task_id = payload.get("task_id") or payload.get("thread_id") or payload.get("conversation_id") or "unknown"
            try:
                await handler(payload)
            except asyncio.CancelledError:
                logger.info("Task queue handler cancelled: task_id=%s", task_id)
                raise
            except Exception as error:
                logger.exception("Task queue handler failed: task_id=%s", task_id)
                self._trace_handler_failure(payload, error)

    def _trace_handler_failure(self, payload: dict, error: Exception) -> None:
        """任务队列异常 trace；懒加载 tracer，避免轻量导入 api.task_queue 时拉入观测写入器。"""
        try:
            from agent.observability.tracer import tracer

            task_id = payload.get("task_id") or payload.get("thread_id") or payload.get("conversation_id") or "unknown"
            tracer.emit(
                "task_queue_handler_failed",
                trace_id=task_id,
                task_id=payload.get("task_id"),
                conversation_id=payload.get("conversation_id") or payload.get("thread_id"),
                agent_name="task_queue",
                error=str(error)[:1000],
                metadata={"backend": self.backend, "queue_name": self.queue_name},
            )
        except Exception:
            logger.exception("Failed to write task queue failure trace")

    def _profile_for_payload(self, payload: dict) -> str:
        if payload.get("source") == "ai_chat":
            return "realtime"
        return normalize_runtime_profile(payload.get("runtime_profile") or payload.get("profile") or "standard")


task_queue = TaskQueue()