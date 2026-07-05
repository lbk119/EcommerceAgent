"""爆品分析固定 workflow 描述。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List


@dataclass(frozen=True)
class HotProductStep:
    """爆品分析 workflow 的可观测步骤定义。"""

    name: str
    required: bool
    description: str

    def to_dict(self) -> Dict[str, object]:
        return {
            "name": self.name,
            "required": self.required,
            "description": self.description,
        }


HOT_PRODUCT_DAG: List[HotProductStep] = [
    HotProductStep(
        name="爆品综合指标查询",
        required=True,
        description="一次性查询候选商品的销售额、销量、流量、转化、价格、库存、活动 ROI 和退款指标。",
    ),
]


def describe_hot_product_workflow() -> Dict[str, object]:
    """返回给 trace/Critic/综合提示使用的 workflow 定义。"""
    return {"workflow": "hot_product_analysis", "steps": [step.to_dict() for step in HOT_PRODUCT_DAG]}