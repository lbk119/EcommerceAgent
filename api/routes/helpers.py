"""FastAPI 路由共享辅助函数。"""

from __future__ import annotations

from fastapi import HTTPException, Request


def gateway_identity(request: Request) -> dict[str, str]:
    """读取 Gateway 注入的可信身份。

    生产链路必须经过 Go Gateway；本地直连 Brain 调试时允许 query/header 缺省值兜底，但业务
    服务仍统一使用这个函数返回的 tenant/user/shop，避免每个路由各自解析造成隔离口径不一致。
    """
    tenant_id = request.headers.get("X-Tenant-ID") or request.query_params.get("tenant_id") or "tenant_demo"
    user_id = request.headers.get("X-User-ID") or request.query_params.get("user_id") or "local_user"
    shop_id = request.headers.get("X-Shop-ID") or request.query_params.get("shop_id") or "default_shop"
    if not tenant_id or not user_id:
        raise HTTPException(status_code=401, detail="缺少可信身份上下文")
    return {"tenant_id": tenant_id, "user_id": user_id, "shop_id": shop_id}


def requested_shop(request: Request, identity: dict[str, str]) -> str:
    """解析路由指定 shop_id；没有时使用 Gateway 上下文里的当前店铺。"""
    return request.query_params.get("shop_id") or identity.get("shop_id") or "default_shop"
