"""deepagents business subagent 注册表。

这里声明 EcommerceAgent 可委托的业务 subagents、各自工具白名单、profile 支持范围、输出 schema 和预算。
不要在这里创建 human_approval 或 deep_agent；审批属于 deepagents HITL，deep 是运行 profile。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent.subagent.config import get_deepagents_profile
from agent.subagent.tools import native_tools_for_names


@dataclass(frozen=True)
class DeepAgentsSubagentSpec:
    """单个业务 subagent 的平台治理规格。"""

    name: str
    description: str
    prompt: str
    allowed_tools: tuple[str, ...]
    supported_profiles: tuple[str, ...]
    output_schema: dict[str, Any]
    risk_level: str
    max_runtime_seconds: float
    max_tool_calls: int

    def to_deepagents_subagent(self, profile: str) -> dict[str, Any]:
        """转换成 `create_deep_agent` 接受的 subagent dict。"""
        return {
            "name": self.name,
            "description": self.description,
            "system_prompt": self.prompt,
            "tools": native_tools_for_names(self.allowed_tools, profile, owner=self.name),
        }


OUTPUT_SCHEMA = {
    "type": "object",
    "required": ["summary", "evidence", "actions", "risks", "missingData"],
    "properties": {
        "summary": {"type": "string"},
        "evidence": {"type": "array"},
        "actions": {"type": "array"},
        "risks": {"type": "array"},
        "missingData": {"type": "array"},
    },
}


SUBAGENT_SPECS: dict[str, DeepAgentsSubagentSpec] = {
    "product_analysis": DeepAgentsSubagentSpec(
        name="product_analysis",
        description="商品表现、爆品、低转化、商品优化、季节选品。",
        prompt="你是商品分析 subagent。只能基于允许工具和上游上下文输出商品机会、证据、动作、风险，不得编造工具结果。",
        allowed_tools=("query_hot_products", "query_low_conversion_products", "query_inventory_velocity", "query_campaign_roi", "query_shop_profile"),
        supported_profiles=("standard", "deep"),
        output_schema=OUTPUT_SCHEMA,
        risk_level="low",
        max_runtime_seconds=20,
        max_tool_calls=5,
    ),
    "inventory": DeepAgentsSubagentSpec(
        name="inventory",
        description="库存风险、缺货、滞销、补货、库存承接。",
        prompt="你是库存 subagent。优先识别缺货、低安全库存、滞销和补货优先级；如有商品上游结果，必须说明承接关系。",
        allowed_tools=("query_inventory_risks", "query_inventory_velocity", "query_sales_trend", "query_hot_products"),
        supported_profiles=("standard", "deep"),
        output_schema=OUTPUT_SCHEMA,
        risk_level="low",
        max_runtime_seconds=20,
        max_tool_calls=4,
    ),
    "campaign": DeepAgentsSubagentSpec(
        name="campaign",
        description="活动复盘、ROI、投放风险、活动承接。",
        prompt="你是活动 subagent。只分析活动流量、ROI、投放风险和库存/商品承接，不得提出未经证据支持的投放结论。",
        allowed_tools=("query_campaign_traffic", "query_campaign_roi", "query_campaign_risks", "query_hot_products", "query_inventory_velocity"),
        supported_profiles=("standard", "deep"),
        output_schema=OUTPUT_SCHEMA,
        risk_level="medium",
        max_runtime_seconds=20,
        max_tool_calls=5,
    ),
    "report": DeepAgentsSubagentSpec(
        name="report",
        description="日报、周报、经营总结、多 Agent 结果报告。",
        prompt="你是报告 subagent。汇总经营指标、风险、商品/库存/活动子结论，标准档只能写受控报告目录。",
        allowed_tools=("query_daily_metrics", "query_daily_risks", "query_hot_products", "query_campaign_roi", "query_inventory_velocity"),
        supported_profiles=("standard", "deep"),
        output_schema=OUTPUT_SCHEMA,
        risk_level="medium",
        max_runtime_seconds=25,
        max_tool_calls=6,
    ),
    "data_quality": DeepAgentsSubagentSpec(
        name="data_quality",
        description="数据导入、数据新鲜度、授权、schema 健康。",
        prompt="你是数据质量 subagent。检查导入任务、平台授权、数据新鲜度和 schema 健康；不做业务结论夸大。",
        allowed_tools=("check_import_jobs", "check_data_freshness", "check_platform_authorization", "check_schema_health"),
        supported_profiles=("standard", "deep"),
        output_schema=OUTPUT_SCHEMA,
        risk_level="low",
        max_runtime_seconds=15,
        max_tool_calls=4,
    ),
    "knowledge_base": DeepAgentsSubagentSpec(
        name="knowledge_base",
        description="历史记忆、历史报告、历史策略、经验复用。",
        prompt="你是知识库 subagent。standard 只做轻量检索，deep 可做更深入历史策略复用；必须标注历史信息的适用边界。",
        allowed_tools=("search_memory", "search_reports", "search_strategy_candidates"),
        supported_profiles=("standard", "deep"),
        output_schema=OUTPUT_SCHEMA,
        risk_level="low",
        max_runtime_seconds=15,
        max_tool_calls=3,
    ),
    "network_search": DeepAgentsSubagentSpec(
        name="network_search",
        description="外部趋势、市场信息、竞品信息、季节趋势。",
        prompt="你是网络搜索 subagent。仅 deep profile 可用；外部信息必须标注来源不确定性，不得替代店铺真实数据。",
        allowed_tools=("internet_search",),
        supported_profiles=("deep",),
        output_schema=OUTPUT_SCHEMA,
        risk_level="medium",
        max_runtime_seconds=60,
        max_tool_calls=5,
    ),
    "database_query": DeepAgentsSubagentSpec(
        name="database_query",
        description="复杂数据库问题、schema lookup、受控只读查询。",
        prompt="你是数据库查询 subagent。只能做 tenant/shop scoped 的 schema lookup 和安全只读查询；禁止自由 SQL、写 SQL 和越权查询。",
        allowed_tools=("schema_lookup", "safe_read_sql"),
        supported_profiles=("standard", "deep"),
        output_schema=OUTPUT_SCHEMA,
        risk_level="high",
        max_runtime_seconds=30,
        max_tool_calls=4,
    ),
}


def get_subagent_specs(profile: str) -> list[DeepAgentsSubagentSpec]:
    config = get_deepagents_profile(profile)
    return [SUBAGENT_SPECS[name] for name in config.subagents if name in SUBAGENT_SPECS and config.name in SUBAGENT_SPECS[name].supported_profiles]


def build_deepagents_subagents(profile: str) -> list[dict[str, Any]]:
    return [spec.to_deepagents_subagent(profile) for spec in get_subagent_specs(profile)]