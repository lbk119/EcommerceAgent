"""数据导入 API。"""

from fastapi import APIRouter, File, Request, UploadFile

from api.routes.helpers import gateway_identity, requested_shop
from api.services.ecommerce_queries import list_import_jobs
from api.services.import_service import confirm_job, create_sample_job, create_upload_job, preview_job, save_mapping


router = APIRouter(prefix="/api/data-import", tags=["data-import"])


@router.get("/jobs")
async def jobs(request: Request):
    identity = gateway_identity(request)
    return {"jobs": list_import_jobs(identity["tenant_id"], requested_shop(request, identity))}


@router.post("/sample")
async def sample(request: Request):
    identity = gateway_identity(request)
    return {"job": create_sample_job(identity["tenant_id"], requested_shop(request, identity), identity["user_id"])}


@router.post("/upload")
async def upload(request: Request, file: UploadFile = File(...)):
    identity = gateway_identity(request)
    return {"job": await create_upload_job(identity["tenant_id"], requested_shop(request, identity), identity["user_id"], file)}


@router.get("/{job_id}/preview")
async def preview(job_id: str, request: Request):
    identity = gateway_identity(request)
    return preview_job(identity["tenant_id"], requested_shop(request, identity), job_id)


@router.post("/{job_id}/mapping")
async def mapping(job_id: str, payload: dict, request: Request):
    identity = gateway_identity(request)
    return save_mapping(identity["tenant_id"], requested_shop(request, identity), job_id, payload)


@router.post("/{job_id}/confirm")
async def confirm(job_id: str, request: Request):
    identity = gateway_identity(request)
    return confirm_job(identity["tenant_id"], requested_shop(request, identity), job_id, identity["user_id"])
