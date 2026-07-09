from __future__ import annotations

import time

import pytest

from tests.conftest import get_json, post_json, unique_email


AGENTS = ["store-analyst", "product-assistant", "inventory-inspector", "campaign-reviewer", "report-specialist"]


def register_and_onboard(api_base: str) -> dict[str, str]:
    password = "Admin123456"
    auth = post_json(
        f"{api_base}/auth/register",
        {"companyName": "Pytest Team", "name": "Pytest User", "email": unique_email("e2e"), "password": password, "confirmPassword": password},
        timeout=60,
    )
    headers = {"Authorization": f"Bearer {auth['accessToken']}"}
    onboarding = post_json(
        f"{api_base}/onboarding/complete",
        {"shopName": "Pytest Orange Shop", "category": "Orange", "shopType": "Brand Owned", "businessStage": "Growth", "selectedPlatforms": ["Taobao"], "dataMode": "sample", "enabledAgentIds": AGENTS},
        headers=headers,
        timeout=60,
    )
    shop_id = onboarding.get("workspace", {}).get("currentShopId") or onboarding.get("shop", {}).get("id")
    assert shop_id
    shop_auth = post_json(f"{api_base}/auth/shops", {"shop_id": shop_id, "shop_name": "Pytest Orange Shop"}, headers=headers, timeout=60)
    headers = {"Authorization": f"Bearer {shop_auth['accessToken']}", "X-Shop-ID": shop_id}
    post_json(f"{api_base}/account/onboarding-completed", {}, headers=headers, timeout=60)
    post_json(f"{api_base}/data-import/sample", {}, headers=headers, timeout=120)
    return headers


def wait_timeline(api_base: str, headers: dict[str, str], task_id: str, min_events: int = 3) -> dict:
    timeline = {"events": []}
    for _ in range(20):
        timeline = get_json(f"{api_base}/ai-chat/tasks/{task_id}/timeline", headers=headers, timeout=30)
        if len(timeline.get("events") or []) >= min_events:
            return timeline
        time.sleep(1)
    return timeline


@pytest.mark.e2e
def test_ai_chat_accepts_business_tasks_as_standard_runtime(running_gateway: str, api_base: str) -> None:
    headers = register_and_onboard(api_base)
    cases = [
        ("seasonal product selection", "seasonal_selection"),
        ("which product should I optimize", "product_optimization"),
        ("topproduct recommendation", "hot_product_analysis"),
    ]

    for content, expected_intent in cases:
        started = time.perf_counter()
        chat = post_json(f"{api_base}/ai-chat/messages", {"content": content}, headers=headers, timeout=60)
        accepted_ms = (time.perf_counter() - started) * 1000

        assert chat["acceptedLatencyMs"] < 1000
        assert accepted_ms < 10000
        assert chat["status"] == "running"
        assert chat["intent"] == expected_intent
        assert chat["runtimeProfile"] == "standard"
        assert chat.get("taskId")
        assert chat.get("messageId")

        timeline = wait_timeline(api_base, headers, chat["taskId"], min_events=3)
        event_text = " ".join(str(event.get("event_type", "")) + " " + str(event.get("agent_name", "")) for event in timeline.get("events") or [])
        lowered = event_text.lower()
        assert "deepagents_native" in lowered or "deepagents_main_agent" in lowered
        assert "deepagentruntime" not in lowered
