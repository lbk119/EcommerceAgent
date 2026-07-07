"""
电商业务指标能力层。

这些函数是 deterministic workflow 的数据库节点：SQL 固定、只读、输入少，避免让 LLM 在日报、库存、
活动复盘这类高频任务里反复自由生成通用 SQL。上层 WorkflowRunner 只负责组合节点和生成表达层总结。
"""

from dataclasses import dataclass
import re

from agent.core.db import current_data_scope_sql, execute_read_sql_raw
from api.context import get_identity_context


@dataclass(frozen=True)
class BusinessTimeRange:
    """
    业务 workflow 的轻量时间范围。

    anchor_date_sql 使用各业务表里的最大日期作为“今天”，是为了兼容 Olist/demo 数据和真实当前日期
    不一致的问题；用户说“今天/昨天/最近 7 天”时，语义会映射到 demo 数据的最新业务日期。
    """

    label: str
    days: int
    offset_days: int = 0
    custom_note: str = ""
    campaign_keyword: str = ""

    def to_metadata(self) -> dict:
        """转换为 trace/structuredResult 可序列化的时间范围结构。"""
        return {
            "label": self.label,
            "days": self.days,
            "offset_days": self.offset_days,
            "custom_note": self.custom_note,
            "campaign_keyword": self.campaign_keyword,
        }


def parse_business_time_range(query: str) -> BusinessTimeRange:
    """从用户 query 中解析 today/yesterday/last_7d/last_30d/custom 等轻量时间范围。"""
    # 简单规则解析即可满足高频经营问法；不调用 LLM，避免 workflow 起步就产生模型成本。
    compact_query = query.lower().replace(" ", "")
    if any(keyword in compact_query for keyword in ("今天", "今日", "today")):
        return BusinessTimeRange("today", 1)
    if any(keyword in compact_query for keyword in ("昨天", "昨日", "yesterday")):
        return BusinessTimeRange("yesterday", 1, offset_days=1)
    if any(keyword in compact_query for keyword in ("本周", "thisweek", "week")):
        return BusinessTimeRange("last_7d", 7)
    if any(keyword in compact_query for keyword in ("最近7天", "近7天", "7天", "last7d", "last_7d")):
        return BusinessTimeRange("last_7d", 7)
    if "618" in compact_query:
        return BusinessTimeRange("custom", 30, custom_note="campaign_period_keyword", campaign_keyword="618")
    if any(keyword in compact_query for keyword in ("双十一", "双11")):
        return BusinessTimeRange("custom", 30, custom_note="campaign_period_keyword", campaign_keyword="双")
    if "大促" in compact_query:
        return BusinessTimeRange("custom", 30, custom_note="campaign_period_keyword", campaign_keyword="大促")
    match = re.search(r"(?:最近|近|last)(\d+)(?:天|d|day|days)?", compact_query)
    if match:
        days = max(1, min(int(match.group(1)), 365))
        return BusinessTimeRange("custom", days, custom_note=f"last_{days}d")
    return BusinessTimeRange("last_30d", 30)


