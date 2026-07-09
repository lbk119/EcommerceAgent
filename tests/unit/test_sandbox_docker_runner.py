from __future__ import annotations

import subprocess

import pytest

from agent.sandbox.models import SandboxTask
from api.sandbox.docker_runner import DockerSandboxRunner, docker_available, docker_image_available
from api.sandbox.workspace import SandboxWorkspaceManager


pytestmark = pytest.mark.skipif(not docker_available(), reason="Docker is not available")


def require_python_image():
    if not docker_image_available("ecommerce-agent-sandbox-python:latest"):
        pytest.skip("sandbox python image is not built")


def make_task(code: str, timeout_seconds: int = 30):
    return SandboxTask(
        task_id="task-docker-1",
        conversation_id="conv-1",
        tenant_id="tenant-1",
        user_id="user-1",
        shop_id="shop-1",
        profile="standard",
        agent_id="agent-1",
        tool_name="read_file_content",
        runtime="python",
        code=code,
        timeout_seconds=timeout_seconds,
    )


def test_simple_python_print(tmp_path, monkeypatch):
    require_python_image()
    monkeypatch.setenv("ENABLE_DOCKER_SANDBOX", "true")
    runner = DockerSandboxRunner(workspace_manager=SandboxWorkspaceManager(root=tmp_path))

    result = runner.run(make_task("print('sandbox-ok')"))

    assert result.ok
    assert "sandbox-ok" in result.stdout


def test_container_removed_after_execution(tmp_path, monkeypatch):
    require_python_image()
    monkeypatch.setenv("ENABLE_DOCKER_SANDBOX", "true")
    runner = DockerSandboxRunner(workspace_manager=SandboxWorkspaceManager(root=tmp_path))

    result = runner.run(make_task("print('gone')"))
    inspect = subprocess.run(["docker", "ps", "-aq", "--filter", f"name={result.sandbox_id}"], capture_output=True, text=True, timeout=10)

    assert inspect.stdout.strip() == ""


def test_timeout_kills_task(tmp_path, monkeypatch):
    require_python_image()
    monkeypatch.setenv("ENABLE_DOCKER_SANDBOX", "true")
    runner = DockerSandboxRunner(workspace_manager=SandboxWorkspaceManager(root=tmp_path))

    result = runner.run(make_task("import time; time.sleep(10)", timeout_seconds=1))

    assert not result.ok
    assert result.exit_code == 124


def test_workspace_cleaned(tmp_path, monkeypatch):
    require_python_image()
    monkeypatch.setenv("ENABLE_DOCKER_SANDBOX", "true")
    monkeypatch.setenv("SANDBOX_KEEP_WORKSPACE_ON_FAILURE", "false")
    runner = DockerSandboxRunner(workspace_manager=SandboxWorkspaceManager(root=tmp_path))

    runner.run(make_task("print('clean')"))

    assert list(tmp_path.iterdir()) == []