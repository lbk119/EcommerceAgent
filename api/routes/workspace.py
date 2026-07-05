"""工作区聚合 API。"""

from fastapi import APIRouter, Request

from api.routes.helpers import gateway_identity, requested_shop
from api.services.ecommerce_queries import workspace_bundle


router = APIRouter(prefix="/api/workspace", tags=["workspace"])


@router.get("")
async def get_workspace(request: Request):
    """返回前端首屏需要的完整工作区数据。"""
    identity = gateway_identity(request)
    return {"workspace": workspace_bundle(identity["tenant_id"], requested_shop(request, identity))}
