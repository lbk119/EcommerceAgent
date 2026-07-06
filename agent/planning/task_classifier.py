"""
电商领域任务分类器。

这个模块不是通用 Planner，也不尝试替 LLM 拆步骤。它只做轻量、可解释的电商任务识别，给
AgentRuntime、Critic policy 和未来 deterministic DAG 一个稳定的输入信号。

设计原则：
- 规则先行：当前阶段用关键词和风险表，便于审计和本地调试；
- 输出稳定：下游只依赖 task_type/risk/requires_critic/preferred_workflow 四个字段；
- 可替换：未来如果接分类模型，只需要保持 TaskClassification.to_dict() 的结构不变。
"""

from dataclasses import dataclass
from typing import Dict, Iterable, Tuple


TASK_TYPES = {"seasonal_selection", "product_optimization", "inventory_analysis", "campaign_review", "daily_report", "hot_product_analysis", "refund_analysis", "general_business_chat"}
RISK_LEVELS = {"low", "medium", "high"}
WORKFLOWS = {"deepagent", "deterministic_dag"}


@dataclass(frozen=True)
class TaskClassification:
    """一次用户请求的电商任务分类结果。"""

    task_type: str
    risk: str
    requires_critic: bool
    preferred_workflow: str

    def to_dict(self) -> Dict[str, object]:
        """返回 API/trace 友好的稳定结构，避免调用方直接序列化 dataclass。"""
        return {
            "task_type": self.task_type,
            "risk": self.risk,
            "requires_critic": self.requires_critic,
            "preferred_workflow": self.preferred_workflow,
        }


def classify_task(query: str) -> TaskClassification:
    """
    识别电商任务类型、风险等级和推荐执行形态。

    deterministic_dag 表示“优先走固定业务 DAG”。AgentRuntime 会先交给 WorkflowRouter，未覆盖或
    执行失败时再回落 DeepAgent。
    """
    compact_query = _compact(query)
    if _contains_any(compact_query, _SEASONAL_SELECTION_KEYWORDS):
        return TaskClassification("seasonal_selection", "medium", True, "deterministic_dag")
    if _contains_any(compact_query, _PRODUCT_OPTIMIZATION_KEYWORDS):
        return TaskClassification("product_optimization", "medium", True, "deterministic_dag")
    if _contains_any(compact_query, _HOT_PRODUCT_KEYWORDS):
        return TaskClassification("hot_product_analysis", "medium", True, "deterministic_dag")

    matched_type = _first_matching_type(compact_query)

    if matched_type == "daily_report":
        return TaskClassification("daily_report", "medium", True, "deterministic_dag")
    if matched_type == "inventory_analysis":
        return TaskClassification("inventory_analysis", "medium", True, "deterministic_dag")
    if matched_type == "campaign_review":
        return TaskClassification("campaign_review", "medium", True, "deterministic_dag")
    if matched_type == "refund_analysis":
        return TaskClassification("refund_analysis", "high", True, "deepagent")

    # SQL、写库、更新等词即使没有匹配到具体业务模板，也应提升风险并要求 Critic。
    if _contains_any(compact_query, ("sql", "写入", "更新", "插入", "删除", "改库", "入库")):
        return TaskClassification("general_business_chat", "high", True, "deepagent")

    return TaskClassification("general_business_chat", "low", False, "deepagent")


def _first_matching_type(compact_query: str) -> str:
    for task_type, keywords in _TASK_KEYWORDS:
        if _contains_any(compact_query, keywords):
            return task_type
    return "general_chat"


def _contains_any(compact_query: str, keywords: Iterable[str]) -> bool:
    return any(_compact(keyword) in compact_query for keyword in keywords)


def _compact(text: str) -> str:
    return text.lower().replace(" ", "").replace("_", "")


_TASK_KEYWORDS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("daily_report", ("经营日报", "日报", "周报", "月报", "经营分析", "dailyreport")),
    ("inventory_analysis", ("库存", "补货", "安全库存", "缺货", "滞销", "inventory")),
    ("campaign_review", ("活动复盘", "投放复盘", "roi", "转化率", "campaign")),
    ("refund_analysis", ("退款", "退款率", "客诉", "异常分析", "refund")),
)


_SEASONAL_SELECTION_KEYWORDS: Tuple[str, ...] = (
    "这个季节适合卖什么",
    "现在适合卖什么",
    "夏天卖什么",
    "冬天卖什么",
    "春天卖什么",
    "秋天卖什么",
    "应季商品",
    "季节性",
    "选品",
    "上新",
    "趋势",
    "市场机会",
    "适合卖什么东西",
)


_PRODUCT_OPTIMIZATION_KEYWORDS: Tuple[str, ...] = (
    "哪个商品值得优化",
    "哪个商品最值得优化",
    "商品怎么优化",
    "商品优化",
    "标题",
    "主图",
    "价格",
    "转化",
    "转化率",
)


_HOT_PRODUCT_KEYWORDS: Tuple[str, ...] = (
    "爆品",
    "热销",
    "畅销",
    "表现最好",
    "销售增长",
    "放量",
    "topproduct",
    "bestseller",
)
