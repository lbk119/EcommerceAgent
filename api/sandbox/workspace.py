"""Ephemeral workspace management for Docker sandbox tasks."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from agent.sandbox.models import SandboxFile, SandboxTask


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DENIED_NAMES = {".env", ".git", ".venv", "node_modules", "dockerfile", "docker-compose.yml", "docker-compose.yaml"}


class WorkspaceSecurityError(ValueError):
    """Raised for unsafe sandbox workspace paths or sizes."""


class SandboxWorkspaceManager:
    """Creates task-local workspaces and limits all input/output file access."""

    def __init__(self, root: Path | None = None, max_input_bytes: int | None = None, max_output_bytes: int | None = None):
        self.root = (root or Path(os.getenv("SANDBOX_ROOT", PROJECT_ROOT / "output" / "sandbox"))).resolve()
        self.max_input_bytes = int(max_input_bytes or os.getenv("SANDBOX_MAX_INPUT_BYTES", "10485760"))
        self.max_output_bytes = int(max_output_bytes or os.getenv("SANDBOX_MAX_OUTPUT_BYTES", "10485760"))

    def create_workspace(self, task: SandboxTask) -> Path:
        workspace = (self.root / _safe_workspace_name(task.task_id)).resolve()
        if not _is_relative_to(workspace, self.root):
            raise WorkspaceSecurityError("workspace escaped sandbox root")
        workspace.mkdir(parents=True, exist_ok=True)
        (workspace / "output").mkdir(exist_ok=True)
        _best_effort_chmod(workspace, 0o755)
        _best_effort_chmod(workspace / "output", 0o777)
        return workspace

    def write_input_files(self, workspace: Path, files: list[SandboxFile]) -> None:
        total = 0
        for item in files:
            relative = validate_relative_path(item.relative_path)
            data = item.decoded_bytes()
            total += len(data)
            if total > self.max_input_bytes:
                raise WorkspaceSecurityError("sandbox input files exceed size limit")
            target = (workspace / relative).resolve()
            if not _is_relative_to(target, workspace):
                raise WorkspaceSecurityError("input path escaped workspace")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            _best_effort_chmod(target.parent, 0o755)
            _best_effort_chmod(target, 0o644)

    def collect_output_files(self, workspace: Path) -> list[SandboxFile]:
        output_dir = (workspace / "output").resolve()
        if not output_dir.exists():
            return []
        collected: list[SandboxFile] = []
        total = 0
        for file_path in sorted(output_dir.rglob("*")):
            if not file_path.is_file():
                continue
            resolved = file_path.resolve()
            if not _is_relative_to(resolved, output_dir):
                continue
            relative = resolved.relative_to(output_dir).as_posix()
            validate_relative_path(relative)
            data = resolved.read_bytes()
            total += len(data)
            if total > self.max_output_bytes:
                raise WorkspaceSecurityError("sandbox output files exceed size limit")
            collected.append(SandboxFile.from_bytes(relative, data))
        return collected

    def cleanup_workspace(self, workspace: Path, *, keep: bool = False) -> None:
        if keep:
            return
        resolved = workspace.resolve()
        if _is_relative_to(resolved, self.root) and resolved.exists():
            shutil.rmtree(resolved, ignore_errors=True)


def validate_relative_path(value: str) -> str:
    path = value.replace("\\", "/").strip()
    if not path:
        raise WorkspaceSecurityError("empty path is not allowed")
    if path.startswith("/") or ":" in path.split("/", 1)[0]:
        raise WorkspaceSecurityError("absolute paths are not allowed")
    parts = [part for part in path.split("/") if part]
    if any(part == ".." for part in parts):
        raise WorkspaceSecurityError("path traversal is not allowed")
    lowered = {part.lower() for part in parts}
    if lowered & DENIED_NAMES:
        raise WorkspaceSecurityError(f"denied sandbox path: {path}")
    return "/".join(parts)


def _safe_workspace_name(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in value)[:120] or "sandbox-task"


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _best_effort_chmod(path: Path, mode: int) -> None:
    try:
        path.chmod(mode)
    except OSError:
        return