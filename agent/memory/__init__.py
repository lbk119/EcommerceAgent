"""DeepAgents 记忆后端与租户身份工具。

本包定义生产/本地 memory store 的选择策略、长期记忆命名空间和任务身份对象。
生产环境应使用 LangSmith managed store、Postgres 或 Redis；`InMemoryStore` 只允许作为测试或本地降级。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from langgraph.store.memory import InMemoryStore


class MemoryBackendConfigurationError(RuntimeError):
    pass


@dataclass(frozen=True)
class MemoryBackend:
    backend: str
    store: Any
    persistence_ready: bool
    warning: str = ""


@dataclass(frozen=True)
class MemoryIdentity:
    """通过 runtime context 和工具层传递的租户、用户、店铺身份。"""

    tenant_id: str = "default_tenant"
    user_id: str = "local_user"
    shop_id: str = "default_shop"
    conversation_id: str = ""
    task_id: str = ""


class MemoryBackendFactory:
    """根据环境变量构造 LangGraph store 兼容的记忆后端。"""

    def __init__(self, env: dict[str, str] | None = None):
        self.env = env or os.environ

    def build(self, *, production: bool = False) -> MemoryBackend:
        """返回当前环境的 memory backend，并在生产环境拒绝非持久化方案。"""
        backend = (self.env.get("DEEPAGENTS_STORE_BACKEND") or ("postgres" if production else "memory")).lower()
        if backend == "langsmith":
            return MemoryBackend("langsmith", None, True, "LangSmith Deployment managed store is expected to be provisioned by the platform.")
        if backend == "postgres":
            return self._postgres()
        if backend == "redis":
            return self._redis()
        if backend == "filesystem":
            if production:
                raise MemoryBackendConfigurationError("FilesystemBackend is only for local development; configure DEEPAGENTS_STORE_BACKEND=postgres or langsmith in production.")
            return MemoryBackend("filesystem", InMemoryStore(), False, "Local filesystem/memory fallback is not suitable for production long-term memory.")
        if backend == "memory":
            if production:
                raise MemoryBackendConfigurationError("InMemoryStore is not allowed as production memory; configure Postgres, Redis, or LangSmith managed store.")
            return MemoryBackend("memory", InMemoryStore(), False, "InMemoryStore is local/test fallback only and is not suitable for production.")
        raise MemoryBackendConfigurationError(f"Unsupported DEEPAGENTS_STORE_BACKEND={backend}. Expected langsmith|postgres|redis|filesystem|memory.")

    def _postgres(self) -> MemoryBackend:
        url = self.env.get("DEEPAGENTS_POSTGRES_URL") or self.env.get("DATABASE_URL")
        if not url:
            raise MemoryBackendConfigurationError("DEEPAGENTS_STORE_BACKEND=postgres requires DEEPAGENTS_POSTGRES_URL or DATABASE_URL.")
        try:
            from langgraph.store.postgres import PostgresStore  # type: ignore
        except Exception as error:
            raise MemoryBackendConfigurationError("Postgres LangGraph store dependency is missing. Install the langgraph postgres store package and set DEEPAGENTS_POSTGRES_URL.") from error
        return MemoryBackend("postgres", PostgresStore.from_conn_string(url), True)

    def _redis(self) -> MemoryBackend:
        url = self.env.get("DEEPAGENTS_REDIS_URL") or self.env.get("REDIS_URL")
        if not url:
            raise MemoryBackendConfigurationError("DEEPAGENTS_STORE_BACKEND=redis requires DEEPAGENTS_REDIS_URL or REDIS_URL.")
        try:
            from langgraph.store.redis import RedisStore  # type: ignore
        except Exception as error:
            raise MemoryBackendConfigurationError("Redis LangGraph store dependency is missing. Install the langgraph redis store package and set DEEPAGENTS_REDIS_URL.") from error
        return MemoryBackend("redis", RedisStore.from_conn_string(url), True)


def memory_namespace(config: dict[str, Any] | None = None) -> tuple[str, ...]:
    configurable = (config or {}).get("configurable", {}) if isinstance(config, dict) else {}
    mode = os.getenv("DEEPAGENTS_MEMORY_NAMESPACE_MODE", "tenant_shop_user")
    tenant_id = str(configurable.get("tenant_id") or "default_tenant")
    shop_id = str(configurable.get("shop_id") or "default_shop")
    user_id = str(configurable.get("user_id") or "local_user")
    if mode == "tenant_shop":
        return ("tenant", tenant_id, "shop", shop_id)
    if mode == "assistant":
        return ("assistant", str(configurable.get("assistant_id") or "main_agent"))
    return ("tenant", tenant_id, "shop", shop_id, "user", user_id)


__all__ = [
    "MemoryBackend",
    "MemoryBackendConfigurationError",
    "MemoryBackendFactory",
    "MemoryIdentity",
    "memory_namespace",
]
