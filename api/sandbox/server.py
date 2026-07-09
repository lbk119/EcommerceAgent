"""Internal FastAPI router for Docker sandbox execution."""

from __future__ import annotations

import os

from fastapi import APIRouter, Header, HTTPException

from agent.sandbox.models import SandboxResult, SandboxTask
from api.sandbox.audit import emit_sandbox_event
from api.sandbox.docker_runner import DockerSandboxRunner, docker_available
from api.sandbox.policy import SandboxPolicyEngine


router = APIRouter(prefix="/api/v1/sandbox", tags=["sandbox"])
policy_engine = SandboxPolicyEngine()
runner = DockerSandboxRunner(policy_engine=policy_engine)


def verify_internal_token(token: str | None) -> None:
    expected = os.getenv("SANDBOX_SERVER_INTERNAL_TOKEN", "dev-sandbox-token-change-me")
    if not token or token != expected:
        raise HTTPException(status_code=403, detail="invalid sandbox internal token")


@router.get("/health")
async def sandbox_health(x_sandbox_internal_token: str | None = Header(default=None, alias="X-Sandbox-Internal-Token")):
    verify_internal_token(x_sandbox_internal_token)
    return {
        "enabled": os.getenv("ENABLE_DOCKER_SANDBOX", "true").lower() in {"1", "true", "yes", "on"},
        "dockerAvailable": docker_available(),
        "networkEnabled": os.getenv("SANDBOX_ENABLE_NETWORK", "false").lower() in {"1", "true", "yes", "on"},
    }


@router.post("/execute", response_model=SandboxResult)
async def execute_sandbox(task: SandboxTask, x_sandbox_internal_token: str | None = Header(default=None, alias="X-Sandbox-Internal-Token")) -> SandboxResult:
    verify_internal_token(x_sandbox_internal_token)
    decision = policy_engine.decide(task)
    if not decision.allowed:
        emit_sandbox_event("sandbox_task_denied", task, denied_reason=decision.reason, risk_level=decision.risk_level, required_approval=decision.required_approval)
        return SandboxResult(ok=False, denied_reason=decision.reason, trace_id=task.task_id)
    return runner.run(task)