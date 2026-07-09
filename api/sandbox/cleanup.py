"""Cleanup helpers for stale sandbox containers and workspaces."""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

from api.sandbox.docker_runner import docker_available


def cleanup_stale_containers(max_age_seconds: int = 3600) -> int:
    """Remove only EcommerceAgent sandbox containers with the expected labels."""
    if not docker_available():
        return 0
    command = [
        "docker",
        "ps",
        "-aq",
        "--filter",
        "label=app=ecommerce-agent",
        "--filter",
        "label=component=sandbox",
    ]
    completed = subprocess.run(command, capture_output=True, text=True, timeout=10)
    ids = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    removed = 0
    for container_id in ids:
        subprocess.run(["docker", "rm", "-f", container_id], capture_output=True, text=True, timeout=10)
        removed += 1
    return removed


def cleanup_stale_workspaces(root: Path | None = None, max_age_seconds: int = 3600) -> int:
    sandbox_root = (root or Path(os.getenv("SANDBOX_ROOT", Path(__file__).resolve().parents[2] / "output" / "sandbox"))).resolve()
    if not sandbox_root.exists():
        return 0
    now = time.time()
    removed = 0
    for child in sandbox_root.iterdir():
        if not child.is_dir():
            continue
        try:
            if now - child.stat().st_mtime > max_age_seconds:
                shutil.rmtree(child, ignore_errors=True)
                removed += 1
        except OSError:
            continue
    return removed