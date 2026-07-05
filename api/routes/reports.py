"""经营报告 API。"""

from fastapi import APIRouter, HTTPException, Request

from api.routes.helpers import gateway_identity, requested_shop
from api.services.ecommerce_queries import list_reports
from api.services.report_service import generate_report, get_report


router = APIRouter(prefix="/api/reports", tags=["reports"])


@router.get("")
async def reports(request: Request):
    identity = gateway_identity(request)
    return {"reports": list_reports(identity["tenant_id"], requested_shop(request, identity))}


@router.post("/generate")
async def generate(payload: dict, request: Request):
    identity = gateway_identity(request)
    try:
        return await generate_report(identity["tenant_id"], requested_shop(request, identity), identity["user_id"], payload)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.get("/{report_id}")
async def report_detail(report_id: str, request: Request):
    identity = gateway_identity(request)
    report = get_report(identity["tenant_id"], requested_shop(request, identity), report_id)
    if not report:
        raise HTTPException(status_code=404, detail="报告不存在")
    return {"report": report}
