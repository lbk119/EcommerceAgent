from __future__ import annotations

import os
import time
import uuid

from locust import HttpUser, between, task


class EcommerceAgentUser(HttpUser):
    wait_time = between(1, 3)
    host = os.getenv("GATEWAY_URL", "http://127.0.0.1:9090")

    def on_start(self) -> None:
        password = "Admin123456"
        email = f"locust_{uuid.uuid4().hex[:12]}@example.com"
        auth = self.client.post("/api/v1/auth/register", json={"companyName": "Locust Team", "name": "Load User", "email": email, "password": password, "confirmPassword": password}).json()
        self.headers = {"Authorization": f"Bearer {auth['accessToken']}"}
        onboarding = self.client.post("/api/v1/onboarding/complete", headers=self.headers, json={"shopName": "Load Orange Shop", "category": "Orange", "shopType": "Brand Owned", "businessStage": "Growth", "selectedPlatforms": ["Taobao"], "dataMode": "sample", "enabledAgentIds": ["store-analyst", "product-assistant", "inventory-inspector", "campaign-reviewer", "report-specialist"]}).json()
        shop_id = onboarding.get("workspace", {}).get("currentShopId") or onboarding.get("shop", {}).get("id")
        shop_auth = self.client.post("/api/v1/auth/shops", headers=self.headers, json={"shop_id": shop_id, "shop_name": "Load Orange Shop"}).json()
        self.headers = {"Authorization": f"Bearer {shop_auth['accessToken']}", "X-Shop-ID": shop_id}
        self.client.post("/api/v1/account/onboarding-completed", headers=self.headers, json={})
        self.client.post("/api/v1/data-import/sample", headers=self.headers)

    @task(5)
    def ai_chat_task(self) -> None:
        started = time.perf_counter()
        response = self.client.post("/api/v1/ai-chat/messages", headers=self.headers, json={"content": "topproduct recommendation"}, name="ai_chat_acceptance")
        response_time_ms = (time.perf_counter() - started) * 1000
        if response_time_ms > 1000:
            response.failure(f"AI Chat acceptance P95 target breach sample: {response_time_ms:.0f}ms")
        if response.ok:
            task_id = response.json().get("taskId")
            if task_id:
                self.client.get(f"/api/v1/ai-chat/tasks/{task_id}/timeline", headers=self.headers, name="ai_chat_timeline")

    @task(1)
    def standard_agent_job(self) -> None:
        response = self.client.post("/api/v1/agents/product-assistant/jobs", headers=self.headers, json={"jobType": "product_optimization", "title": "Load product optimization", "params": {}}, name="agent_job_acceptance")
        if response.ok:
            job_id = response.json().get("job", {}).get("jobId")
            if job_id:
                self.client.get(f"/api/v1/agents/product-assistant/jobs/{job_id}", headers=self.headers, name="agent_job_poll")