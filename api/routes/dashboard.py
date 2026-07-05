"""经营工作台 API。"""

from fastapi import APIRouter, Request

from api.routes.helpers import gateway_identity, requested_shop
from api.services.ecommerce_queries import get_metrics, list_strategies


router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("")
async def dashboard(request: Request):
    """返回工作台核心指标和待审核策略。"""
    identity = gateway_identity(request)
    shop_id = requested_shop(request, identity)
    return {"metrics": get_metrics(identity["tenant_id"], shop_id), "strategies": list_strategies(identity["tenant_id"], shop_id)}
