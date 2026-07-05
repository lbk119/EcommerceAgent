"""店铺元数据 API。"""

from fastapi import APIRouter, HTTPException, Request

from api.routes.helpers import gateway_identity
from api.services.ecommerce_queries import create_shop, list_shops, soft_delete_shop, update_shop


router = APIRouter(prefix="/api/shops", tags=["shops"])


@router.get("")
async def shops(request: Request):
    identity = gateway_identity(request)
    return {"shops": list_shops(identity["tenant_id"])}


@router.post("")
async def add_shop(payload: dict, request: Request):
    identity = gateway_identity(request)
    return {"shop": create_shop(identity["tenant_id"], payload)}


@router.put("/{shop_id}")
async def edit_shop(shop_id: str, payload: dict, request: Request):
    identity = gateway_identity(request)
    shop = update_shop(identity["tenant_id"], shop_id, payload)
    if not shop:
        raise HTTPException(status_code=404, detail="店铺不存在")
    return {"shop": shop}


@router.delete("/{shop_id}")
async def remove_shop(shop_id: str, request: Request):
    identity = gateway_identity(request)
    return soft_delete_shop(identity["tenant_id"], shop_id)
