from __future__ import annotations

import sys
import types

import pytest

from agent.subagent.config import get_deepagents_profile
from agent.subagent.filesystem import filesystem_permissions, reject_path_traversal
from agent.subagent.guard import GuardViolation, RuntimeGuard, guard_middleware, reset_active_runtime_guard, set_active_runtime_guard
from agent.subagent.hitl import hitl_required_for_tool, interrupt_on_for_profile
from agent.subagent.mcp import assert_mcp_tool_allowed, mcp_policy_for_profile
from agent.memory import MemoryBackendConfigurationError, MemoryBackendFactory, memory_namespace
from agent.subagent.subagents import build_deepagents_subagents, get_subagent_specs
from agent.subagent.tools import reset_tool_context, set_tool_context
from scripts.collect_nonfunctional_report import Dimension, deepagents_metadata


def test_subagents_registered_without_human_or_deep_subagents() -> None:
    standard = get_subagent_specs("standard")
    deep = get_subagent_specs("deep")

    standard_names = {item.name for item in standard}
    deep_names = {item.name for item in deep}

    assert {"product_analysis", "inventory", "campaign", "report", "data_quality", "knowledge_base", "database_query"}.issubset(standard_names)
    assert "network_search" not in standard_names
    assert {"product_analysis", "inventory", "campaign", "report", "data_quality", "knowledge_base", "network_search", "database_query"}.issubset(deep_names)
    assert not ({"human_approval", "deep_agent", "deep_expert"} & deep_names)


def test_realtime_profile_has_no_business_subagents_or_tools() -> None:
    profile = get_deepagents_profile("realtime")

    assert profile.subagents == ()
    assert profile.max_tool_calls == 0
    assert profile.max_subagent_calls == 0
    assert not profile.allow_business_tools
    assert not profile.allow_mcp
    assert not profile.allow_filesystem


def test_each_subagent_only_receives_own_tools() -> None:
    subagents = build_deepagents_subagents("standard")
    by_name = {item["name"]: item for item in subagents}

    assert {tool.name for tool in by_name["product_analysis"]["tools"]} == {"query_hot_products", "query_low_conversion_products", "query_inventory_velocity", "query_campaign_roi", "query_shop_profile"}
    assert {tool.name for tool in by_name["inventory"]["tools"]} == {"query_inventory_risks", "query_inventory_velocity", "query_sales_trend", "query_hot_products"}
    assert {tool.name for tool in by_name["database_query"]["tools"]} == {"schema_lookup", "safe_read_sql"}


def test_hitl_uses_interrupt_on_not_human_approval_subagent() -> None:
    assert "human_approval" not in {item.name for item in get_subagent_specs("deep")}
    assert hitl_required_for_tool("update_price", "deep")
    assert hitl_required_for_tool("write_report_file", "standard")
    assert not interrupt_on_for_profile("realtime")


def test_memory_factory_rejects_inmemory_in_production() -> None:
    factory = MemoryBackendFactory({"DEEPAGENTS_STORE_BACKEND": "memory"})

    with pytest.raises(MemoryBackendConfigurationError, match="InMemoryStore"):
        factory.build(production=True)


