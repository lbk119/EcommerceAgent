"""
活动复盘 deterministic DAG 模板。

活动复盘需要稳定覆盖曝光、点击、转化、GMV、退款和 ROI。固定 DAG 可以减少 LLM 自由规划遗漏
关键指标的问题，当前先作为架构模板沉淀。
"""

from dataclasses import dataclass
from typing import Dict, List


@dataclass(frozen=True)
class WorkflowStep:
    """活动复盘 DAG 的单个固定步骤。"""

    name: str
    description: str
    required_outputs: List[str]

    def to_dict(self) -> Dict[str, object]:
        return {"name": self.name, "description": self.description, "required_outputs": self.required_outputs}


CAMPAIGN_REVIEW_DAG = (
    WorkflowStep("traffic_query", "查询曝光、点击、CTR 和流量来源。", ["impressions", "clicks", "ctr"]),
    WorkflowStep("conversion_query", "查询转化、订单、GMV、客单价和 ROI。", ["conversion_rate", "orders", "gmv", "roi"]),
    WorkflowStep("risk_query", "查询退款、差评和活动后库存风险。", ["refund_rate", "review_risks", "inventory_risks"]),
    WorkflowStep("insight_generation", "输出活动成败原因和下一轮优化动作。", ["summary", "actions"]),
    WorkflowStep("critic_check", "校验复盘是否覆盖投放、转化、GMV、退款或 ROI。", ["critic_status", "fix_instruction"]),
)


def describe_campaign_review_workflow() -> Dict[str, object]:
    """返回活动复盘 DAG 描述，当前用于规划和前端展示，不直接执行。"""
    return {"workflow": "campaign_review", "steps": [step.to_dict() for step in CAMPAIGN_REVIEW_DAG]}