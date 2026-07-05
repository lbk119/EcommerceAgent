"""平台授权 API。"""

from fastapi import APIRouter, Request

from api.routes.helpers import gateway_identity, requested_shop
from api.services.ecommerce_queries import list_integrations, set_integration_status


router = APIRouter(prefix="/api/integrations", tags=["integrations"])


@router.get("")
async def integrations(request: Request):
    identity = gateway_identity(request)
    return {"integrations": list_integrations(identity["tenant_id"], requested_shop(request, identity))}


@router.post("/status")
async def update_status(payload: dict, request: Request):
    """更新平台授权状态。

    平台名称可能包含斜杠或空格（例如“淘宝 / 天猫”），放在 URL path 中容易被代理层解码成
    多级路径，所以产品化接口统一用 JSON body 传 platform。
    """
    identity = gateway_identity(request)
    return {"integration": set_integration_status(identity["tenant_id"], requested_shop(request, identity), payload.get("platform") or "", payload.get("status") or "unauthorized")}


@router.post("/{platform}/authorize")
async def authorize(platform: str, request: Request):
    """本地开发模拟授权成功；真实 OAuth 回调后续替换这里的状态写入。"""
    identity = gateway_identity(request)
    return {"integration": set_integration_status(identity["tenant_id"], requested_shop(request, identity), platform, "authorized")}


@router.post("/{platform}/sync")
async def sync(platform: str, request: Request):
    identity = gateway_identity(request)
    return {"integration": set_integration_status(identity["tenant_id"], requested_shop(request, identity), platform, "syncing")}


@router.post("/{platform}/disconnect")
async def disconnect(platform: str, request: Request):
    identity = gateway_identity(request)
    return {"integration": set_integration_status(identity["tenant_id"], requested_shop(request, identity), platform, "unauthorized")}
