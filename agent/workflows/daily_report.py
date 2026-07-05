"""
经营日报 deterministic DAG 模板。

当前文件只定义稳定步骤和输入输出契约，不直接替换 DeepAgent 默认执行流。这样前端、trace 和后续
调度器可以先看到“日报应当怎么跑”，等数据库 workflow 和文档工具稳定后再切真实执行。
"""

from dataclasses import dataclass
from typing import Dict, List


@dataclass(frozen=True)
class WorkflowStep:
    """业务 DAG 的单个固定步骤。"""

    name: str
    description: str
    required_outputs: List[str]

    def to_dict(self) -> Dict[str, object]:
        return {"name": self.name, "description": self.description, "required_outputs": self.required_outputs}


DAILY_REPORT_DAG = (
    WorkflowStep("metric_query", "查询 GMV、订单数、客单价、评分、退款率等核心指标。", ["gmv", "orders", "aov", "rating", "refund_rate"]),
    WorkflowStep("risk_query", "查询退款、差评、客服、库存等经营风险信号。", ["refund_risks", "review_risks", "inventory_risks"]),
    WorkflowStep("insight_generation", "基于指标和风险生成面向经营者的结论。", ["summary", "key_findings", "recommended_actions"]),
    WorkflowStep("critic_check", "对日报完整性和数据依据做 Critic 校验。", ["critic_status", "fix_instruction"]),
    WorkflowStep("document_generation", "生成 Markdown/PDF 等可下载经营日报。", ["markdown_path"]),
)


def describe_daily_report_workflow() -> Dict[str, object]:
    """返回可序列化 DAG 描述，供未来 Planner、API 或前端工作流视图使用。"""
    return {"workflow": "daily_report", "steps": [step.to_dict() for step in DAILY_REPORT_DAG]}