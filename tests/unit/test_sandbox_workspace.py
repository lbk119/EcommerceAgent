from __future__ import annotations

import pytest

from agent.sandbox.models import SandboxFile, SandboxTask
from api.sandbox.workspace import SandboxWorkspaceManager, WorkspaceSecurityError, validate_relative_path


def make_task():
    return SandboxTask(
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
    )


def test_relative_path_allowed():
    assert validate_relative_path("folder/file.txt") == "folder/file.txt"


@pytest.mark.parametrize("path", ["../secret.txt", "/tmp/secret.txt", "C:/secret.txt", ".env", ".git/config", "node_modules/a.js"])
def test_unsafe_paths_rejected(path):
    with pytest.raises(WorkspaceSecurityError):
        validate_relative_path(path)


def test_write_and_collect_output(tmp_path):
    manager = SandboxWorkspaceManager(root=tmp_path, max_input_bytes=1024, max_output_bytes=1024)
    workspace = manager.create_workspace(make_task())

    manager.write_input_files(workspace, [SandboxFile.from_text("input/a.txt", "hello")])
    (workspace / "output" / "result.txt").write_text("done", encoding="utf-8")

    output = manager.collect_output_files(workspace)

    assert output[0].relative_path == "result.txt"
    assert output[0].decoded_bytes() == b"done"


def test_output_size_limit(tmp_path):
    manager = SandboxWorkspaceManager(root=tmp_path, max_input_bytes=1024, max_output_bytes=3)
    workspace = manager.create_workspace(make_task())
    (workspace / "output" / "big.txt").write_text("too big", encoding="utf-8")

    with pytest.raises(WorkspaceSecurityError):
        manager.collect_output_files(workspace)