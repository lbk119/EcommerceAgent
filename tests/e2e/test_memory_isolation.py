from __future__ import annotations

from agent.memory import memory_namespace


def test_cross_tenant_memory_namespaces_do_not_collide() -> None:
    first = memory_namespace({"configurable": {"tenant_id": "a", "shop_id": "s", "user_id": "u"}})
    second = memory_namespace({"configurable": {"tenant_id": "b", "shop_id": "s", "user_id": "u"}})

    assert first != second
