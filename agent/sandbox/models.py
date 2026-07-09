"""Shared sandbox API models.

The Agent process can construct these models, but only the FastAPI sandbox server
is allowed to create Docker containers or call Docker APIs.
"""

from __future__ import annotations

import base64
import uuid
from typing import Any, Dict, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


SandboxProfile = Literal["realtime", "standard", "deep"]
SandboxRuntime = Literal["python", "node", "shell", "file"]
SandboxNetworkMode = Literal["none", "allowlist"]


class SandboxFile(BaseModel):
    """A small file copied into or out of a sandbox workspace."""

    model_config = ConfigDict(extra="forbid")

    relative_path: str = Field(..., min_length=1, max_length=260)
    content_base64: str = ""
    mode: str = Field(default="0644", pattern=r"^[0-7]{3,4}$")

    @field_validator("relative_path")
    @classmethod
    def validate_relative_path(cls, value: str) -> str:
        normalized = value.replace("\\", "/").strip()
        if not normalized:
            raise ValueError("relative_path is required")
        if normalized.startswith("/") or ":" in normalized.split("/", 1)[0]:
            raise ValueError("relative_path must not be absolute")
        parts = [part for part in normalized.split("/") if part]
        if any(part == ".." for part in parts):
            raise ValueError("relative_path must not contain '..'")
        return "/".join(parts)

    def decoded_bytes(self) -> bytes:
        try:
            return base64.b64decode(self.content_base64.encode("ascii"), validate=True)
        except Exception as error:
            raise ValueError(f"invalid base64 content for {self.relative_path}") from error

    @classmethod
    def from_bytes(cls, relative_path: str, content: bytes, mode: str = "0644") -> "SandboxFile":
        return cls(relative_path=relative_path, content_base64=base64.b64encode(content).decode("ascii"), mode=mode)

    @classmethod
    def from_text(cls, relative_path: str, content: str, mode: str = "0644") -> "SandboxFile":
        return cls.from_bytes(relative_path, content.encode("utf-8"), mode=mode)


class SandboxResourceLimits(BaseModel):
    """Container resource limits."""

    model_config = ConfigDict(extra="forbid")

    cpu_count: float = Field(default=1, gt=0, le=8)
    memory_mb: int = Field(default=512, ge=128, le=8192)
    pids_limit: int = Field(default=128, ge=32, le=1024)
    disk_mb: int = Field(default=256, ge=16, le=4096)
    timeout_seconds: int = Field(default=30, ge=1, le=600)


class SandboxNetworkPolicy(BaseModel):
    """Requested network policy for a task."""

    model_config = ConfigDict(extra="forbid")

    mode: SandboxNetworkMode = "none"
    allowed_domains: list[str] = Field(default_factory=list)
    allowed_ports: list[int] = Field(default_factory=list)

    @field_validator("allowed_domains")
    @classmethod
    def normalize_domains(cls, value: list[str]) -> list[str]:
        return sorted({item.strip().lower().rstrip(".") for item in value if item.strip()})

    @field_validator("allowed_ports")
    @classmethod
    def validate_ports(cls, value: list[int]) -> list[int]:
        return sorted({port for port in value if 1 <= int(port) <= 65535})


class SandboxTask(BaseModel):
    """Request submitted by Agent-side tools to the sandbox server."""

    model_config = ConfigDict(extra="forbid")

    task_id: str = Field(..., min_length=1)
    conversation_id: str = Field(..., min_length=1)
    tenant_id: str = Field(..., min_length=1)
    user_id: str = Field(..., min_length=1)
    shop_id: str = Field(..., min_length=1)
    profile: SandboxProfile
    agent_id: str = Field(..., min_length=1)
    tool_name: str = Field(..., min_length=1)
    runtime: SandboxRuntime
    command: list[str] = Field(default_factory=list)
    code: str | None = None
    input_files: list[SandboxFile] = Field(default_factory=list)
    env: Dict[str, str] = Field(default_factory=dict)
    timeout_seconds: int = Field(default=30, ge=1, le=600)
    max_stdout_bytes: int = Field(default=65536, ge=1024, le=2_000_000)
    max_stderr_bytes: int = Field(default=65536, ge=1024, le=2_000_000)
    network_policy: SandboxNetworkPolicy = Field(default_factory=SandboxNetworkPolicy)
    resource_limits: SandboxResourceLimits = Field(default_factory=SandboxResourceLimits)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("tool_name", "agent_id")
    @classmethod
    def normalize_identifier(cls, value: str) -> str:
        return value.strip()

    @field_validator("env")
    @classmethod
    def validate_env(cls, value: Dict[str, str]) -> Dict[str, str]:
        return {str(key): str(item) for key, item in value.items()}

    @model_validator(mode="after")
    def validate_runtime_payload(self) -> "SandboxTask":
        if self.runtime in {"python", "node"} and not self.code and not self.command:
            raise ValueError("python/node sandbox tasks require code or command")
        if self.runtime == "shell" and not self.command:
            raise ValueError("shell sandbox tasks require command")
        if self.runtime == "file" and not self.command:
            raise ValueError("file sandbox tasks require command")
        if self.timeout_seconds > self.resource_limits.timeout_seconds:
            self.resource_limits.timeout_seconds = self.timeout_seconds
        return self

    @classmethod
    def new_id(cls) -> str:
        return f"sandbox-{uuid.uuid4().hex}"


class SandboxResult(BaseModel):
    """Structured sandbox execution response."""

    model_config = ConfigDict(extra="forbid")

    ok: bool
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    output_files: list[SandboxFile] = Field(default_factory=list)
    duration_ms: float = 0
    sandbox_id: str = ""
    denied_reason: str | None = None
    trace_id: str = ""
    resource_usage: Dict[str, Any] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class SandboxDecision(BaseModel):
    """Sandbox policy decision."""

    model_config = ConfigDict(extra="forbid")

    allowed: bool
    reason: str = ""
    risk_level: Literal["low", "medium", "high"] = "low"
    required_approval: bool = False