def query_daily_metrics(time_range: BusinessTimeRange | None = None) -> str:
    """
    查询经营日报核心指标。

    默认最近 30 天；如果用户 query 解析出今天、昨天、本周或最近 N 天，则使用对应时间窗口。
    """
    time_range = time_range or BusinessTimeRange("last_30d", 30)
    # 每个参与 JOIN 的经营表都加 tenant/shop scope，防止 JOIN 时某张表漏过滤导致跨租户混数。
    data_scope = _required_data_scope("o")
    item_scope = _required_data_scope("oi")
    review_scope = _required_data_scope("r")
    refund_scope = _required_data_scope("rf")
    order_filter = _time_filter("o.order_purchase_timestamp", "b.max_time", time_range)
    refund_filter = _time_filter("rf.refund_time", "b.max_time", time_range)
    return execute_read_sql_raw(f"""
WITH bounds AS (
    SELECT MAX(order_purchase_timestamp) AS max_time FROM orders
), sales AS (
    SELECT
        COUNT(DISTINCT o.order_id) AS orders_count,
        COUNT(oi.product_id) AS units_sold,
        ROUND(SUM(oi.price), 2) AS gmv,
        ROUND(SUM(oi.price) / NULLIF(COUNT(DISTINCT o.order_id), 0), 2) AS aov
    FROM orders o
    JOIN order_items oi ON oi.order_id = o.order_id
    CROSS JOIN bounds b
    WHERE o.order_status IN ('delivered', 'shipped', 'invoiced', 'approved')
            AND {data_scope}
            AND {item_scope}
            AND {order_filter}
), reviews_30d AS (
    SELECT
        ROUND(AVG(r.review_score), 2) AS avg_review_score,
        SUM(CASE WHEN r.review_score <= 2 THEN 1 ELSE 0 END) AS bad_reviews
    FROM reviews r
    JOIN orders o ON o.order_id = r.order_id
    CROSS JOIN bounds b
    WHERE {review_scope}
            AND {data_scope}
            AND {order_filter}
), refunds_30d AS (
    SELECT
        COUNT(*) AS refund_count,
        ROUND(SUM(refund_amount), 2) AS refund_amount
    FROM refunds rf
    CROSS JOIN bounds b
    WHERE {refund_scope}
            AND {refund_filter}
)
SELECT
    COALESCE(s.orders_count, 0) AS orders_count,
    COALESCE(s.units_sold, 0) AS units_sold,
    COALESCE(s.gmv, 0) AS gmv,
    COALESCE(s.aov, 0) AS aov,
    COALESCE(rv.avg_review_score, 0) AS avg_review_score,
    COALESCE(rv.bad_reviews, 0) AS bad_reviews,
    COALESCE(rf.refund_count, 0) AS refund_count,
    COALESCE(rf.refund_amount, 0) AS refund_amount,
    ROUND(COALESCE(rf.refund_count, 0) / NULLIF(COALESCE(s.orders_count, 0), 0), 4) AS refund_rate
FROM sales s
CROSS JOIN reviews_30d rv
CROSS JOIN refunds_30d rf
""")


def query_daily_risks(time_range: BusinessTimeRange | None = None) -> str:
    """查询日报需要关注的经营风险：低库存、差评、退款和客服问题。"""
    time_range = time_range or BusinessTimeRange("last_30d", 30)
    data_scope = _required_data_scope("o")
    inventory_scope = _required_data_scope("inventory")
    review_scope = _required_data_scope("r")
    refund_scope = _required_data_scope("rf")
    ticket_scope = _required_data_scope("t")
    order_filter = _time_filter("o.order_purchase_timestamp", "b.max_time", time_range)
    refund_filter = _time_filter("rf.refund_time", "b.max_time", time_range)
    ticket_filter = _time_filter("t.ticket_time", "b.max_time", time_range)
    return execute_read_sql_raw(f"""
WITH bounds AS (
    SELECT MAX(order_purchase_timestamp) AS max_time FROM orders
), low_inventory AS (
    SELECT COUNT(*) AS low_inventory_products
    FROM inventory
    WHERE {inventory_scope}
            AND stock <= safety_stock
), bad_reviews AS (
    SELECT COUNT(*) AS bad_review_count
    FROM reviews r
    JOIN orders o ON o.order_id = r.order_id
    CROSS JOIN bounds b
    WHERE r.review_score <= 2
            AND {review_scope}
            AND {data_scope}
            AND {order_filter}
), refund_reasons AS (
    SELECT refund_reason, COUNT(*) AS refund_count
    FROM refunds rf
    CROSS JOIN bounds b
        WHERE {refund_scope}
            AND {refund_filter}
    GROUP BY refund_reason
    ORDER BY refund_count DESC
    LIMIT 3
), service_risks AS (
    SELECT issue_type, COUNT(*) AS ticket_count
    FROM customer_service_tickets t
    CROSS JOIN bounds b
        WHERE {ticket_scope}
            AND {ticket_filter}
    GROUP BY issue_type
    ORDER BY ticket_count DESC
    LIMIT 3
)
SELECT 'low_inventory_products' AS risk_type, CAST(low_inventory_products AS CHAR) AS risk_value FROM low_inventory
UNION ALL
SELECT 'bad_review_count', CAST(bad_review_count AS CHAR) FROM bad_reviews
UNION ALL
SELECT CONCAT('top_refund_reason:', refund_reason), CAST(refund_count AS CHAR) FROM refund_reasons
UNION ALL
SELECT CONCAT('top_service_issue:', issue_type), CAST(ticket_count AS CHAR) FROM service_risks
""")


