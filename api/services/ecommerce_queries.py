"""电商 SaaS 工作台查询服务。

本文件集中处理前端工作台、商品、库存、活动、店铺、授权等读取/状态流转逻辑。所有函数
都接收 Gateway 注入的 tenant_id/shop_id，不信任前端 body 里的租户字段。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from api.db import execute, execute_many, fetch_all, fetch_one, ensure_platform_schema, mysql_conn


PLATFORMS = ["淘宝 / 天猫", "京东", "拼多多", "抖音电商", "快手电商", "小红书", "Shopify"]

AGENT_DEFINITIONS = [
    {"id": "store-analyst", "name": "店铺经营分析员", "role": "经营数据巡检与日报生成", "responsibilities": ["每日经营数据分析", "异常波动识别", "经营日报生成", "GMV / 订单 / 转化 / 客单价分析"]},
    {"id": "product-assistant", "name": "商品运营助理", "role": "商品分层、优化建议与机会识别", "responsibilities": ["商品表现分析", "爆品 / 潜力品 / 滞销品识别", "标题、价格、主图、库存建议"]},
    {"id": "inventory-inspector", "name": "库存风险巡检员", "role": "库存不足、滞销与备货风险预警", "responsibilities": ["库存不足预警", "滞销库存识别", "活动备货风险分析", "周转天数分析"]},
    {"id": "campaign-reviewer", "name": "活动复盘专员", "role": "活动 ROI、投放效果与复盘沉淀", "responsibilities": ["活动前后数据对比", "ROI 分析", "投放效果分析", "活动复盘报告生成"]},
    {"id": "report-specialist", "name": "知识与报告专员", "role": "报告中心、知识库与历史策略维护", "responsibilities": ["汇总经营报告", "维护运营知识库", "生成周报、月报、复盘文档", "沉淀历史策略"]},
]


def normalize_time(value: Any) -> str:
    """把 MySQL datetime/date 安全转换为前端可直接展示的字符串。"""
    if not value:
        return "未同步"
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M")
    return str(value)


def ensure_shop_seed(tenant_id: str, shop_id: str | None) -> str:
    """确保当前租户至少有一个店铺，返回可用 shop_id。

    兼容“刚注册但还没 onboarding”的用户：此时 JWT 可能没有 default_shop_id，Brain 仍要能
    返回空工作台或创建 onboarding 店铺。
    """
    ensure_platform_schema()
    if shop_id:
        row = fetch_one("SELECT id FROM gateway_shops WHERE tenant_id=%s AND id=%s", (tenant_id, shop_id))
        if row:
            return shop_id
    row = fetch_one("SELECT id FROM gateway_shops WHERE tenant_id=%s AND status <> 'deleted' ORDER BY created_at LIMIT 1", (tenant_id,))
    if row:
        return row["id"]
    fallback_shop_id = "default_shop"
    execute(
        """
        INSERT INTO gateway_tenants (id, name, status) VALUES (%s, %s, 'active')
        ON DUPLICATE KEY UPDATE name=VALUES(name)
        """,
        (tenant_id, tenant_id),
    )
    execute(
        """
        INSERT INTO gateway_shops (tenant_id, id, name, category, platform, shop_type, business_stage, status, auth_status, data_status, last_sync_at)
        VALUES (%s, %s, '示例旗舰店', '服饰鞋包', '淘宝 / 天猫', '品牌自营', '成长期', 'active', 'authorized', 'sample', NOW())
        ON DUPLICATE KEY UPDATE updated_at=NOW()
        """,
        (tenant_id, fallback_shop_id),
    )
    return fallback_shop_id


def list_shops(tenant_id: str) -> list[dict[str, Any]]:
    ensure_platform_schema()
    rows = fetch_all(
        """
        SELECT id, name, category, platform, status, shop_type, business_stage, auth_status, data_status, last_sync_at
        FROM gateway_shops
        WHERE tenant_id=%s AND status <> 'deleted'
        ORDER BY created_at DESC
        """,
        (tenant_id,),
    )
    unique_rows = []
    seen_names = set()
    for row in rows:
        dedupe_key = (row.get("name") or row["id"]).strip().lower()
        if dedupe_key in seen_names:
            continue
        seen_names.add(dedupe_key)
        unique_rows.append(row)
    return [
        {
            "id": row["id"],
            "name": row["name"],
            "category": row.get("category") or "未设置",
            "platform": row.get("platform") or "淘宝 / 天猫",
            "status": row.get("status") or "active",
            "type": row.get("shop_type") or "品牌自营",
            "businessStage": row.get("business_stage") or "成长期",
            "authStatus": row.get("auth_status") or "pending",
            "importStatus": row.get("data_status") or "empty",
            "lastSyncAt": normalize_time(row.get("last_sync_at")),
        }
        for row in unique_rows
    ]


def create_shop(tenant_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """创建店铺元数据；经营数据不会被这里写入或删除。"""
    ensure_platform_schema()
    shop_name = payload.get("name") or "未命名店铺"
    if payload.get("reuseByName") and not payload.get("id"):
        existing = fetch_one(
            """
            SELECT id
            FROM gateway_shops
            WHERE tenant_id=%s AND name=%s AND status <> 'deleted'
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (tenant_id, shop_name),
        )
        if existing:
            payload = {**payload, "id": existing["id"]}
    shop_id = payload.get("id") or str(uuid.uuid4())
    execute(
        """
        INSERT INTO gateway_shops (tenant_id, id, name, category, platform, shop_type, business_stage, status, auth_status, data_status, last_sync_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, 'active', 'pending', 'empty', NULL)
        ON DUPLICATE KEY UPDATE name=VALUES(name), category=VALUES(category), platform=VALUES(platform), shop_type=VALUES(shop_type), business_stage=VALUES(business_stage), status='active'
        """,
        (tenant_id, shop_id, shop_name, payload.get("category") or "未设置", payload.get("platform") or "淘宝 / 天猫", payload.get("type") or payload.get("shopType") or "品牌自营", payload.get("businessStage") or "成长期"),
    )
    return get_shop(tenant_id, shop_id)


