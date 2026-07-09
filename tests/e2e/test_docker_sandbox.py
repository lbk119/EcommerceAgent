from __future__ import annotations

import os
import time

import pytest
import requests

from agent.sandbox.models import SandboxFile, SandboxNetworkPolicy, SandboxTask
from api.sandbox.docker_runner import docker_available, docker_image_available


def brain_url() -> str:
    return os.getenv("PYTHON_BRAIN_URL", "http://127.0.0.1:9000").rstrip("/")


def brain_available() -> bool:
    try:
        response = requests.get(f"{brain_url()}/api/agent-runtime/health", timeout=5)
        return response.status_code == 200
    except requests.RequestException:
        return False


def internal_headers() -> dict[str, str]:
    return {"X-Sandbox-Internal-Token": os.getenv("SANDBOX_SERVER_INTERNAL_TOKEN", "dev-sandbox-token-change-me")}


def execute(task: SandboxTask) -> dict:
    response = requests.post(f"{brain_url()}/api/v1/sandbox/execute", json=task.model_dump(mode="json"), headers=internal_headers(), timeout=60)
    response.raise_for_status()
    return response.json()


def make_task(**overrides) -> SandboxTask:
    data = {
        "task_id": f"sandbox-e2e-{int(time.time() * 1000)}",
        "conversation_id": "sandbox-e2e-conversation",
        "tenant_id": "tenant-e2e",
        "user_id": "user-e2e",
        "shop_id": "shop-e2e",
        "profile": "standard",
        "agent_id": "e2e-agent",
        "tool_name": "read_file_content",
        "runtime": "python",
        "code": "print('ok')",
    }
    data.update(overrides)
    return SandboxTask(**data)


pytestmark = pytest.mark.e2e


def require_brain():
    if not brain_available():
        pytest.skip(f"Python Brain is not running at {brain_url()}")


def test_ai_chat_realtime_shell_is_rejected():
    require_brain()
    task = make_task(profile="realtime", tool_name="sandbox_shell", runtime="shell", command=["sh", "-c", "echo nope"], code=None)

    result = execute(task)

    assert not result["ok"]
    assert "realtime" in result["denied_reason"]


def test_standard_job_cannot_read_env_path():
    require_brain()
    task = make_task(input_files=[SandboxFile.from_text(".env", "SECRET=1")])

    result = execute(task)

    assert not result["ok"]
    assert ".env" in result["denied_reason"]


def test_standard_sandbox_python_processes_csv():
    require_brain()
    if not docker_available() or not docker_image_available("ecommerce-agent-sandbox-python:latest"):
        pytest.skip("Docker or sandbox python image is unavailable")
    task = make_task(
        code="""
import csv
from pathlib import Path
rows = list(csv.DictReader((Path('/workspace') / 'input' / 'orders.csv').open()))
total = sum(float(row['amount']) for row in rows)
Path('/workspace/output/summary.txt').write_text(f'total={total}', encoding='utf-8')
print(f'total={total}')
""",
        input_files=[SandboxFile.from_text("input/orders.csv", "order_id,amount\n1,10.5\n2,2.5\n")],
    )

    result = execute(task)

    assert result["ok"]
    assert "total=13.0" in result["stdout"]
    assert result["output_files"]


def test_deep_network_rejects_localhost():
    require_brain()
    task = make_task(profile="deep", network_policy=SandboxNetworkPolicy(mode="allowlist", allowed_domains=["localhost"]))

    result = execute(task)

    assert not result["ok"]
    assert "localhost" in result["denied_reason"] or "blocked" in result["denied_reason"]


def test_trace_contains_sandbox_event():
    require_brain()
    task = make_task(profile="realtime", tool_name="sandbox_shell", runtime="shell", command=["sh", "-c", "echo nope"], code=None)

    result = execute(task)
    assert not result["ok"]
    assert _trace_contains(task.task_id, {"sandbox_task_denied", "sandbox_task_finished"})


def _trace_contains(task_id: str, event_types: set[str]) -> bool:
    trace_path = os.getenv("AGENT_TRACE_PATH", "data/memory/agent_traces.jsonl")
    for _ in range(10):
        if os.path.exists(trace_path):
            with open(trace_path, "r", encoding="utf-8") as handle:
                tail = handle.readlines()[-200:]
            if any(task_id in line and any(event_type in line for event_type in event_types) for line in tail):
                return True
        time.sleep(0.5)
    return False