def query_shop_profile() -> str:
    """查询当前店铺画像，作为季节性选品和经营策略类 workflow 的上下文节点。"""
    identity = get_identity_context()
    if not identity or not identity.tenant_id or not identity.shop_id:
        raise RuntimeError("缺少可信 tenant/shop 上下文，无法执行店铺画像 workflow")
    # 店铺画像来自平台表，仍使用当前身份上下文限制到当前租户/店铺。
    tenant_id = _sql_literal(identity.tenant_id)
    shop_id = _sql_literal(identity.shop_id)
    return execute_read_sql_raw(f"""
SELECT
    gs.id AS shop_id,
    gs.name AS shop_name,
    gs.category,
    gs.platform,
    gs.shop_type,
    gs.business_stage,
    gs.auth_status,
    gs.data_status,
    gs.last_sync_at
FROM gateway_shops gs
WHERE gs.tenant_id = {tenant_id} AND gs.id = {shop_id}
LIMIT 1
""")


def query_inventory_risks(time_range: BusinessTimeRange | None = None) -> str:
    """查询库存预警的风险 SKU，固定覆盖低库存、缺货和滞销信号。"""
    time_range = time_range or BusinessTimeRange("last_30d", 30)
    order_bound_scope = _required_data_scope("orders")
    data_scope = _required_data_scope("o")
    item_scope = _required_data_scope("oi")
    inventory_scope = _required_data_scope("i")
    product_scope = _required_data_scope("p")
    order_filter = _time_filter("o.order_purchase_timestamp", "b.max_time", time_range)
    return execute_read_sql_raw(f"""
WITH bounds AS (
    SELECT MAX(order_purchase_timestamp) AS max_time FROM orders WHERE {order_bound_scope}
), recent_sales AS (
    SELECT
        oi.product_id,
        COUNT(*) AS units_sold_30d,
        ROUND(SUM(oi.price), 2) AS sales_amount_30d
    FROM order_items oi
    JOIN orders o ON o.order_id = oi.order_id
        CROSS JOIN bounds b
    WHERE o.order_status IN ('delivered', 'shipped', 'invoiced', 'approved')
            AND {data_scope}
            AND {item_scope}
            AND {order_filter}
    GROUP BY oi.product_id
)
SELECT
    i.product_id,
    p.category_name_en,
    i.stock,
    i.safety_stock,
    COALESCE(rs.units_sold_30d, 0) AS units_sold_30d,
    COALESCE(rs.sales_amount_30d, 0) AS sales_amount_30d,
    CASE
        WHEN i.stock <= 0 THEN 'out_of_stock'
        WHEN i.stock <= i.safety_stock THEN 'below_safety_stock'
        WHEN COALESCE(rs.units_sold_30d, 0) = 0 AND i.stock > i.safety_stock THEN 'slow_moving'
        ELSE 'watch'
    END AS risk_type
FROM inventory i
JOIN products p ON p.product_id = i.product_id
LEFT JOIN recent_sales rs ON rs.product_id = i.product_id
WHERE {inventory_scope}
    AND {product_scope}
    AND (i.stock <= i.safety_stock
          OR i.stock <= 0
          OR (COALESCE(rs.units_sold_30d, 0) = 0 AND i.stock > i.safety_stock))
ORDER BY
    CASE
        WHEN i.stock <= 0 THEN 1
        WHEN i.stock <= i.safety_stock THEN 2
        ELSE 3
    END,
    COALESCE(rs.sales_amount_30d, 0) DESC
LIMIT 20
""")


