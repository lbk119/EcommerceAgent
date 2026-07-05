"""数字员工 API。"""

from fastapi import APIRouter, HTTPException, Request

from api.routes.helpers import gateway_identity, requested_shop
from api.services.agent_job_service import create_agent_job, get_agent_job, list_agent_jobs, sync_agent_job_from_runtime
from api.services.ecommerce_queries import list_agents, update_strategy_status


router = APIRouter(prefix="/api/agents", tags=["agents"])


@router.get("")
async def agents(request: Request):
    identity = gateway_identity(request)
    return {"agents": list_agents(identity["tenant_id"], requested_shop(request, identity))}


@router.post("/{agent_id}/jobs")
async def create_job(agent_id: str, payload: dict, request: Request):
    identity = gateway_identity(request)
    try:
        return {"job": await create_agent_job(identity["tenant_id"], requested_shop(request, identity), identity["user_id"], agent_id, payload)}
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.get("/{agent_id}/jobs")
async def jobs(agent_id: str, request: Request):
    identity = gateway_identity(request)
    return {"jobs": list_agent_jobs(identity["tenant_id"], requested_shop(request, identity), agent_id)}


@router.get("/jobs/{job_id}")
async def job_detail(job_id: str, request: Request):
    identity = gateway_identity(request)
    shop_id = requested_shop(request, identity)
    await sync_agent_job_from_runtime(identity["tenant_id"], shop_id, identity["user_id"], job_id)
    job = get_agent_job(identity["tenant_id"], shop_id, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    return {"job": job}


@router.post("/strategies/{strategy_id}/approve")
async def approve_strategy(strategy_id: str, request: Request):
    identity = gateway_identity(request)
    try:
        return {"strategy": update_strategy_status(identity["tenant_id"], requested_shop(request, identity), strategy_id, "accepted", identity["user_id"])}
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.post("/strategies/{strategy_id}/reject")
async def reject_strategy(strategy_id: str, request: Request):
    identity = gateway_identity(request)
    try:
        return {"strategy": update_strategy_status(identity["tenant_id"], requested_shop(request, identity), strategy_id, "rejected", identity["user_id"])}
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.post("/strategies/{strategy_id}/defer")
async def defer_strategy(strategy_id: str, request: Request):
    """把策略标记为暂缓，保持策略进入后续人工复盘队列。"""
    identity = gateway_identity(request)
    try:
        return {"strategy": update_strategy_status(identity["tenant_id"], requested_shop(request, identity), strategy_id, "deferred", identity["user_id"])}
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
