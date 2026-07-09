from __future__ import annotations

import os
import socket
import sys
import uuid
from pathlib import Path
from typing import Any

import pytest
import requests


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def gateway_url() -> str:
    return os.getenv("GATEWAY_URL", "http://127.0.0.1:9090").rstrip("/")


def gateway_available(url: str) -> bool:
    try:
        host_port = url.split("://", 1)[-1].split("/", 1)[0]
        host, _, port = host_port.partition(":")
        with socket.create_connection((host, int(port or 80)), timeout=1):
            return True
    except OSError:
        return False


@pytest.fixture(scope="session")
def api_base() -> str:
    return f"{gateway_url()}/api/v1"


@pytest.fixture(scope="session")
def running_gateway() -> str:
    url = gateway_url()
    if not gateway_available(url):
        pytest.skip(f"gateway is not running at {url}")
    response = requests.get(f"{url}/health", timeout=5)
    if response.status_code != 200:
        pytest.skip(f"gateway health check failed: {response.status_code}")
    return url


def unique_email(prefix: str = "pytest") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}@example.com"


def post_json(url: str, body: dict[str, Any], headers: dict[str, str] | None = None, timeout: int = 30) -> dict[str, Any]:
    response = requests.post(url, json=body, headers=headers or {}, timeout=timeout)
    response.raise_for_status()
    return response.json()


def get_json(url: str, headers: dict[str, str] | None = None, timeout: int = 30) -> dict[str, Any]:
    response = requests.get(url, headers=headers or {}, timeout=timeout)
    response.raise_for_status()
    return response.json()