def query_inventory_velocity(time_range: BusinessTimeRange | None = None) -> str:
    """查询补货判断需要的销量速度、安全库存和建议补货量。"""
    time_range = time_range or BusinessTimeRange("last_30d", 30)
    order_bound_scope = _required_data_scope("orders")
    data_scope = _required_data_scope("o")
    item_scope = _required_data_scope("oi")
    inventory_scope = _required_data_scope("i")
    product_scope = _required_data_scope("p")
    order_filter = _time_filter("o.order_purchase_timestamp", "b.max_time", time_range)
    return execute_read_sql_raw(f"""
WITH bounds AS (
    SELECT MAX(order_purchase_timestamp) AS max_time FROM orders WHERE {order_bound_scope}
), sales_30d AS (
    SELECT
        oi.product_id,
        COUNT(*) AS units_sold_30d,
        ROUND(COUNT(*) / {time_range.days}, 2) AS avg_daily_units,
        ROUND(SUM(oi.price), 2) AS sales_amount_30d
    FROM order_items oi
    JOIN orders o ON o.order_id = oi.order_id
        CROSS JOIN bounds b
    WHERE o.order_status IN ('delivered', 'shipped', 'invoiced', 'approved')
            AND {data_scope}
            AND {item_scope}
            AND {order_filter}
    GROUP BY oi.product_id
)
SELECT
    i.product_id,
    p.category_name_en,
    i.stock,
    i.safety_stock,
    COALESCE(s.units_sold_30d, 0) AS units_sold_30d,
    COALESCE(s.avg_daily_units, 0) AS avg_daily_units,
    COALESCE(s.sales_amount_30d, 0) AS sales_amount_30d,
    GREATEST(i.safety_stock * 2 - i.stock, 0) AS suggested_replenishment,
    CASE
        WHEN i.stock <= i.safety_stock AND COALESCE(s.units_sold_30d, 0) > 0 THEN 'replenish_first'
        WHEN i.stock <= i.safety_stock THEN 'protect_stock'
        WHEN COALESCE(s.units_sold_30d, 0) = 0 THEN 'clearance_or_pause'
        ELSE 'normal'
    END AS suggested_action
FROM inventory i
JOIN products p ON p.product_id = i.product_id
LEFT JOIN sales_30d s ON s.product_id = i.product_id
WHERE {inventory_scope}
    AND {product_scope}
ORDER BY suggested_replenishment DESC, sales_amount_30d DESC
LIMIT 20
""")


def query_campaign_traffic(time_range: BusinessTimeRange | None = None) -> str:
    """查询活动复盘的曝光、点击和 CTR。"""
    time_range = time_range or BusinessTimeRange("last_30d", 30)
    campaign_filter = _campaign_time_filter(time_range)
    campaign_scope = _required_data_scope("c")
    stats_scope = _required_data_scope("cps")
    return execute_read_sql_raw(f"""
SELECT
    c.campaign_id,
    c.campaign_name,
    c.channel,
    c.status,
    SUM(cps.impressions) AS impressions,
    SUM(cps.clicks) AS clicks,
    ROUND(SUM(cps.clicks) / NULLIF(SUM(cps.impressions), 0), 4) AS ctr
FROM campaigns c
JOIN campaign_product_stats cps ON cps.campaign_id = c.campaign_id
WHERE {campaign_filter}
    AND {campaign_scope}
    AND {stats_scope}
GROUP BY c.campaign_id, c.campaign_name, c.channel, c.status
ORDER BY impressions DESC
LIMIT 20
""")