def get_shop(tenant_id: str, shop_id: str) -> dict[str, Any]:
    shops = list_shops(tenant_id)
    return next((shop for shop in shops if shop["id"] == shop_id), {})


def update_shop(tenant_id: str, shop_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    execute(
        """
        UPDATE gateway_shops
        SET name=%s, category=%s, platform=%s, shop_type=%s, business_stage=%s, updated_at=NOW()
        WHERE tenant_id=%s AND id=%s
        """,
        (payload.get("name"), payload.get("category"), payload.get("platform"), payload.get("type") or payload.get("shopType"), payload.get("businessStage"), tenant_id, shop_id),
    )
    return get_shop(tenant_id, shop_id)


def soft_delete_shop(tenant_id: str, shop_id: str) -> dict[str, Any]:
    row = fetch_one("SELECT name FROM gateway_shops WHERE tenant_id=%s AND id=%s", (tenant_id, shop_id))
    if row and row.get("name"):
        execute("UPDATE gateway_shops SET status='deleted', updated_at=NOW() WHERE tenant_id=%s AND name=%s", (tenant_id, row["name"]))
    else:
        execute("UPDATE gateway_shops SET status='deleted', updated_at=NOW() WHERE tenant_id=%s AND id=%s", (tenant_id, shop_id))
    return {"deleted": True, "shopId": shop_id}


def ensure_integrations(tenant_id: str, shop_id: str, selected_platforms: list[str] | None = None) -> list[dict[str, Any]]:
    """确保当前店铺拥有所有支持平台的授权记录。"""
    ensure_platform_schema()
    existing_rows = fetch_all(
        """
        SELECT id, platform, status, last_sync_at, error_message
        FROM platform_integrations
        WHERE tenant_id=%s AND shop_id=%s
        """,
        (tenant_id, shop_id),
    )
    if selected_platforms is None:
        return _format_integrations(existing_rows)

    selected = set(selected_platforms or [])
    rows = []
    for platform in selected:
        integration_id = _integration_id(tenant_id, shop_id, platform)
        rows.append((integration_id, tenant_id, shop_id, platform, "unauthorized"))
    execute_many(
        """
        INSERT INTO platform_integrations (id, tenant_id, shop_id, platform, status)
        VALUES (%s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE platform = VALUES(platform)
        """,
        rows,
    )
    return list_integrations(tenant_id, shop_id)


def list_integrations(tenant_id: str, shop_id: str) -> list[dict[str, Any]]:
    ensure_platform_schema()
    ensure_shop_seed(tenant_id, shop_id)
    rows = fetch_all(
        """
        SELECT id, platform, status, last_sync_at, error_message
        FROM platform_integrations
        WHERE tenant_id=%s AND shop_id=%s
        ORDER BY FIELD(platform, '淘宝 / 天猫','京东','拼多多','抖音电商','快手电商','小红书','Shopify'), platform
        """,
        (tenant_id, shop_id),
    )
    return _format_integrations(rows)


def _format_integrations(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把授权表行转换成前端 Integration 类型。"""
    order = {platform: index for index, platform in enumerate(PLATFORMS)}
    sorted_rows = sorted(rows, key=lambda row: (order.get(row["platform"], len(order)), row["platform"]))
    return [{"id": row["id"], "platform": row["platform"], "status": row["status"], "lastSyncAt": normalize_time(row.get("last_sync_at")), "errorMessage": row.get("error_message")} for row in sorted_rows]


def set_integration_status(tenant_id: str, shop_id: str, platform: str, status: str) -> dict[str, Any]:
    error_message = "订单接口同步失败，请稍后重试" if status == "failed" else None
    execute(
        """
        INSERT INTO platform_integrations (id, tenant_id, shop_id, platform, status, last_sync_at, error_message)
        VALUES (%s, %s, %s, %s, %s, CASE WHEN %s IN ('authorized','syncing') THEN NOW() ELSE NULL END, %s)
        ON DUPLICATE KEY UPDATE status=VALUES(status), last_sync_at=VALUES(last_sync_at), error_message=VALUES(error_message)
        """,
        (_integration_id(tenant_id, shop_id, platform), tenant_id, shop_id, platform, status, status, error_message),
    )
    return next(item for item in list_integrations(tenant_id, shop_id) if item["platform"] == platform)


def _integration_id(tenant_id: str, shop_id: str, platform: str) -> str:
    """生成稳定且不超过 64 位的授权记录 ID。"""
    return f"itg_{uuid.uuid5(uuid.NAMESPACE_URL, f'{tenant_id}:{shop_id}:{platform}').hex}"


def get_metrics(tenant_id: str, shop_id: str) -> dict[str, Any]:
    """聚合核心经营指标；没有经营数据时返回 0 值而不是抛 500。"""
    # 多个指标共用一个 MySQL 连接，避免登录后工作区预加载反复握手导致按钮长时间无反馈。
    with mysql_conn(dictionary=True) as (_, cursor):
        cursor.execute(
            """
            SELECT
              COALESCE(ROUND(SUM(oi.price), 2), 0) AS gmv,
              COUNT(DISTINCT o.order_id) AS orders,
              COALESCE(ROUND(SUM(oi.price) / NULLIF(COUNT(DISTINCT o.order_id), 0), 2), 0) AS average_order_value
            FROM orders o
            LEFT JOIN order_items oi ON oi.order_id=o.order_id AND oi.tenant_id=o.tenant_id AND oi.shop_id=o.shop_id
            WHERE o.tenant_id=%s AND o.shop_id=%s
            """,
            (tenant_id, shop_id),
        )
        row = cursor.fetchone() or {}
        cursor.execute("SELECT COALESCE(SUM(visitors),0) AS visitors, COALESCE(SUM(conversions),0) AS conversions FROM traffic_stats WHERE tenant_id=%s AND shop_id=%s", (tenant_id, shop_id))
        visitors = cursor.fetchone() or {}
        cursor.execute("SELECT COUNT(*) AS refunds FROM refunds WHERE tenant_id=%s AND shop_id=%s", (tenant_id, shop_id))
        refunds = cursor.fetchone() or {}
        cursor.execute("SELECT COUNT(*) AS risk_count FROM inventory WHERE tenant_id=%s AND shop_id=%s AND stock <= safety_stock", (tenant_id, shop_id))
        inventory = cursor.fetchone() or {}
        cursor.execute(
            """
            SELECT COUNT(DISTINCT cps.product_id) AS count
            FROM campaigns c
            JOIN campaign_product_stats cps ON cps.campaign_id=c.campaign_id AND cps.tenant_id=c.tenant_id AND cps.shop_id=c.shop_id
            WHERE c.tenant_id=%s AND c.shop_id=%s AND c.status IN ('active','running','进行中')
            """,
            (tenant_id, shop_id),
        )
        active_products = cursor.fetchone() or {}
    orders = int(row.get("orders") or 0)
    visitor_count = int(visitors.get("visitors") or 0)
    conversions = int(visitors.get("conversions") or 0)
    return {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "gmv": float(row.get("gmv") or 0),
        "orders": orders,
        "conversionRate": round(conversions / visitor_count * 100, 2) if visitor_count else 0,
        "averageOrderValue": float(row.get("average_order_value") or 0),
        "refundRate": round(int(refunds.get("refunds") or 0) / orders * 100, 2) if orders else 0,
        "visitors": visitor_count,
        "inventoryRiskSkuCount": int(inventory.get("risk_count") or 0),
        "activeCampaignProducts": int(active_products.get("count") or 0),
        "aiCompletedTasks": int((fetch_one("SELECT COUNT(*) AS count FROM agent_jobs WHERE tenant_id=%s AND shop_id=%s AND status='completed'", (tenant_id, shop_id)) or {}).get("count") or 0),
    }


def list_products(tenant_id: str, shop_id: str, filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    filters = filters or {}
    rows = fetch_all(
        """
        SELECT
          p.product_id AS id,
          COALESCE(p.category_name, p.product_id) AS product_name,
          COALESCE(p.category_name_en, '未分类') AS category,
          COALESCE(i.stock, 0) AS stock,
          COALESCE(i.safety_stock, 0) AS safety_stock,
          COALESCE(sales.sales_amount, 0) AS sales_amount,
          COALESCE(sales.sales, 0) AS sales,
          COALESCE(sales.price, 0) AS price,
          COALESCE(traffic.visitors, 0) AS visitors,
          COALESCE(traffic.conversions, 0) AS conversions,
          COALESCE(traffic.conversions / NULLIF(traffic.visitors, 0) * 100, 0) AS conversion_rate
        FROM products p
        LEFT JOIN inventory i ON i.product_id=p.product_id AND i.tenant_id=p.tenant_id AND i.shop_id=p.shop_id
        LEFT JOIN (
          SELECT tenant_id, shop_id, product_id, SUM(price) AS sales_amount, COUNT(*) AS sales, AVG(price) AS price
          FROM order_items
          WHERE tenant_id=%s AND shop_id=%s
          GROUP BY tenant_id, shop_id, product_id
        ) sales ON sales.product_id=p.product_id AND sales.tenant_id=p.tenant_id AND sales.shop_id=p.shop_id
        LEFT JOIN (
          SELECT tenant_id, shop_id, product_id, SUM(visitors) AS visitors, SUM(conversions) AS conversions
          FROM traffic_stats
          WHERE tenant_id=%s AND shop_id=%s
          GROUP BY tenant_id, shop_id, product_id
        ) traffic ON traffic.product_id=p.product_id AND traffic.tenant_id=p.tenant_id AND traffic.shop_id=p.shop_id
        WHERE p.tenant_id=%s AND p.shop_id=%s
        ORDER BY sales_amount DESC, sales DESC
        LIMIT 100
        """,
        (tenant_id, shop_id, tenant_id, shop_id, tenant_id, shop_id),
    )
    products = []
    positive_sales = [int(row.get("sales") or 0) for row in rows if int(row.get("sales") or 0) > 0]
    hot_threshold_index = max(0, int(len(positive_sales) * 0.2) - 1)
    hot_threshold = sorted(positive_sales, reverse=True)[hot_threshold_index] if positive_sales else 0
    for row in rows:
        stock = int(row.get("stock") or 0)
        safety_stock = int(row.get("safety_stock") or 0)
        sales = int(row.get("sales") or 0)
        risk_level = "high" if stock <= safety_stock else "medium" if sales == 0 and stock > safety_stock else "low"
        visitors = int(row.get("visitors") or 0)
        conversions = int(row.get("conversions") or 0)
        if hot_threshold and sales >= hot_threshold:
            layer = "爆品"
        elif visitors > 0 and conversions > 0 and sales > 0:
            layer = "潜力品"
        elif stock > safety_stock * 2 and sales <= 1:
            layer = "滞销品"
        else:
            layer = "稳定品"
        risk_reason = "库存不足" if stock <= safety_stock else "周转过慢" if layer == "滞销品" else "稳定增长"
        products.append({
            "id": row["id"],
            "name": row.get("product_name") or row["id"],
            "sku": row["id"],
            "category": row["category"],
            "price": float(row.get("price") or 0),
            "stock": stock,
            "sales": sales,
            "conversionRate": round(float(row.get("conversion_rate") or 0), 2),
            "riskLevel": risk_level,
            "layer": layer,
            "riskReason": risk_reason,
            "aiSuggestion": _product_suggestion(risk_level, layer),
        })
    keyword = (filters.get("keyword") or "").lower()
    if keyword:
        products = [item for item in products if keyword in item["name"].lower() or keyword in item["sku"].lower()]
    if filters.get("riskLevel"):
        products = [item for item in products if item["riskLevel"] == filters["riskLevel"]]
    return products


def list_inventory_risks(tenant_id: str, shop_id: str) -> list[dict[str, Any]]:
    rows = fetch_all(
        """
        SELECT p.product_id AS sku,
               COALESCE(p.category_name, p.product_id) AS product_name,
               COALESCE(i.stock, 0) AS stock,
               COALESCE(i.safety_stock, 0) AS safety_stock,
               COALESCE(sales.sales7d, 0) AS sales7d
        FROM inventory i
        JOIN products p ON p.product_id=i.product_id AND p.tenant_id=i.tenant_id AND p.shop_id=i.shop_id
        LEFT JOIN (
          SELECT oi.tenant_id, oi.shop_id, oi.product_id, COUNT(*) AS sales7d
          FROM order_items oi
          JOIN orders o ON o.order_id=oi.order_id AND o.tenant_id=oi.tenant_id AND o.shop_id=oi.shop_id
          JOIN (SELECT tenant_id, shop_id, MAX(order_purchase_timestamp) AS max_time FROM orders WHERE tenant_id=%s AND shop_id=%s GROUP BY tenant_id, shop_id) b
            ON b.tenant_id=o.tenant_id AND b.shop_id=o.shop_id
          WHERE oi.tenant_id=%s AND oi.shop_id=%s AND o.order_purchase_timestamp >= DATE_SUB(b.max_time, INTERVAL 7 DAY)
          GROUP BY oi.tenant_id, oi.shop_id, oi.product_id
        ) sales ON sales.product_id=i.product_id AND sales.tenant_id=i.tenant_id AND sales.shop_id=i.shop_id
        WHERE i.tenant_id=%s AND i.shop_id=%s
        ORDER BY (i.stock <= i.safety_stock) DESC, sales7d DESC, i.stock ASC
        LIMIT 100
        """,
        (tenant_id, shop_id, tenant_id, shop_id, tenant_id, shop_id),
    )
    risks = []
    for row in rows:
        stock = int(row.get("stock") or 0)
        safety_stock = int(row.get("safety_stock") or 0)
        sales7d = int(row.get("sales7d") or 0)
        turnover_days = round(stock / max(sales7d / 7, 1), 1) if stock else 0
        if stock <= safety_stock:
            risk_level = "high"
            risk_reason = "库存低于安全库存"
            suggested_action = "立即补货，并检查活动库存锁定量"
        elif sales7d == 0 and stock > safety_stock:
            risk_level = "medium"
            risk_reason = "近 7 天无销量，存在滞销风险"
            suggested_action = "降低补货优先级，复盘价格和流量入口"
        else:
            continue
        risks.append({"sku": row["sku"], "productName": row["product_name"], "stock": stock, "safetyStock": safety_stock, "sales7d": sales7d, "turnoverDays": turnover_days, "riskLevel": risk_level, "riskReason": risk_reason, "suggestedAction": suggested_action})
    return risks


def list_campaigns(tenant_id: str, shop_id: str) -> list[dict[str, Any]]:
    rows = fetch_all(
        """
        SELECT c.campaign_id AS id, c.campaign_name AS name,
               COALESCE(SUM(cps.revenue),0) AS gmv,
               COALESCE(SUM(cps.revenue)/NULLIF(SUM(cps.spend),0),0) AS roi,
               COALESCE(SUM(cps.orders_count),0) AS orders_count,
               COALESCE(SUM(cps.clicks),0) AS clicks,
               COALESCE(SUM(cps.spend),0) AS spend,
               COALESCE(SUM(CASE WHEN i.stock <= i.safety_stock THEN 1 ELSE 0 END),0) AS risk_skus,
               COALESCE(refunds.refund_count,0) AS refund_count
        FROM campaigns c
        LEFT JOIN campaign_product_stats cps ON cps.campaign_id=c.campaign_id AND cps.tenant_id=c.tenant_id AND cps.shop_id=c.shop_id
        LEFT JOIN inventory i ON i.product_id=cps.product_id AND i.tenant_id=cps.tenant_id AND i.shop_id=cps.shop_id
        LEFT JOIN (
          SELECT tenant_id, shop_id, COUNT(*) AS refund_count FROM refunds WHERE tenant_id=%s AND shop_id=%s GROUP BY tenant_id, shop_id
        ) refunds ON refunds.tenant_id=c.tenant_id AND refunds.shop_id=c.shop_id
        WHERE c.tenant_id=%s AND c.shop_id=%s
        GROUP BY c.campaign_id, c.campaign_name, refunds.refund_count
        ORDER BY gmv DESC
        LIMIT 50
        """,
        (tenant_id, shop_id, tenant_id, shop_id),
    )
    campaigns = []
    for row in rows:
        roi = round(float(row.get("roi") or 0), 2)
        gmv = float(row.get("gmv") or 0)
        clicks = int(row.get("clicks") or 0)
        orders_count = int(row.get("orders_count") or 0)
        conversion_change = round(orders_count / clicks * 100, 2) if clicks else 0
        risk_skus = int(row.get("risk_skus") or 0)
        refund_count = int(row.get("refund_count") or 0)
        score = max(35, min(95, 55 + int(roi * 10) + (8 if gmv > 0 else 0) - risk_skus * 5 - min(refund_count, 5)))
        campaigns.append({"id": row["id"], "name": row["name"], "score": score, "roi": roi, "gmv": gmv, "conversionChange": conversion_change, "conclusion": _campaign_conclusion(roi, gmv, risk_skus, refund_count)})
    return campaigns


def list_reports(tenant_id: str, shop_id: str) -> list[dict[str, Any]]:
    rows = fetch_all("SELECT id, type, title, summary, status, created_at FROM business_reports WHERE tenant_id=%s AND shop_id=%s ORDER BY created_at DESC LIMIT 50", (tenant_id, shop_id))
    return [{"id": row["id"], "type": row["type"], "title": row["title"], "summary": row["summary"], "createdAt": normalize_time(row["created_at"]), "status": row["status"]} for row in rows]


def list_strategies(tenant_id: str, shop_id: str) -> list[dict[str, Any]]:
    rows = fetch_all("SELECT id, title, source, expected_impact, risk_level, status, created_at FROM strategy_reviews WHERE tenant_id=%s AND shop_id=%s ORDER BY created_at DESC LIMIT 50", (tenant_id, shop_id))
    return [{"id": row["id"], "title": row["title"], "source": row["source"], "expectedImpact": row["expected_impact"], "riskLevel": row["risk_level"], "status": row["status"], "createdAt": normalize_time(row["created_at"])} for row in rows]


def seed_strategy(tenant_id: str, shop_id: str) -> None:
    execute(
        """
        INSERT INTO strategy_reviews (id, tenant_id, shop_id, title, source, expected_impact, risk_level, status)
        VALUES (%s, %s, %s, '提高高热 SKU 安全库存阈值', '库存风险巡检员', '预计减少断货损失并提升活动承接能力', 'medium', 'pending')
        """,
        (str(uuid.uuid4()), tenant_id, shop_id),
    )


def update_strategy_status(tenant_id: str, shop_id: str, strategy_id: str, status: str, reviewer_id: str) -> dict[str, Any]:
    affected = execute("UPDATE strategy_reviews SET status=%s, reviewed_by=%s, reviewed_at=NOW() WHERE tenant_id=%s AND shop_id=%s AND id=%s", (status, reviewer_id, tenant_id, shop_id, strategy_id))
    if affected == 0:
        raise LookupError("策略不存在")
    return {"id": strategy_id, "status": status}


def _product_suggestion(risk_level: str, layer: str) -> str:
    """根据商品风险和分层生成确定性运营建议。"""
    if risk_level == "high":
        return "建议优先补货，并核对活动库存锁定量，避免高转化流量无法承接。"
    if layer == "爆品":
        return "建议保持投放和库存水位，重点观察退款率与活动 ROI。"
    if layer == "潜力品":
        return "建议优化主图、价格和利益点，放大高转化流量入口。"
    if layer == "滞销品":
        return "建议控制补货，结合折扣、组合销售或下架策略清理库存。"
    return "建议维持日常监控，观察转化率和库存周转是否出现异常。"


def _campaign_conclusion(roi: float, gmv: float, risk_skus: int, refund_count: int) -> str:
    """根据活动 ROI、成交、库存和退款风险生成复盘结论。"""
    if risk_skus > 0:
        return "活动存在库存承接风险，建议先补齐安全库存再继续放量。"
    if refund_count >= 3:
        return "活动成交有效，但退款风险偏高，建议复盘商品质量、承诺时效和售后话术。"
    if roi >= 3 and gmv > 0:
        return "活动 ROI 表现良好，可保留核心商品和投放结构，逐步扩大预算。"
    if roi >= 1 and gmv > 0:
        return "活动有成交贡献但效率一般，建议优化人群、价格和素材后再放量。"
    return "活动成交或 ROI 偏弱，建议收敛预算并重新校准选品与投放目标。"


def list_import_jobs(tenant_id: str, shop_id: str) -> list[dict[str, Any]]:
    rows = fetch_all("SELECT id, source, file_name, rows_count, status, quality_score, created_at FROM data_import_jobs WHERE tenant_id=%s AND shop_id=%s ORDER BY created_at DESC LIMIT 50", (tenant_id, shop_id))
    return [{"id": row["id"], "source": row["source"], "fileName": row.get("file_name") or "", "rows": row.get("rows_count") or 0, "status": row["status"], "qualityScore": row.get("quality_score") or 0, "createdAt": normalize_time(row["created_at"])} for row in rows]


def list_agents(tenant_id: str, shop_id: str) -> list[dict[str, Any]]:
    reports = list_reports(tenant_id, shop_id)
    jobs = fetch_all("SELECT agent_id, title, status, created_at FROM agent_jobs WHERE tenant_id=%s AND shop_id=%s ORDER BY created_at DESC LIMIT 100", (tenant_id, shop_id))
    result = []
    for agent in AGENT_DEFINITIONS:
        related_jobs = [job for job in jobs if job["agent_id"] == agent["id"]]
        result.append({
            **agent,
            "status": "working" if any(job["status"] == "running" for job in related_jobs) else "idle",
            "tasks": [{"title": job["title"], "status": _job_status_cn(job["status"]), "due": normalize_time(job["created_at"])} for job in related_jobs[:3]] or [{"title": "等待下一次巡检", "status": "待执行", "due": "今天"}],
            "outputs": [{"title": report["title"], "type": report["type"], "createdAt": report["createdAt"]} for report in reports[:2]],
        })
    return result


def _job_status_cn(status: str) -> str:
    return {"pending": "待执行", "running": "进行中", "waiting_review": "待审核", "completed": "已完成", "failed": "异常"}.get(status, "待执行")


def workspace_bundle(tenant_id: str, shop_id: str | None) -> dict[str, Any]:
    actual_shop_id = ensure_shop_seed(tenant_id, shop_id)
    ensure_integrations(tenant_id, actual_shop_id)
    return {
        "currentShopId": actual_shop_id,
        "shops": list_shops(tenant_id),
        "integrations": list_integrations(tenant_id, actual_shop_id),
        "metrics": get_metrics(tenant_id, actual_shop_id),
        "products": list_products(tenant_id, actual_shop_id),
        "agents": list_agents(tenant_id, actual_shop_id),
        "reports": list_reports(tenant_id, actual_shop_id),
        "strategies": list_strategies(tenant_id, actual_shop_id),
        "campaigns": list_campaigns(tenant_id, actual_shop_id),
        "imports": list_import_jobs(tenant_id, actual_shop_id),
    }
