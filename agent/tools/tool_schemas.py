"""deepagents-native 工具参数 schema 目录。

这些 schema 控制模型能向受控工具传入哪些参数。它们不是业务校验的唯一来源，但能减少模型暴露面，
并让前端、subagent registry 和工具 wrapper 复用同一份描述。
"""

from __future__ import annotations

from typing import Any


COMMON_PROPERTIES: dict[str, dict[str, Any]] = {
    "time_range": {"type": "string", "max_days": 365},
    "limit": {"type": "integer", "minimum": 1, "maximum": 50},
    "category": {"type": "string"},
    "metric_focus": {"type": "string"},
    "campaign_keyword": {"type": "string"},
    "product_ids": {"type": "array", "items": {"type": "string"}},
    "include_inventory": {"type": "boolean"},
    "include_campaign": {"type": "boolean"},
    "only_low_stock": {"type": "boolean"},
    "include_sales_velocity": {"type": "boolean"},
    "report_type": {"type": "string", "enum": ["daily", "weekly", "monthly", "business"]},
    "query": {"type": "string"},
}


def object_schema(description: str, *, required: list[str] | None = None, properties: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    """构造标准 object 参数 schema，默认使用通用经营查询参数。"""
    return {
        "description": description,
        "params_schema": {
            "type": "object",
            "properties": properties or COMMON_PROPERTIES,
            "required": required or ["time_range", "limit"],
        },
    }


TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    "query_hot_products": object_schema("查询热销或可放量商品"),
    "query_low_conversion_products": object_schema("查询低转化但有优化空间的商品"),
    "query_inventory_velocity": object_schema("查询库存周转、补货速度和库存承接"),
    "query_campaign_roi": object_schema("查询活动 ROI 和成交效率"),
    "query_shop_profile": object_schema("查询店铺画像", required=[], properties={"query": COMMON_PROPERTIES["query"]}),
    "query_inventory_risks": object_schema("查询缺货、低安全库存和滞销风险"),
    "query_sales_trend": object_schema("查询销售趋势"),
    "query_campaign_traffic": object_schema("查询活动流量和转化表现"),
    "query_campaign_risks": object_schema("查询活动风险"),
    "query_daily_metrics": object_schema("查询经营核心指标"),
    "query_daily_risks": object_schema("查询经营风险"),
    "check_import_jobs": object_schema("检查数据导入任务", required=[], properties={"query": COMMON_PROPERTIES["query"], "limit": COMMON_PROPERTIES["limit"]}),
    "check_data_freshness": object_schema("检查数据新鲜度", required=[], properties={"query": COMMON_PROPERTIES["query"]}),
    "check_platform_authorization": object_schema("检查平台授权状态", required=[], properties={"query": COMMON_PROPERTIES["query"]}),
    "check_schema_health": object_schema("检查核心数据表健康状态", required=[], properties={"query": COMMON_PROPERTIES["query"]}),
    "search_memory": object_schema("检索历史记忆", required=["query"], properties={"query": COMMON_PROPERTIES["query"], "limit": COMMON_PROPERTIES["limit"]}),
    "search_reports": object_schema("检索历史报告", required=["query"], properties={"query": COMMON_PROPERTIES["query"], "limit": COMMON_PROPERTIES["limit"]}),
    "search_strategy_candidates": object_schema("检索历史策略候选", required=["query"], properties={"query": COMMON_PROPERTIES["query"], "limit": COMMON_PROPERTIES["limit"]}),
    "internet_search": object_schema("外部网络搜索", required=["query"], properties={"query": COMMON_PROPERTIES["query"], "limit": COMMON_PROPERTIES["limit"]}),
    "schema_lookup": object_schema("查看允许范围内的数据表结构", required=["query"], properties={"query": COMMON_PROPERTIES["query"]}),
    "safe_read_sql": object_schema("执行批准后的只读 SQL", required=["query"], properties={"query": COMMON_PROPERTIES["query"]}),
}


PRODUCT_AWARE_PROPERTIES = {**COMMON_PROPERTIES, "product_ids": COMMON_PROPERTIES["product_ids"]}

for _tool_name in ("query_inventory_velocity", "query_inventory_risks", "query_sales_trend", "query_campaign_roi", "query_campaign_traffic", "query_campaign_risks"):
    if _tool_name in TOOL_SCHEMAS:
        TOOL_SCHEMAS[_tool_name]["params_schema"]["properties"] = PRODUCT_AWARE_PROPERTIES


def schemas_for(tool_names: set[str]) -> dict[str, dict[str, Any]]:
    """按工具名返回可展示或可注入模型的 schema 子集。"""
    return {name: TOOL_SCHEMAS[name] for name in sorted(tool_names) if name in TOOL_SCHEMAS}