def query_campaign_roi(time_range: BusinessTimeRange | None = None) -> str:
    """查询活动复盘的转化、GMV、花费和 ROI。"""
    time_range = time_range or BusinessTimeRange("last_30d", 30)
    campaign_filter = _campaign_time_filter(time_range)
    campaign_scope = _required_data_scope("c")
    stats_scope = _required_data_scope("cps")
    return execute_read_sql_raw(f"""
SELECT
    c.campaign_id,
    c.campaign_name,
    c.channel,
    SUM(cps.orders_count) AS orders_count,
    ROUND(SUM(cps.revenue), 2) AS revenue,
    ROUND(SUM(cps.spend), 2) AS spend,
    ROUND(SUM(cps.revenue) / NULLIF(SUM(cps.spend), 0), 2) AS roi,
    ROUND(SUM(cps.orders_count) / NULLIF(SUM(cps.clicks), 0), 4) AS click_to_order_rate
FROM campaigns c
JOIN campaign_product_stats cps ON cps.campaign_id = c.campaign_id
WHERE {campaign_filter}
    AND {campaign_scope}
    AND {stats_scope}
GROUP BY c.campaign_id, c.campaign_name, c.channel
ORDER BY roi DESC, revenue DESC
LIMIT 20
""")


def query_campaign_risks(time_range: BusinessTimeRange | None = None) -> str:
    """查询活动商品相关退款、库存和差评风险。"""
    time_range = time_range or BusinessTimeRange("last_30d", 30)
    campaign_filter = _campaign_time_filter(time_range)
    campaign_scope = _required_data_scope("c")
    stats_scope = _required_data_scope("campaign_product_stats")
    product_scope = _required_data_scope("p")
    inventory_scope = _required_data_scope("i")
    refund_scope = _required_data_scope("rf")
    item_scope = _required_data_scope("oi")
    order_scope = _required_data_scope("o")
    review_scope = _required_data_scope("r")
    return execute_read_sql_raw(f"""
WITH campaign_products AS (
    SELECT DISTINCT campaign_id, product_id FROM campaign_product_stats
    WHERE {stats_scope}
), refund_risk AS (
    SELECT
        cp.campaign_id,
        cp.product_id,
        COUNT(rf.refund_id) AS refund_count,
        ROUND(SUM(rf.refund_amount), 2) AS refund_amount
    FROM campaign_products cp
    LEFT JOIN refunds rf ON rf.product_id = cp.product_id
        AND {refund_scope}
    GROUP BY cp.campaign_id, cp.product_id
), review_risk AS (
    SELECT
        cp.campaign_id,
        cp.product_id,
        COUNT(CASE WHEN r.review_score <= 2 THEN 1 END) AS bad_review_count
    FROM campaign_products cp
    LEFT JOIN order_items oi ON oi.product_id = cp.product_id
        AND {item_scope}
    LEFT JOIN orders o ON o.order_id = oi.order_id
        AND {order_scope}
    LEFT JOIN reviews r ON r.order_id = o.order_id
        AND {review_scope}
    GROUP BY cp.campaign_id, cp.product_id
)
SELECT
    c.campaign_id,
    c.campaign_name,
    cp.product_id,
    p.category_name_en,
    i.stock,
    i.safety_stock,
    COALESCE(rr.refund_count, 0) AS refund_count,
    COALESCE(rr.refund_amount, 0) AS refund_amount,
    COALESCE(rv.bad_review_count, 0) AS bad_review_count,
    CASE
        WHEN i.stock <= i.safety_stock THEN 'inventory_risk'
        WHEN COALESCE(rr.refund_count, 0) > 0 THEN 'refund_risk'
        WHEN COALESCE(rv.bad_review_count, 0) > 0 THEN 'review_risk'
        ELSE 'normal'
    END AS risk_type
FROM campaigns c
JOIN campaign_products cp ON cp.campaign_id = c.campaign_id
JOIN products p ON p.product_id = cp.product_id
LEFT JOIN inventory i ON i.product_id = cp.product_id AND {inventory_scope}
LEFT JOIN refund_risk rr ON rr.campaign_id = cp.campaign_id AND rr.product_id = cp.product_id
LEFT JOIN review_risk rv ON rv.campaign_id = cp.campaign_id AND rv.product_id = cp.product_id
WHERE {campaign_filter}
    AND {campaign_scope}
    AND {product_scope}
  AND (i.stock <= i.safety_stock
    OR COALESCE(rr.refund_count, 0) > 0
    OR COALESCE(rv.bad_review_count, 0) > 0)
ORDER BY COALESCE(rr.refund_amount, 0) DESC, COALESCE(rv.bad_review_count, 0) DESC
LIMIT 20
""")


