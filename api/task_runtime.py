import asyncio
import time
from typing import Dict, Optional


class TaskRuntime:
    def __init__(self):
        self._tasks: Dict[str, asyncio.Task] = {}
        self._states: Dict[str, dict] = {}
        self._interrupt_futures: Dict[str, asyncio.Future] = {}
        self._conversation_latest_task: Dict[str, str] = {}
        self._lock = asyncio.Lock()

    async def enqueue(self, thread_id: str, query: str, metadata: dict = None) -> None:
        metadata = metadata or {}
        async with self._lock:
            if thread_id in self._tasks and not self._tasks[thread_id].done():
                raise ValueError(f"任务正在运行: {thread_id}")
            conversation_id = metadata.get("conversation_id")
            self._ensure_conversation_available(conversation_id, thread_id)
            self._states[thread_id] = {
                "thread_id": thread_id,
                "status": "queued",
                "query": query,
                "created_at": time.time(),
                "updated_at": time.time(),
                **metadata,
            }
            if conversation_id:
                self._conversation_latest_task[conversation_id] = thread_id

    async def start(self, thread_id: str, query: str, coroutine, metadata: dict = None) -> None:
        task = await self._start_task(thread_id, query, coroutine, metadata)
        task.add_done_callback(lambda done_task: asyncio.create_task(self._finish(thread_id, done_task)))

    async def start_and_wait(self, thread_id: str, query: str, coroutine, metadata: dict = None) -> None:
        """
        启动任务并等待真实 Agent 协程结束。

        TaskQueue 的 MAX_AGENT_CONCURRENCY semaphore 会包住调用这个方法的 handler；如果这里只是
        create_task 后立刻返回，并发限制只能限制“启动速度”，不能限制“运行中的 Agent 数量”。
        start_and_wait 让队列 worker 一直持有 semaphore，直到 Agent task 完成或取消。
        """
        task = await self._start_task(thread_id, query, coroutine, metadata)
        try:
            await task
        finally:
            await self._finish(thread_id, task)

    async def _start_task(self, thread_id: str, query: str, coroutine, metadata: dict = None) -> asyncio.Task:
        """创建并登记任务；start 和 start_and_wait 共用，避免状态写入逻辑分叉。"""
        metadata = metadata or {}
        async with self._lock:
            if thread_id in self._tasks and not self._tasks[thread_id].done():
                self._close_coroutine(coroutine)
                raise ValueError(f"任务正在运行: {thread_id}")
            conversation_id = metadata.get("conversation_id")
            try:
                self._ensure_conversation_available(conversation_id, thread_id)
            except ValueError:
                self._close_coroutine(coroutine)
                raise

            task = asyncio.create_task(coroutine)
            self._tasks[thread_id] = task
            self._states[thread_id] = {
                **self._states.get(thread_id, {}),
                "thread_id": thread_id,
                "status": "running",
                "query": query,
                "updated_at": time.time(),
                **metadata,
            }
            self._states[thread_id].setdefault("created_at", time.time())
            if conversation_id:
                self._conversation_latest_task[conversation_id] = thread_id
            return task

    def _ensure_conversation_available(self, conversation_id: str | None, thread_id: str) -> None:
        if not conversation_id:
            return
        active_task_id = self._conversation_latest_task.get(conversation_id)
        if not active_task_id or active_task_id == thread_id:
            return
        active_state = self._states.get(active_task_id) or {}
        if active_state.get("status") in {"queued", "running", "interrupted", "cancelling"}:
            raise ValueError(f"当前会话已有任务运行中: conversation_id={conversation_id}, task_id={active_task_id}")

    @staticmethod
    def _close_coroutine(coroutine) -> None:
        close = getattr(coroutine, "close", None)
        if close:
            close()

    async def resolve_task_id(self, task_or_conversation_id: str) -> str:
        async with self._lock:
            return self._conversation_latest_task.get(task_or_conversation_id, task_or_conversation_id)

    async def cancel(self, thread_id: str) -> bool:
        async with self._lock:
            task = self._tasks.get(thread_id)
            state = self._states.get(thread_id)
            interrupt_future = self._interrupt_futures.pop(thread_id, None)
            if interrupt_future and not interrupt_future.done():
                interrupt_future.set_result({"decision": "abort", "instruction": "用户取消任务"})
            if not task or task.done():
                if state and state.get("status") == "queued":
                    state["status"] = "cancelled"
                    state["updated_at"] = time.time()
                    return True
                return False
            self._states[thread_id]["status"] = "cancelling"
            self._states[thread_id]["updated_at"] = time.time()
            task.cancel()
            return True

    async def interrupt(self, thread_id: str, reason: str, summary: str) -> dict:
        # 人工熔断点：任务协程会停在这里，直到 resume 接口写入决策。
        async with self._lock:
            loop = asyncio.get_running_loop()
            future = loop.create_future()
            self._interrupt_futures[thread_id] = future
            state = self._states.setdefault(thread_id, {"thread_id": thread_id})
            state["status"] = "interrupted"
            state["interrupt"] = {
                "reason": reason,
                "summary": summary,
                "options": ["continue", "revise", "abort"],
                "created_at": time.time(),
            }
            state["updated_at"] = time.time()

        return await future

    async def resume(self, thread_id: str, decision: str, instruction: str = "") -> bool:
        decision = decision.lower().strip()
        if decision not in {"continue", "revise", "abort"}:
            raise ValueError("decision 必须是 continue、revise 或 abort")

        async with self._lock:
            future = self._interrupt_futures.pop(thread_id, None)
            state = self._states.get(thread_id)
            if not future or future.done() or not state or state.get("status") != "interrupted":
                return False

            state["status"] = "running" if decision != "abort" else "cancelling"
            state["resume_decision"] = {"decision": decision, "instruction": instruction}
            state.pop("interrupt", None)
            state["updated_at"] = time.time()
            future.set_result({"decision": decision, "instruction": instruction})
            return True

    async def is_cancelled(self, thread_id: str) -> bool:
        async with self._lock:
            state = self._states.get(thread_id)
            return bool(state and state.get("status") == "cancelled")

    async def get(self, thread_id: str) -> Optional[dict]:
        async with self._lock:
            state = self._states.get(thread_id)
            if not state:
                return None
            return dict(state)

    async def list(self) -> list[dict]:
        async with self._lock:
            return [dict(state) for state in self._states.values()]

    async def _finish(self, thread_id: str, task: asyncio.Task) -> None:
        async with self._lock:
            state = self._states.get(thread_id)
            if not state:
                return
            if task.cancelled():
                state["status"] = "cancelled"
            else:
                error = task.exception()
                if error:
                    state["status"] = "failed"
                    state["error"] = str(error)
                else:
                    state["status"] = "succeeded"
            state["updated_at"] = time.time()


task_runtime = TaskRuntime()