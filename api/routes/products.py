"""商品分析 API。"""

from fastapi import APIRouter, HTTPException, Request

from api.routes.helpers import gateway_identity, requested_shop
from api.services.agent_job_service import create_agent_job
from api.services.ecommerce_queries import list_products


router = APIRouter(prefix="/api/products", tags=["products"])


@router.get("")
async def products(request: Request, keyword: str = "", riskLevel: str = ""):
    """返回商品分层、风险和 AI 建议。"""
    identity = gateway_identity(request)
    return {"products": list_products(identity["tenant_id"], requested_shop(request, identity), {"keyword": keyword, "riskLevel": riskLevel})}


@router.post("/analyze")
async def analyze_products(payload: dict, request: Request):
    """创建商品运营助理分析任务；前端只能传 params，不允许传自由 prompt。"""
    identity = gateway_identity(request)
    try:
        job = await create_agent_job(
            identity["tenant_id"],
            requested_shop(request, identity),
            identity["user_id"],
            "product-assistant",
            {"jobType": "product_optimization", "title": payload.get("title") or "商品优化分析", "params": payload.get("params") or {}},
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return {"job": job}
