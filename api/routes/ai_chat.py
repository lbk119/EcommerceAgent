"""辅助 AI 对话 API。"""

from fastapi import APIRouter, Request

from api.routes.helpers import gateway_identity, requested_shop
from api.services.ecommerce_queries import get_metrics, list_inventory_risks, list_campaigns, list_products


router = APIRouter(prefix="/api/ai-chat", tags=["ai-chat"])


@router.post("/messages")
async def chat(payload: dict, request: Request):
    """轻量经营问答。

    这里先用确定性经营数据回答，避免每次普通问答都触发完整 DeepAgent；需要复杂分析时前端可以
    再调用数字员工 job 接口。
    """
    identity = gateway_identity(request)
    tenant_id = identity["tenant_id"]
    shop_id = requested_shop(request, identity)
    content = str(payload.get("content") or "")
    metrics = get_metrics(tenant_id, shop_id)
    if any(word in content for word in ["库存", "补货", "缺货", "滞销"]):
        risks = list_inventory_risks(tenant_id, shop_id)[:3]
        names = "、".join(item["name"] for item in risks) or "暂无高风险商品"
        reply = f"当前有 {metrics['inventoryRiskSkuCount']} 个库存风险 SKU，重点关注 {names}。建议先处理低于安全库存的 SKU，再评估滞销清仓。"
    elif any(word in content for word in ["活动", "投放", "ROI", "复盘"]):
        campaign = (list_campaigns(tenant_id, shop_id) or [{"name": "暂无活动", "roi": 0, "conclusion": "暂无活动数据"}])[0]
        reply = f"最近活动 {campaign['name']} 的 ROI 为 {campaign['roi']}，结论是：{campaign['conclusion']}"
    elif any(word in content for word in ["商品", "爆品", "优化"]):
        product = (list_products(tenant_id, shop_id) or [{"name": "暂无商品", "layer": "稳态品", "aiSuggestion": "请先导入商品数据。"}])[0]
        reply = f"优先查看 {product['name']}，当前分层为 {product['layer']}。{product['aiSuggestion']}"
    else:
        reply = f"当前店铺 GMV 为 {metrics['gmv']:.0f} 元，订单 {metrics['orders']} 单，转化率 {metrics['conversionRate']}%。可以继续问我库存、商品、活动或报告。"
    return {"message": {"role": "assistant", "content": reply}}
