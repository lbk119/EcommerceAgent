"""库存风险 API。"""

from fastapi import APIRouter, HTTPException, Request

from api.routes.helpers import gateway_identity, requested_shop
from api.services.agent_job_service import create_agent_job
from api.services.ecommerce_queries import list_inventory_risks


router = APIRouter(prefix="/api/inventory", tags=["inventory"])


@router.get("/risks")
async def inventory_risks(request: Request):
    """返回库存不足、滞销和周转风险 SKU。"""
    identity = gateway_identity(request)
    return {"items": list_inventory_risks(identity["tenant_id"], requested_shop(request, identity))}


@router.post("/replenishment-plan")
async def replenishment_plan(payload: dict, request: Request):
    """创建库存巡检员补货计划任务。"""
    identity = gateway_identity(request)
    try:
        job = await create_agent_job(
            identity["tenant_id"],
            requested_shop(request, identity),
            identity["user_id"],
            "inventory-inspector",
            {"jobType": "replenishment_plan", "title": payload.get("title") or "库存补货计划", "params": payload.get("params") or {}},
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return {"job": job}
