from __future__ import annotations

import pytest

from agent.memory import memory_namespace


@pytest.mark.security
def test_memory_namespaces_are_tenant_scoped() -> None:
    first = memory_namespace({"configurable": {"tenant_id": "tenant-a", "shop_id": "shop", "user_id": "user"}})
    second = memory_namespace({"configurable": {"tenant_id": "tenant-b", "shop_id": "shop", "user_id": "user"}})

    assert first != second
