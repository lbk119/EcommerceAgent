from __future__ import annotations

import pytest
from pydantic import ValidationError

from agent.sandbox.models import SandboxFile, SandboxTask


def test_sandbox_task_round_trip():
    task = SandboxTask(
        task_id="task-1",
        conversation_id="conv-1",
        tenant_id="tenant-1",
        user_id="user-1",
        shop_id="shop-1",
        profile="standard",
        agent_id="agent-1",
        tool_name="read_file_content",
        runtime="python",
        code="print('ok')",
        input_files=[SandboxFile.from_text("input/a.txt", "hello")],
    )

    loaded = SandboxTask.model_validate(task.model_dump(mode="json"))

    assert loaded.task_id == "task-1"
    assert loaded.input_files[0].decoded_bytes() == b"hello"


def test_missing_tenant_task_profile_fails():
    with pytest.raises(ValidationError):
        SandboxTask(
            task_id="",
            conversation_id="conv-1",
            tenant_id="",
            user_id="user-1",
            shop_id="shop-1",
            profile="standard",
            agent_id="agent-1",
            tool_name="read_file_content",
            runtime="python",
            code="print('ok')",
        )


def test_runtime_enum_validation():
    with pytest.raises(ValidationError):
        SandboxTask(
            task_id="task-1",
            conversation_id="conv-1",
            tenant_id="tenant-1",
            user_id="user-1",
            shop_id="shop-1",
            profile="standard",
            agent_id="agent-1",
            tool_name="read_file_content",
            runtime="ruby",
            code="puts 'nope'",
        )