def query_hot_products(time_range: BusinessTimeRange | None = None, limit: int = 5) -> str:
    """
    查询爆品分析的完整候选指标。

    这个查询是 DeepAgent 自由调库的确定性替代：一次 SQL 覆盖销售、流量、转化、价格、库存、活动和退款，
    避免主 Agent 因为缺少终止信号而反复调用数据库助手。多租户/多店铺隔离由 api.context 中的身份
    上下文和 _required_data_scope 强制拼接到每张经营表上。
    """
    time_range = time_range or BusinessTimeRange("last_30d", 30)
    limit = max(1, min(int(limit), 20))

    def run_query(active_time_range: BusinessTimeRange) -> str:
        """按给定时间范围执行爆品聚合 SQL。

        拆成内部函数是为了在短时间窗口无数据时复用同一套 SQL，自动扩大到最近 365 天。
        """
        order_bound_scope = _required_data_scope("orders")
        traffic_bound_scope = _required_data_scope("traffic_stats")
        refund_bound_scope = _required_data_scope("refunds")
        order_scope = _required_data_scope("o")
        item_scope = _required_data_scope("oi")
        traffic_scope = _required_data_scope("ts")
        refund_scope = _required_data_scope("rf")
        campaign_scope = _required_data_scope("campaign_product_stats")
        product_scope = _required_data_scope("p")
        inventory_scope = _required_data_scope("i")
        order_filter = _time_filter("o.order_purchase_timestamp", "b.max_time", active_time_range)
        traffic_filter = _time_filter("ts.stat_date", "tb.max_time", active_time_range)
        refund_filter = _time_filter("rf.refund_time", "rb.max_time", active_time_range)
        return execute_read_sql_raw(f"""
WITH order_bounds AS (
    SELECT MAX(order_purchase_timestamp) AS max_time FROM orders WHERE {order_bound_scope}
), traffic_bounds AS (
    SELECT MAX(stat_date) AS max_time FROM traffic_stats WHERE {traffic_bound_scope}
), refund_bounds AS (
    SELECT MAX(refund_time) AS max_time FROM refunds WHERE {refund_bound_scope}
), sales AS (
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
    CROSS JOIN order_bounds b
    WHERE o.order_status IN ('delivered', 'shipped', 'invoiced', 'approved')
            AND {order_scope}
            AND {item_scope}
      AND {order_filter}
    GROUP BY oi.product_id
), traffic AS (
    SELECT
        ts.product_id,
        SUM(ts.views) AS views,
        SUM(ts.visitors) AS visitors,
        SUM(ts.add_to_cart) AS add_to_cart,
        SUM(ts.favorites) AS favorites,
        SUM(ts.conversions) AS conversions,
        ROUND(SUM(ts.conversions) / NULLIF(SUM(ts.visitors), 0), 4) AS conversion_rate
    FROM traffic_stats ts
    CROSS JOIN traffic_bounds tb
    WHERE {traffic_filter}
            AND {traffic_scope}
    GROUP BY ts.product_id
), campaign AS (
    SELECT
        product_id,
        SUM(impressions) AS campaign_impressions,
        SUM(clicks) AS campaign_clicks,
        SUM(orders_count) AS campaign_orders,
        ROUND(SUM(revenue), 2) AS campaign_revenue,
        ROUND(SUM(spend), 2) AS campaign_spend,
        ROUND(SUM(revenue) / NULLIF(SUM(spend), 0), 2) AS campaign_roi
    FROM campaign_product_stats
    WHERE {campaign_scope}
    GROUP BY product_id
), refund AS (
    SELECT
        rf.product_id,
        COUNT(*) AS refunds_count,
        ROUND(SUM(rf.refund_amount), 2) AS refund_amount
    FROM refunds rf
    CROSS JOIN refund_bounds rb
    WHERE {refund_filter}
            AND {refund_scope}
    GROUP BY rf.product_id
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
LEFT JOIN inventory i ON i.product_id = s.product_id AND {inventory_scope}
LEFT JOIN campaign c ON c.product_id = s.product_id
LEFT JOIN refund r ON r.product_id = s.product_id
WHERE {product_scope}
ORDER BY s.sales_amount DESC, s.units_sold DESC
LIMIT {limit}
""")

    result = run_query(time_range)
    if len(result.splitlines()) > 1:
        return result

    # Demo/历史数据的“今天”不一定有足够订单，固定 workflow 不能因为短窗口为空就回落 DeepAgent 反复调库。
    fallback_result = run_query(BusinessTimeRange("last_365d", 365))
    if len(fallback_result.splitlines()) > 1:
        return "请求时间窗口内没有足够订单明细，已自动扩大到最近 365 天业务数据。\n" + fallback_result
    return fallback_result


