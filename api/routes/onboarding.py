"""新用户引导 API。"""

from fastapi import APIRouter, Request

from api.routes.helpers import gateway_identity
from api.services.ecommerce_queries import AGENT_DEFINITIONS, create_shop, ensure_integrations, list_integrations, list_shops


router = APIRouter(prefix="/api/onboarding", tags=["onboarding"])


@router.post("/complete")
async def complete_onboarding(payload: dict, request: Request):
    """完成店铺和平台初始化，重型数据导入由前端进入工作台后后台触发。"""
    identity = gateway_identity(request)
    shop = create_shop(identity["tenant_id"], {
        "name": payload.get("shopName"),
        "category": payload.get("category"),
        "platform": ",".join(payload.get("selectedPlatforms") or ["taobao_tmall"]),
        "type": payload.get("shopType"),
        "businessStage": payload.get("businessStage"),
        "reuseByName": True,
    })
    ensure_integrations(identity["tenant_id"], shop["id"], payload.get("selectedPlatforms") or [])
    # 完成引导是强交互动作，不能同步等待完整经营聚合；前端进入 dashboard 后会后台刷新 workspace。
    return {
        "onboardingCompleted": True,
        "shop": shop,
        "workspace": {
            "currentShopId": shop["id"],
            "shops": list_shops(identity["tenant_id"]),
            "integrations": list_integrations(identity["tenant_id"], shop["id"]),
            "metrics": {"date": "", "gmv": 0, "orders": 0, "conversionRate": 0, "averageOrderValue": 0, "refundRate": 0, "visitors": 0, "inventoryRiskSkuCount": 0, "activeCampaignProducts": 0, "aiCompletedTasks": 0},
            "products": [],
            "agents": [{**agent, "status": "idle", "tasks": [], "outputs": []} for agent in AGENT_DEFINITIONS],
            "reports": [],
            "strategies": [],
            "campaigns": [],
            "imports": [],
        },
    }
