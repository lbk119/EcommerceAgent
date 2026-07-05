"""活动复盘 API。"""

from fastapi import APIRouter, HTTPException, Request

from api.routes.helpers import gateway_identity, requested_shop
from api.services.agent_job_service import create_agent_job
from api.services.ecommerce_queries import list_campaigns


router = APIRouter(prefix="/api/campaigns", tags=["campaigns"])


@router.get("")
async def campaigns(request: Request):
    """返回活动 ROI、GMV 和复盘结论。"""
    identity = gateway_identity(request)
    return {"campaigns": list_campaigns(identity["tenant_id"], requested_shop(request, identity))}


@router.post("/{campaign_id}/review")
async def review_campaign(campaign_id: str, payload: dict, request: Request):
    """创建活动复盘专员任务，并把 campaignId 放入受控 params。"""
    identity = gateway_identity(request)
    params = {**(payload.get("params") or {}), "campaignId": campaign_id}
    try:
        job = await create_agent_job(
            identity["tenant_id"],
            requested_shop(request, identity),
            identity["user_id"],
            "campaign-reviewer",
            {"jobType": "campaign_review", "title": payload.get("title") or "活动复盘", "params": params},
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return {"job": job}
