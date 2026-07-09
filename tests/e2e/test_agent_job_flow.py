from __future__ import annotations

import pytest

from tests.e2e.test_ai_chat_flow import register_and_onboard
from tests.conftest import get_json, post_json, unique_email


@pytest.mark.e2e
def test_standard_agent_job_accepts_and_exposes_runtime_profile(running_gateway: str, api_base: str) -> None:
    headers = register_and_onboard(api_base)
    response = post_json(
        f"{api_base}/agents/product-assistant/jobs",
        {"jobType": "product_optimization", "title": "Pytest product optimization", "params": {}},
        headers=headers,
        timeout=60,
    )
    job = response["job"]

    assert job["jobId"]
    assert job["runtimeProfile"] == "standard"


@pytest.mark.e2e
def test_agent_runtime_slow_tasks_diagnostic_is_bounded(running_gateway: str, api_base: str) -> None:
    password = "Admin123456"
    auth = post_json(
        f"{api_base}/auth/register",
        {"companyName": "Pytest Diagnostics", "name": "Pytest Diagnostic User", "email": unique_email("diag"), "password": password, "confirmPassword": password},
        timeout=30,
    )
    headers = {"Authorization": f"Bearer {auth['accessToken']}"}

    slow_tasks = get_json(f"{api_base}/agent-runtime/slow-tasks?limit=5", headers=headers, timeout=5)

    assert "tasks" in slow_tasks
    if "diagnostic" in slow_tasks:
        assert slow_tasks["diagnostic"].get("source") in {"trace_tail", "gateway_bounded_proxy"}