def test_release_metadata_requires_production_memory_store(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DEEPAGENTS_STORE_BACKEND", raising=False)
    monkeypatch.delenv("DEEPAGENTS_POSTGRES_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)

    metadata = deepagents_metadata({"loop_guard": Dimension(), "hitl_safety": Dimension(), "mcp_whitelist": Dimension()}, mode="release")

    assert metadata["store_backend"].startswith("misconfigured:")
    assert metadata["memory_persistence_ready"] is False


def test_memory_factory_requires_postgres_url() -> None:
    factory = MemoryBackendFactory({"DEEPAGENTS_STORE_BACKEND": "postgres"})

    with pytest.raises(MemoryBackendConfigurationError, match="DEEPAGENTS_POSTGRES_URL"):
        factory.build(production=True)


def test_memory_factory_constructs_postgres_adapter(monkeypatch: pytest.MonkeyPatch) -> None:
    module = types.ModuleType("langgraph.store.postgres")

    class FakePostgresStore:
        @classmethod
        def from_conn_string(cls, url: str):
            return {"adapter": "postgres", "url": url}

    module.PostgresStore = FakePostgresStore
    monkeypatch.setitem(sys.modules, "langgraph.store.postgres", module)

    backend = MemoryBackendFactory({"DEEPAGENTS_STORE_BACKEND": "postgres", "DEEPAGENTS_POSTGRES_URL": "postgresql://example/db"}).build(production=True)

    assert backend.backend == "postgres"
    assert backend.persistence_ready
    assert backend.store == {"adapter": "postgres", "url": "postgresql://example/db"}


def test_memory_factory_constructs_redis_adapter(monkeypatch: pytest.MonkeyPatch) -> None:
    module = types.ModuleType("langgraph.store.redis")

    class FakeRedisStore:
        @classmethod
        def from_conn_string(cls, url: str):
            return {"adapter": "redis", "url": url}

    module.RedisStore = FakeRedisStore
    monkeypatch.setitem(sys.modules, "langgraph.store.redis", module)

    backend = MemoryBackendFactory({"DEEPAGENTS_STORE_BACKEND": "redis", "DEEPAGENTS_REDIS_URL": "redis://localhost:6379/1"}).build(production=True)

    assert backend.backend == "redis"
    assert backend.persistence_ready
    assert backend.store == {"adapter": "redis", "url": "redis://localhost:6379/1"}


def test_memory_namespace_is_tenant_shop_user_scoped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEEPAGENTS_MEMORY_NAMESPACE_MODE", "tenant_shop_user")

    namespace = memory_namespace({"configurable": {"tenant_id": "t1", "shop_id": "s1", "user_id": "u1"}})

    assert namespace == ("tenant", "t1", "shop", "s1", "user", "u1")


def test_loop_guard_blocks_repeated_tool_args() -> None:
    guard = RuntimeGuard.for_profile("standard")
    guard.record_tool_call("query_hot_products", {"time_range": "last_30d"})
    guard.record_tool_call("query_hot_products", {"time_range": "last_30d"})

    with pytest.raises(GuardViolation) as error:
        guard.record_tool_call("query_hot_products", {"time_range": "last_30d"})

    assert error.value.reason == "tool_loop"


def test_loop_guard_blocks_repeated_subagent() -> None:
    guard = RuntimeGuard.for_profile("standard")
    guard.record_subagent_call("inventory")
    guard.record_subagent_call("inventory")
    guard.record_subagent_call("inventory")

    with pytest.raises(GuardViolation) as error:
        guard.record_subagent_call("inventory")

    assert error.value.reason == "subagent_loop"


def test_loop_guard_blocks_model_budget() -> None:
    guard = RuntimeGuard.for_profile("realtime")
    guard.record_model_call()
    guard.record_model_call()

    with pytest.raises(GuardViolation) as error:
        guard.record_model_call()

    assert error.value.reason == "budget_exceeded"


def test_guard_middleware_records_model_calls() -> None:
    guard = RuntimeGuard.for_profile("standard")
    middleware = guard_middleware()[0]
    token = set_active_runtime_guard(guard)
    try:
        middleware.wrap_model_call(object(), lambda request: "ok")
    finally:
        reset_active_runtime_guard(token)

    assert guard.model_calls == 1


def test_native_tool_wrapper_records_guard_calls() -> None:
    guard = RuntimeGuard.for_profile("standard")
    token = set_tool_context({"guard": guard, "tenant_id": "t", "shop_id": "s", "user_id": "u"})
    try:
        product_agent = next(item for item in build_deepagents_subagents("standard") if item["name"] == "product_analysis")
        tool = next(tool for tool in product_agent["tools"] if tool.name == "query_hot_products")
        tool.invoke({"time_range": "last_30d", "limit": 1})
    finally:
        reset_tool_context(token)

    assert guard.tool_calls == 1
    assert guard.tool_counts["query_hot_products"] == 1


def test_filesystem_profile_permissions_and_path_traversal() -> None:
    assert filesystem_permissions("realtime") == []
    standard_permissions = filesystem_permissions("standard", tenant_id="t1", shop_id="s1", task_id="task1")
    assert any(permission.mode == "allow" and "/workspace/task1/**" in permission.paths for permission in standard_permissions)

    with pytest.raises(ValueError):
        reject_path_traversal("../secrets.env")


def test_mcp_policy_profile_gating(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENABLE_DEEPAGENTS_MCP", "true")
    monkeypatch.setenv("DEEPAGENTS_STANDARD_ENABLE_MCP", "true")
    monkeypatch.setenv("DEEPAGENTS_MCP_TOOL_WHITELIST", "read_catalog,write_campaign")

    realtime = mcp_policy_for_profile("realtime")
    standard = mcp_policy_for_profile("standard")

    assert not realtime.enabled
    assert standard.allowed_tools == ("read_catalog",)
    with pytest.raises(PermissionError):
        assert_mcp_tool_allowed("write_campaign", "standard")
