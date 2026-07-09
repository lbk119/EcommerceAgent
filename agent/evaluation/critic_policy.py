"""Critic 触发策略。

Critic 是否运行不应该散落在关键词判断里。这里把平台治理字段、任务类型、工具风险和用户请求
统一收敛成一个可观测的决策对象，方便前端展示、审计和策略调优。

注意：本文件只决定“是否需要复核”，不负责业务分类，也不直接调用模型。
"""

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional

from agent.tools.registry import tool_registry
from agent.plan.models import AgentTaskPlan
from agent.plan.planner import planner_agent


CRITIC_KEYWORDS = (
    "经营日报",
    "日报",
    "sql",
    "写入",
    "更新",
    "活动复盘",
    "库存",
    "补货",
    "退款",
    "异常分析",
)

# 关键词到治理任务类型的映射。这里用于解释“为什么需要 Critic”，不是最终业务分类器。
TASK_TYPE_KEYWORDS = {
    "business_report": ("经营日报", "日报", "周报", "月报", "经营分析"),
    "database_change": ("sql", "写入", "更新", "插入", "删除", "改库", "入库"),
    "campaign_review": ("活动复盘", "投放复盘", "roi", "转化"),
    "inventory_replenishment": ("库存", "补货", "安全库存", "缺货"),
    "refund_anomaly": ("退款", "退货", "异常分析", "退款率", "客诉"),
}


@dataclass(frozen=True)
class CriticPolicyDecision:
    """Critic 策略的结构化决策结果。"""

    required: bool
    reasons: List[str] = field(default_factory=list)
    task_types: List[str] = field(default_factory=list)
    matched_keywords: List[str] = field(default_factory=list)
    agent_names: List[str] = field(default_factory=list)
    high_risk_tools: List[str] = field(default_factory=list)

    def to_metadata(self) -> Dict[str, Any]:
        """转换为 trace metadata，避免调用方直接依赖 dataclass 内部结构。"""
        return {
            "required": self.required,
            "reasons": self.reasons,
            "task_types": self.task_types,
            "matched_keywords": self.matched_keywords,
            "agent_names": self.agent_names,
            "high_risk_tools": self.high_risk_tools,
        }


def evaluate_critic_policy(
    query: str,
    *,
    agent_specs: Optional[Iterable[Any]] = None,
    tool_calls: Optional[Iterable[Dict[str, Any]]] = None,
    task_plan: Optional[AgentTaskPlan] = None,
) -> CriticPolicyDecision:
    """判断当前任务是否需要 Critic 复核。"""
    task_plan = task_plan or planner_agent.plan(query)
    compact_query = query.lower().replace(" ", "")
    reasons: List[str] = []
    matched_keywords = [keyword for keyword in CRITIC_KEYWORDS if keyword.lower() in compact_query]
    task_types = _match_task_types(compact_query, task_plan)
    agent_names = _match_critic_required_agents(agent_specs or [], tool_calls or [])
    high_risk_tools = _match_high_risk_tools(tool_calls or [])

    if matched_keywords:
        reasons.append("user_request_keyword")
    if task_types:
        reasons.append("task_type")
    if task_plan.critic_required and "task_plan" not in reasons:
        reasons.append("task_plan")
    if agent_names:
        reasons.append("agent_spec_critic_required")
    if high_risk_tools:
        reasons.append("tool_risk")

    return CriticPolicyDecision(
        required=bool(reasons),
        reasons=reasons,
        task_types=task_types,
        matched_keywords=matched_keywords,
        agent_names=agent_names,
        high_risk_tools=high_risk_tools,
    )


def _match_task_types(compact_query: str, task_plan: AgentTaskPlan) -> List[str]:
    """合并关键词命中的治理任务类型和 PlannerAgent 输出的业务任务类型。"""
    task_types: List[str] = []
    for task_type, keywords in TASK_TYPE_KEYWORDS.items():
        if any(keyword.lower().replace(" ", "") in compact_query for keyword in keywords):
            task_types.append(task_type)
    if task_plan.primary_task_type != "general_business_chat":
        task_types.append(task_plan.primary_task_type)
    return sorted(set(task_types))


def _match_critic_required_agents(agent_specs: Iterable[Any], tool_calls: Iterable[Dict[str, Any]]) -> List[str]:
    """根据已观察到的工具或 Agent 调用，找出需要 Critic 复核的 subagent。"""
    observed_tools = {str(call.get("tool_name", "")) for call in tool_calls}
    observed_subagents = {
        str(call.get("args", {}).get("subagent_type", ""))
        for call in tool_calls
        if isinstance(call.get("args"), dict)
    }
    agent_names: List[str] = []
    for spec in agent_specs:
        spec_name = str(getattr(spec, "name", ""))
        spec_tools = tuple(getattr(spec, "allowed_tools", getattr(spec, "tools", ())) or ())
        critic_required = bool(getattr(spec, "critic_required", False)) or str(getattr(spec, "risk_level", "")).lower() == "high"
        if not critic_required:
            continue
        if spec_name in observed_subagents or any(tool_name in observed_tools for tool_name in spec_tools):
            agent_names.append(spec_name)
    return agent_names


def _match_high_risk_tools(tool_calls: Iterable[Dict[str, Any]]) -> List[str]:
    """识别高风险或天然需要人工审核的工具调用。"""
    high_risk_tools: List[str] = []
    for call in tool_calls:
        tool_name = str(call.get("tool_name", ""))
        risk = str(call.get("risk", "")).lower()
        requires_human_approval = bool(call.get("requires_human_approval"))
        if not risk and tool_name in tool_registry._specs:
            # 兼容旧 trace：如果事件里没有风险字段，就回查 ToolRegistry 当前元数据。
            spec = tool_registry.get_spec(tool_name)
            risk = spec.risk.lower()
            requires_human_approval = spec.requires_human_approval
        if tool_name and (risk == "high" or requires_human_approval):
            high_risk_tools.append(tool_name)
    return sorted(set(high_risk_tools))