def _time_filter(column: str, anchor_sql: str, time_range: BusinessTimeRange) -> str:
    """生成相对业务锚点日期的 SQL 时间过滤条件。

    anchor_sql 通常是业务表里的 MAX(date)；这样 demo 数据的“今天”会落在数据自身最新日期，
    而不是机器当前日期。
    """
    if time_range.label == "today":
        start_expr = f"DATE({anchor_sql})"
        end_expr = f"DATE_ADD(DATE({anchor_sql}), INTERVAL 1 DAY)"
        return f"{column} >= {start_expr} AND {column} < {end_expr}"
    if time_range.offset_days:
        start_expr = f"DATE_SUB(DATE({anchor_sql}), INTERVAL {time_range.offset_days} DAY)"
        end_expr = f"DATE_SUB(DATE({anchor_sql}), INTERVAL {time_range.offset_days - 1} DAY)"
        return f"{column} >= {start_expr} AND {column} < {end_expr}"
    return f"{column} >= DATE_SUB({anchor_sql}, INTERVAL {time_range.days} DAY)"


def _required_data_scope(alias: str) -> str:
    """返回必须存在的 tenant/shop 过滤条件。

    与 core.db.current_data_scope_sql 不同，这里缺少上下文会直接抛异常，因为 deterministic workflow
    不允许在身份不可信时读取经营数据。
    """
    scope = current_data_scope_sql(alias)
    if not scope:
        raise RuntimeError("缺少可信 tenant/shop 上下文，无法执行经营数据 workflow")
    return scope


def _sql_literal(value: str) -> str:
    """生成只用于可信身份上下文的 SQL 字符串字面量，避免 gateway_shops 特殊主键场景误用通用 shop_id scope。"""
    return "'" + value.replace("'", "''") + "'"


def _campaign_time_filter(time_range: BusinessTimeRange) -> str:
    """活动表按活动起止时间粗过滤；没有日粒度统计日期时，用活动窗口和业务时间范围相交判断。"""
    if time_range.campaign_keyword:
        # 大促/618/双十一这类活动优先按活动名关键词过滤，比固定日期更贴近运营语义。
        return f"c.campaign_name LIKE '%{time_range.campaign_keyword}%'"
    anchor_sql = "(SELECT MAX(end_time) FROM campaigns)"
    if time_range.label == "today":
        start_expr = f"DATE({anchor_sql})"
        end_expr = f"DATE_ADD(DATE({anchor_sql}), INTERVAL 1 DAY)"
    elif time_range.offset_days:
        start_expr = f"DATE_SUB(DATE({anchor_sql}), INTERVAL {time_range.offset_days} DAY)"
        end_expr = f"DATE_SUB(DATE({anchor_sql}), INTERVAL {time_range.offset_days - 1} DAY)"
    else:
        start_expr = f"DATE_SUB({anchor_sql}, INTERVAL {time_range.days} DAY)"
        end_expr = anchor_sql
    return f"c.start_time <= {end_expr} AND c.end_time >= {start_expr}"