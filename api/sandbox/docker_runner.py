"""Docker CLI based sandbox runner.

Docker is only touched from this API-side module. Agent and tool modules submit
SandboxTask objects through the SandboxClient instead.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path

from agent.sandbox.models import SandboxResult, SandboxTask
from api.sandbox.audit import emit_sandbox_event
from api.sandbox.policy import SandboxPolicyEngine
from api.sandbox.workspace import SandboxWorkspaceManager, WorkspaceSecurityError


def docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        subprocess.run(["docker", "version", "--format", "{{.Server.Version}}"], capture_output=True, text=True, timeout=5, check=True)
        return True
    except Exception:
        return False


def docker_image_available(image: str) -> bool:
    if not docker_available():
        return False
    try:
        subprocess.run(["docker", "image", "inspect", image], capture_output=True, text=True, timeout=5, check=True)
        return True
    except Exception:
        return False


class DockerSandboxRunner:
    """Runs one SandboxTask in one ephemeral Docker container."""

    def __init__(self, workspace_manager: SandboxWorkspaceManager | None = None, policy_engine: SandboxPolicyEngine | None = None):
        self.workspace_manager = workspace_manager or SandboxWorkspaceManager()
        self.policy_engine = policy_engine or SandboxPolicyEngine()

    def run(self, task: SandboxTask) -> SandboxResult:
        started = time.perf_counter()
        sandbox_id = f"ecommerce-sandbox-{uuid.uuid4().hex[:18]}"
        workspace: Path | None = None
        image = _image_for_runtime(task.runtime)
        emit_sandbox_event("sandbox_task_requested", task, image=image)
        if not docker_available():
            emit_sandbox_event("sandbox_task_failed", task, error="docker unavailable", image=image)
            return SandboxResult(ok=False, denied_reason="Docker is not available", sandbox_id=sandbox_id, trace_id=task.task_id)
        try:
            limits = self.policy_engine.normalized_limits(task)
            workspace = self.workspace_manager.create_workspace(task)
            self.workspace_manager.write_input_files(workspace, task.input_files)
            command = self._prepare_command(task, workspace)
            docker_command = self._docker_command(task, sandbox_id, image, workspace, command, limits)
            emit_sandbox_event("sandbox_container_created", task, image=image, sandbox_id=sandbox_id)
            try:
                completed = subprocess.run(
                    docker_command,
                    capture_output=True,
                    text=True,
                    timeout=limits.timeout_seconds + 2,
                    encoding="utf-8",
                    errors="replace",
                )
            except subprocess.TimeoutExpired:
                subprocess.run(["docker", "rm", "-f", sandbox_id], capture_output=True, text=True, timeout=10)
                emit_sandbox_event("sandbox_task_failed", task, error="sandbox timeout", image=image, sandbox_id=sandbox_id)
                return SandboxResult(ok=False, exit_code=124, stderr="sandbox timeout", duration_ms=_elapsed_ms(started), sandbox_id=sandbox_id, trace_id=task.task_id)
            output_files = self.workspace_manager.collect_output_files(workspace)
            stdout = _truncate(completed.stdout, task.max_stdout_bytes)
            stderr = _truncate(completed.stderr, task.max_stderr_bytes)
            result = SandboxResult(
                ok=completed.returncode == 0,
                exit_code=completed.returncode,
                stdout=stdout,
                stderr=stderr,
                output_files=output_files,
                duration_ms=_elapsed_ms(started),
                sandbox_id=sandbox_id,
                trace_id=task.task_id,
                resource_usage={"limits": limits.model_dump(mode="json")},
                metadata={"image": image},
            )
            emit_sandbox_event("sandbox_task_finished", task, image=image, sandbox_id=sandbox_id, exit_code=completed.returncode, duration_ms=result.duration_ms)
            return result
        except WorkspaceSecurityError as error:
            emit_sandbox_event("sandbox_task_denied", task, error=str(error), image=image, sandbox_id=sandbox_id, denied_reason=str(error))
            return SandboxResult(ok=False, denied_reason=str(error), duration_ms=_elapsed_ms(started), sandbox_id=sandbox_id, trace_id=task.task_id)
        except Exception as error:
            emit_sandbox_event("sandbox_task_failed", task, error=str(error)[:500], image=image, sandbox_id=sandbox_id)
            subprocess.run(["docker", "rm", "-f", sandbox_id], capture_output=True, text=True, timeout=10)
            return SandboxResult(ok=False, stderr=str(error)[:1000], duration_ms=_elapsed_ms(started), sandbox_id=sandbox_id, trace_id=task.task_id)
        finally:
            keep = os.getenv("SANDBOX_KEEP_WORKSPACE_ON_FAILURE", "false").lower() in {"1", "true", "yes", "on"}
            if workspace is not None:
                self.workspace_manager.cleanup_workspace(workspace, keep=keep)
            emit_sandbox_event("sandbox_container_removed", task, image=image, sandbox_id=sandbox_id)

    def _prepare_command(self, task: SandboxTask, workspace: Path) -> list[str]:
        if task.code and task.runtime == "python":
            entrypoint = workspace / "__sandbox_entry.py"
            entrypoint.write_text(task.code, encoding="utf-8")
            return ["python", "/workspace/__sandbox_entry.py"]
        if task.code and task.runtime == "node":
            entrypoint = workspace / "__sandbox_entry.mjs"
            entrypoint.write_text(task.code, encoding="utf-8")
            return ["node", "/workspace/__sandbox_entry.mjs"]
        return list(task.command)

    def _docker_command(self, task: SandboxTask, sandbox_id: str, image: str, workspace: Path, command: list[str], limits) -> list[str]:
        network = "none"  # first phase: network allowlist is server-proxy only, never raw container network.
        mount = f"{workspace.resolve()}:/workspace:rw"
        return [
            "docker",
            "run",
            "--name",
            sandbox_id,
            "--rm",
            "--network",
            network,
            "--read-only",
            "--tmpfs",
            "/tmp:rw,nosuid,nodev,size=64m",
            "-v",
            mount,
            "-w",
            "/workspace",
            "--memory",
            f"{limits.memory_mb}m",
            "--cpus",
            str(limits.cpu_count),
            "--pids-limit",
            str(limits.pids_limit),
            "--security-opt",
            "no-new-privileges",
            "--cap-drop",
            "ALL",
            "--user",
            "1000:1000",
            "--label",
            "app=ecommerce-agent",
            "--label",
            "component=sandbox",
            "--label",
            f"task_id={task.task_id}",
            "--label",
            f"tenant_id={task.tenant_id}",
            "--label",
            f"profile={task.profile}",
            image,
            *command,
        ]


def _image_for_runtime(runtime: str) -> str:
    if runtime == "python":
        return os.getenv("SANDBOX_PYTHON_IMAGE", "ecommerce-agent-sandbox-python:latest")
    if runtime == "node":
        return os.getenv("SANDBOX_NODE_IMAGE", "ecommerce-agent-sandbox-node:latest")
    return os.getenv("SANDBOX_BASE_IMAGE", "ecommerce-agent-sandbox-base:latest")


def _truncate(value: str, max_bytes: int) -> str:
    data = value.encode("utf-8", errors="replace")
    if len(data) <= max_bytes:
        return value
    return data[:max_bytes].decode("utf-8", errors="replace") + "\n[truncated]"


def _elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 2)