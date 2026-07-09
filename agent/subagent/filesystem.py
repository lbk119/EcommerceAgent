"""deepagents virtual filesystem/backend 权限配置。

realtime 不启用 filesystem；standard 只允许写报告和任务 workspace；deep 使用 sandbox 风格后端。
这里还负责把 `/memories/`、`/policies/` 路由到 StoreBackend，把普通工作文件路由到 FilesystemBackend。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from deepagents import FilesystemPermission
from deepagents.backends import CompositeBackend, FilesystemBackend, StoreBackend

from agent.subagent.config import get_deepagents_profile
from agent.memory import memory_namespace


DENY_PATHS = ["/.env", "/secrets/**", "/agent/**", "/api/**", "/gateway/**", "/.git/**"]


def filesystem_permissions(profile: str, *, tenant_id: str = "*", shop_id: str = "*", task_id: str = "*") -> list[FilesystemPermission]:
    """返回指定 profile 下可读写路径的 allow/deny 规则。"""
    config = get_deepagents_profile(profile)
    if not config.allow_filesystem:
        return []
    permissions: list[FilesystemPermission] = [FilesystemPermission(operations=["read", "write"], paths=DENY_PATHS, mode="deny")]
    if config.name == "standard":
        permissions.append(FilesystemPermission(operations=["write"], paths=[f"/reports/{tenant_id}/{shop_id}/**", f"/workspace/{task_id}/**"], mode="allow"))
        permissions.append(FilesystemPermission(operations=["read"], paths=[f"/reports/{tenant_id}/{shop_id}/**", f"/workspace/{task_id}/**", "/memories/**", "/policies/**"], mode="allow"))
        return permissions
    if config.name == "deep":
        permissions.append(FilesystemPermission(operations=["read", "write"], paths=["/workspace/**", "/reports/**"], mode="allow"))
        permissions.append(FilesystemPermission(operations=["read"], paths=["/memories/**", "/policies/**"], mode="allow"))
    return permissions


def build_composite_backend(profile: str, *, store: Any = None, workspace_root: str | Path | None = None) -> CompositeBackend | FilesystemBackend | None:
    """按 profile 构造 deepagents backend，必要时组合 filesystem 与 store。"""
    config = get_deepagents_profile(profile)
    if not config.allow_filesystem and store is None:
        return None
    filesystem_backend = FilesystemBackend(root_dir=workspace_root, virtual_mode=True)
    if store is None:
        return filesystem_backend
    store_backend = StoreBackend(store=store, namespace=lambda runtime: memory_namespace(getattr(runtime, "config", None)))
    return CompositeBackend(default=filesystem_backend, routes={"/memories/": store_backend, "/policies/": store_backend})


def reject_path_traversal(path: str) -> None:
    """在真正访问文件前拒绝绝对路径和 `..` 逃逸路径。"""
    normalized = Path(path.replace("\\", "/"))
    if normalized.is_absolute() or ".." in normalized.parts:
        raise ValueError("path traversal is not allowed")
