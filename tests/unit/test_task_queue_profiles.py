from __future__ import annotations

from api.task_queue import TaskQueue


def test_ai_chat_payload_uses_declared_runtime_profile_for_queue_pool() -> None:
    queue = TaskQueue()

    assert queue._profile_for_payload({"source": "ai_chat"}) == "realtime"
    assert queue._profile_for_payload({"source": "ai_chat", "runtime_profile": "standard"}) == "standard"
    assert queue._profile_for_payload({"source": "ai_chat", "runtime_profile": "deep"}) == "deep"

