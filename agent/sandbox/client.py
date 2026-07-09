"""Agent-side sandbox client.

Tools submit SandboxTask objects to the FastAPI sandbox server. This module must
not import Docker SDKs or call Docker CLI.
"""

from __future__ import annotations

import base64
import os
import uuid
from pathlib import Path
from typing import Any, Mapping

import httpx

from api.context import get_identity_context, get_session_context, get_thread_context
from agent.sandbox.errors import SandboxUnavailableError
from agent.sandbox.models import SandboxFile, SandboxNetworkPolicy, SandboxResourceLimits, SandboxResult, SandboxTask
from agent.sandbox.policy import AgentSandboxPolicy


class SandboxClient:
    """HTTP client for the internal sandbox server."""

    def __init__(self, base_url: str | None = None, internal_token: str | None = None, timeout_seconds: float | None = None):
        self.base_url = (base_url or os.getenv("SANDBOX_SERVER_URL") or os.getenv("PYTHON_BRAIN_URL") or "http://127.0.0.1:9000").rstrip("/")
        self.internal_token = internal_token or os.getenv("SANDBOX_SERVER_INTERNAL_TOKEN", "dev-sandbox-token-change-me")
        self.timeout_seconds = float(timeout_seconds or os.getenv("SANDBOX_CLIENT_TIMEOUT_SECONDS", "130"))
        self.policy = AgentSandboxPolicy()

    def execute(self, task: SandboxTask) -> SandboxResult:
        decision = self.policy.decide(task)
        if not decision.allowed:
            # Shell can still be enabled server-side; do not block it here when the caller explicitly asks.
            if task.runtime != "shell":
                return SandboxResult(ok=False, denied_reason=decision.reason, trace_id=task.task_id)
        try:
            response = httpx.post(
                f"{self.base_url}/api/v1/sandbox/execute",
                json=task.model_dump(mode="json"),
                headers={"X-Sandbox-Internal-Token": self.internal_token},
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            return SandboxResult.model_validate(response.json())
        except httpx.HTTPStatusError as error:
            try:
                payload = error.response.json()
            except Exception:
                payload = {"detail": error.response.text}
            return SandboxResult(ok=False, denied_reason=str(payload.get("detail") or payload), trace_id=task.task_id)
        except httpx.HTTPError as error:
            raise SandboxUnavailableError(str(error)) from error

    def execute_python(self, code: str, files: list[SandboxFile] | None = None, context: Mapping[str, Any] | None = None) -> SandboxResult:
        return self.execute(self._task("python", code=code, files=files, context=context, tool_name=str((context or {}).get("tool_name") or "sandbox_python")))

    def execute_shell(self, command: list[str], files: list[SandboxFile] | None = None, context: Mapping[str, Any] | None = None) -> SandboxResult:
        return self.execute(self._task("shell", command=command, files=files, context=context, tool_name=str((context or {}).get("tool_name") or "sandbox_shell")))

    def execute_node(self, code: str, files: list[SandboxFile] | None = None, context: Mapping[str, Any] | None = None) -> SandboxResult:
        return self.execute(self._task("node", code=code, files=files, context=context, tool_name=str((context or {}).get("tool_name") or "sandbox_node")))

    def run_file_task(self, entrypoint: str, files: list[SandboxFile] | None = None, context: Mapping[str, Any] | None = None) -> SandboxResult:
        return self.execute(self._task("file", command=[entrypoint], files=files, context=context, tool_name=str((context or {}).get("tool_name") or "sandbox_file")))

    def _task(
        self,
        runtime: str,
        *,
        code: str | None = None,
        command: list[str] | None = None,
        files: list[SandboxFile] | None = None,
        context: Mapping[str, Any] | None = None,
        tool_name: str,
    ) -> SandboxTask:
        ctx = dict(context or {})
        identity = get_identity_context()
        task_id = str(ctx.get("task_id") or getattr(identity, "task_id", "") or f"sandbox-{uuid.uuid4().hex}")
        conversation_id = str(ctx.get("conversation_id") or get_thread_context() or getattr(identity, "conversation_id", "") or task_id)
        return SandboxTask(
            task_id=task_id,
            conversation_id=conversation_id,
            tenant_id=str(ctx.get("tenant_id") or getattr(identity, "tenant_id", "default_tenant")),
            user_id=str(ctx.get("user_id") or getattr(identity, "user_id", "local_user")),
            shop_id=str(ctx.get("shop_id") or getattr(identity, "shop_id", "default_shop")),
            profile=str(ctx.get("profile") or "standard"),
            agent_id=str(ctx.get("agent_id") or ctx.get("active_subagent") or "deepagents_tool"),
            tool_name=tool_name,
            runtime=runtime,
            command=command or [],
            code=code,
            input_files=files or [],
            env={key: str(value) for key, value in dict(ctx.get("env") or {}).items()},
            timeout_seconds=int(ctx.get("timeout_seconds") or 30),
            network_policy=ctx.get("network_policy") or SandboxNetworkPolicy(),
            resource_limits=ctx.get("resource_limits") or SandboxResourceLimits(timeout_seconds=int(ctx.get("timeout_seconds") or 30)),
            metadata={"session_dir": get_session_context() or "", **dict(ctx.get("metadata") or {})},
        )


def sandbox_file_from_path(path: Path, relative_path: str | None = None) -> SandboxFile:
    data = path.read_bytes()
    return SandboxFile(relative_path=relative_path or path.name, content_base64=base64.b64encode(data).decode("ascii"))