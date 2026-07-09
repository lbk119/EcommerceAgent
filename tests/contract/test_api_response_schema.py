from __future__ import annotations

import pytest

from tests.conftest import get_json


@pytest.mark.e2e
def test_health_response_contract(running_gateway: str) -> None:
    health = get_json(f"{running_gateway}/health", timeout=5)

    assert health["status"] == "ok"
    assert "user_store_backend" in health