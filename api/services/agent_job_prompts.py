"""数字员工业务动作到内部 Agent query 的集中映射。

前端只允许传 agent_id + jobType + params，不能传自由 prompt。这样产品按钮是稳定业务动作，
而 prompt 文案可以在后端版本化、审计和灰度。
"""

AGENT_JOB_TEMPLATES = {
    "store-analyst": {
        "daily_report": "请基于当前店铺昨日经营数据生成经营日报，包含 GMV、订单、转化率、客单价、退款率、异常波动和可执行建议。",
        "anomaly_scan": "请分析当前店铺近期经营异常，识别 GMV、订单、转化和退款波动，并输出可执行动作。",
    },
    "product-assistant": {
        "product_optimization": "请分析当前店铺商品表现，识别爆品、潜力品和滞销品，并输出标题、价格、主图和库存优化方案。",
        "hot_product_analysis": "请基于当前店铺商品成交、流量、转化、库存和活动数据，识别最值得放量的爆品候选，并输出放量策略。",
    },
    "inventory-inspector": {
        "inventory_risk_scan": "请巡检当前店铺库存风险，识别库存不足、滞销、周转过慢和活动备货不足 SKU，并输出补货建议。",
        "replenishment_plan": "请基于当前店铺库存、安全库存、近 7 天销量和活动承接需求，生成补货计划和优先级。",
    },
    "campaign-reviewer": {
        "campaign_review": "请复盘当前店铺最近活动，分析 ROI、GMV、转化变化、投放效果和下次活动建议。",
    },
    "report-specialist": {
        "weekly_report": "请汇总当前店铺本周经营、商品、库存、活动和策略审核信息，生成管理层周报。",
        "monthly_report": "请汇总当前店铺本月经营、商品、库存、活动和策略审核信息，生成管理层月报。",
    },
}


def build_agent_query(agent_id: str, job_type: str, params: dict | None = None) -> str:
    """根据业务动作生成内部 Agent query；未知动作直接抛错。"""
    params = params or {}
    template = AGENT_JOB_TEMPLATES.get(agent_id, {}).get(job_type)
    if not template:
        raise ValueError("不支持的数字员工任务类型")
    if params:
        return f"{template}\n任务参数：{params}"
    return template
