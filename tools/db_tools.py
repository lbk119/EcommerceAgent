import os
import sys
import hashlib
import json
import threading
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from api.context import get_thread_context
from api.monitor import monitor
from langchain_core.tools import tool
from agent.core.db import execute_read_sql_raw, get_table_schema_raw

_DB_GUARD_LOCK = threading.Lock()
_DB_GUARD_STATE = {}


def _get_db_guard_state():
    thread_id = get_thread_context() or "__script__"
    with _DB_GUARD_LOCK:
        return _DB_GUARD_STATE.setdefault(thread_id, {
            "calls": 0,
            "schema_calls": 0,
            "sql_calls": 0,
            "fingerprints": [],
            "specialized_result_ready": False,
        })


def reset_db_guard_state():
    thread_id = get_thread_context() or "__script__"
    with _DB_GUARD_LOCK:
        _DB_GUARD_STATE.pop(thread_id, None)


def _fingerprint_tool_call(tool_name, args):
    raw = json.dumps({"tool": tool_name, "args": args}, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _record_db_tool_call(tool_name, args=None):
    # 工具层 guard 现在是兜底保险：主要循环控制在 database_workflow_tool.py 的状态机节点里。
    # 如果未来有人绕过状态机直接暴露原子 DB 工具，这里仍会按 session 熔断重复调用。
    args = args or {}
    state = _get_db_guard_state()

    if state["specialized_result_ready"] and tool_name != "analyze_top_products":
        _raise_db_guard_error(
            "数据库子 Agent 已通过 analyze_top_products 获得爆品分析完整指标，"
            "禁止继续探索表结构或重复 SQL。请立即基于已有工具结果输出最终分析和放量建议。"
        )

    state["calls"] += 1
    if tool_name == "get_table_schema":
        state["schema_calls"] += 1
    if tool_name == "execute_sql_query":
        state["sql_calls"] += 1

    max_calls = int(os.getenv("DB_AGENT_MAX_TOOL_CALLS", "10"))
    max_schema_calls = int(os.getenv("DB_AGENT_MAX_SCHEMA_CALLS", "5"))
    max_sql_calls = int(os.getenv("DB_AGENT_MAX_SQL_CALLS", "4"))

    fingerprint = _fingerprint_tool_call(tool_name, args)
    state["fingerprints"].append(fingerprint)
    recent_fingerprints = state["fingerprints"][-6:]

    if recent_fingerprints.count(fingerprint) >= 2:
        _raise_db_guard_error(f"数据库子 Agent 重复调用 {tool_name} 且参数相同，已触发工具层循环熔断。")
    if state["calls"] > max_calls:
        _raise_db_guard_error(f"数据库子 Agent 工具调用超过上限 {max_calls} 次，已触发工具层循环熔断。")
    if state["schema_calls"] > max_schema_calls:
        _raise_db_guard_error(f"数据库子 Agent 表结构查询超过上限 {max_schema_calls} 次，已触发工具层循环熔断。")
    if state["sql_calls"] > max_sql_calls:
        _raise_db_guard_error(f"数据库子 Agent SQL 查询超过上限 {max_sql_calls} 次，已触发工具层循环熔断。")


def _raise_db_guard_error(message):
    monitor._emit("db_loop_guard", message)
    raise RuntimeError(message)


def _mark_specialized_result_ready(result):
    if len(str(result).splitlines()) > 1:
        _get_db_guard_state()["specialized_result_ready"] = True


@tool
def get_table_schema(table_name)->str:
    """
    查询指定表的字段结构，不读取业务数据。
    用于让数据库助手快速确认字段名，避免为了看字段反复拉取前100行样例。
    """
    _record_db_tool_call("get_table_schema", {"table_name": table_name})
    monitor.report_tool(tool_name="数据库表结构查询工具：get_table_schema", args={"table_name": table_name})
    return get_table_schema_raw(table_name)


@tool
def execute_sql_query(query)->str:
    """
    执行自定义只读 SQL 查询语句。复杂查询前可通过 get_table_schema 明确表结构。
    :param query: 要执行的自定义sql语句
    :return: csv格式的数据（模拟表格数据格式）
             1.第一行是列信息，列之间使用,（英文的逗号）分割
             2.第二行开始是表数据，值之间也使用,(英文的逗号)分割
             3.行和行之间使用\n分割
             4.至多表数据查询100条
             例如：
                id,name,age\n -> 列头
                1,张三,18\n
                1,张三,18\n    -> 至多查询100条
    """
    # 埋点,调用工具了告诉前端哪个工具被调用了！！
    _record_db_tool_call("execute_sql_query", {"query": query})
    monitor.report_tool(tool_name="数据库表数据查询工具：execute_sql_query", args={"query":query})

    return execute_read_sql_raw(query)


@tool
def analyze_top_products(days: int = 365, limit: int = 5)->str:
    """
    爆品分析专用查询：一次性返回最近N天销售、价格、流量、转化、库存、活动和退款指标。
    适合回答“今日或最近表现最好的爆品”“放量建议”等商品运营问题。
    """
    days = max(1, min(int(days), 365))
    limit = max(1, min(int(limit), 20))
    _record_db_tool_call("analyze_top_products", {"days": days, "limit": limit})
    monitor.report_tool(tool_name="爆品综合分析工具：analyze_top_products", args={"days": days, "limit": limit})

    # Olist 演示数据的尾部日期较稀疏，先按窗口查；如果没有结果，自动回退到全量历史。
    date_filter = f"AND o.order_purchase_timestamp >= DATE_SUB((SELECT MAX(order_purchase_timestamp) FROM orders), INTERVAL {days} DAY)"

    def run_query(extra_date_filter):
        query = f"""
WITH sales AS (
    SELECT
        oi.product_id,
        COUNT(DISTINCT o.order_id) AS orders_count,
        COUNT(*) AS units_sold,
        ROUND(SUM(oi.price), 2) AS sales_amount,
        ROUND(AVG(oi.price), 2) AS avg_price,
        MIN(o.order_purchase_timestamp) AS first_order_time,
        MAX(o.order_purchase_timestamp) AS last_order_time
    FROM order_items oi
    JOIN orders o ON o.order_id = oi.order_id
    WHERE o.order_status IN ('delivered', 'shipped', 'invoiced', 'approved')
      {extra_date_filter}
    GROUP BY oi.product_id
),
traffic AS (
    SELECT
        product_id,
        SUM(views) AS views,
        SUM(visitors) AS visitors,
        SUM(add_to_cart) AS add_to_cart,
        SUM(favorites) AS favorites,
        SUM(conversions) AS conversions,
        ROUND(SUM(conversions) / NULLIF(SUM(visitors), 0), 4) AS conversion_rate
    FROM traffic_stats
    WHERE stat_date >= DATE_SUB((SELECT MAX(stat_date) FROM traffic_stats), INTERVAL {days} DAY)
    GROUP BY product_id
),
campaign AS (
    SELECT
        product_id,
        SUM(impressions) AS campaign_impressions,
        SUM(clicks) AS campaign_clicks,
        SUM(orders_count) AS campaign_orders,
        ROUND(SUM(revenue), 2) AS campaign_revenue,
        ROUND(SUM(spend), 2) AS campaign_spend,
        ROUND(SUM(revenue) / NULLIF(SUM(spend), 0), 2) AS campaign_roi
    FROM campaign_product_stats
    GROUP BY product_id
),
refund AS (
    SELECT
        product_id,
        COUNT(*) AS refunds_count,
        ROUND(SUM(refund_amount), 2) AS refund_amount
    FROM refunds
    WHERE refund_time >= DATE_SUB((SELECT MAX(refund_time) FROM refunds), INTERVAL {days} DAY)
    GROUP BY product_id
)
SELECT
    s.product_id,
    p.category_name_en,
    s.orders_count,
    s.units_sold,
    s.sales_amount,
    s.avg_price,
    COALESCE(t.views, 0) AS views,
    COALESCE(t.visitors, 0) AS visitors,
    COALESCE(t.conversions, 0) AS conversions,
    COALESCE(t.conversion_rate, 0) AS conversion_rate,
    COALESCE(i.stock, 0) AS stock,
    COALESCE(i.safety_stock, 0) AS safety_stock,
    COALESCE(c.campaign_impressions, 0) AS campaign_impressions,
    COALESCE(c.campaign_clicks, 0) AS campaign_clicks,
    COALESCE(c.campaign_orders, 0) AS campaign_orders,
    COALESCE(c.campaign_revenue, 0) AS campaign_revenue,
    COALESCE(c.campaign_spend, 0) AS campaign_spend,
    COALESCE(c.campaign_roi, 0) AS campaign_roi,
    COALESCE(r.refunds_count, 0) AS refunds_count,
    COALESCE(r.refund_amount, 0) AS refund_amount,
    s.first_order_time,
    s.last_order_time
FROM sales s
JOIN products p ON p.product_id = s.product_id
LEFT JOIN traffic t ON t.product_id = s.product_id
LEFT JOIN inventory i ON i.product_id = s.product_id
LEFT JOIN campaign c ON c.product_id = s.product_id
LEFT JOIN refund r ON r.product_id = s.product_id
ORDER BY s.sales_amount DESC, s.units_sold DESC
LIMIT {limit}
"""
        return execute_read_sql_raw(query)

    result = run_query(date_filter)
    if len(result.splitlines()) <= 1:
        result = "最近窗口内没有足够订单明细，已自动回退到全量历史数据。\n" + run_query("")
    _mark_specialized_result_ready(result)
    return result



if __name__ == "__main__":
    print(execute_sql_query.invoke({
        "query": """
            SELECT DATE(o.order_purchase_timestamp) AS order_date,
                   COUNT(DISTINCT o.order_id) AS orders,
                   ROUND(SUM(p.payment_value), 2) AS gmv
            FROM orders o
            JOIN payments p ON p.order_id = o.order_id
            GROUP BY DATE(o.order_purchase_timestamp)
            ORDER BY order_date DESC
            LIMIT 5
        """
    }))






