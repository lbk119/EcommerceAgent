from __future__ import annotations

from agent.runtime.task_context import build_task_context


def test_conversation_id_maps_to_langgraph_thread_id() -> None:
    context = build_task_context("hello", "conversation-1", "task-1", "tenant-1", "user-1", "shop-1")
    try:
        assert context.config["configurable"]["thread_id"] == "conversation-1"
        assert context.config["configurable"]["task_id"] == "task-1"
        assert context.config["configurable"]["checkpoint_ns"] == "main_agent"
    finally:
        context.cleanup()


def test_agent_job_task_id_is_available_for_checkpoint_resume() -> None:
    context = build_task_context("job", "job-conversation", "agent-job-task", "tenant-1", "user-1", "shop-1")
    try:
        configurable = context.config["configurable"]
        assert configurable["thread_id"] == "job-conversation"
        assert configurable["task_id"] == "agent-job-task"
        assert configurable["tenant_id"] == "tenant-1"
        assert configurable["shop_id"] == "shop-1"
    finally:
        context.cleanup()