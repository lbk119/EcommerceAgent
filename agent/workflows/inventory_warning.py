"""
库存预警 deterministic DAG 模板。

库存类任务更适合固定流程：先找风险 SKU，再看销量/库存，再生成动作建议。这里先沉淀模板，后续
可以逐步把每一步绑定到数据库 workflow 的确定性查询。
"""

from dataclasses import dataclass
from typing import Dict, List


@dataclass(frozen=True)
class WorkflowStep:
    """库存预警 DAG 的单个固定步骤。"""

    name: str
    description: str
    required_outputs: List[str]

    def to_dict(self) -> Dict[str, object]:
        return {"name": self.name, "description": self.description, "required_outputs": self.required_outputs}


INVENTORY_WARNING_DAG = (
    WorkflowStep("risk_sku_query", "查询缺货、低库存、滞销和高退款风险商品。", ["risk_skus"]),
    WorkflowStep("sales_velocity_query", "查询近 7/30 天销量、库存周转和安全库存。", ["sales_velocity", "safety_stock"]),
    WorkflowStep("replenishment_plan", "生成补货、清仓或暂停投放建议。", ["actions", "priority"]),
    WorkflowStep("critic_check", "校验建议是否包含数据依据、风险商品和明确动作。", ["critic_status", "fix_instruction"]),
)


def describe_inventory_warning_workflow() -> Dict[str, object]:
    """返回库存预警 DAG 描述，当前用于规划和前端展示，不直接执行。"""
    return {"workflow": "inventory_warning", "steps": [step.to_dict() for step in INVENTORY_WARNING_DAG]}