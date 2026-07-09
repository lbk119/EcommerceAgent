from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agent.platform.db import get_db_config  # noqa: E402
from mysql.connector import connect  # noqa: E402


BUSINESS_TABLES = [
    "agent_jobs",
    "ai_chat_messages",
    "ai_chat_conversations",
    "strategy_reviews",
    "business_reports",
    "data_import_jobs",
    "campaign_product_stats",
    "campaigns",
    "customer_service_tickets",
    "refunds",
    "traffic_stats",
    "reviews",
    "payments",
    "order_items",
    "orders",
    "inventory",
    "products",
    "sellers",
    "customers",
]

USER_TABLES = [
    "gateway_user_shops",
    "gateway_user_tenants",
    "gateway_users",
    "platform_integrations",
    "gateway_shops",
    "gateway_tenants",
]

DEV_DATABASE_MARKERS = ("demo", "dev", "test", "local")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Reset scoped EcommerceAgent dev data.")
    parser.add_argument("--tenant-id", default="tenant_demo")
    parser.add_argument("--shop-id", default="default_shop")
    parser.add_argument("--reset-users", action="store_true")
    parser.add_argument("--allow-non-dev-database", action="store_true")
    return parser.parse_args()


def table_exists(cursor, table_name: str) -> bool:
    cursor.execute(
        """
        SELECT COUNT(*)
        FROM information_schema.tables
        WHERE table_schema = DATABASE() AND table_name = %s
        """,
        (table_name,),
    )
    return bool(cursor.fetchone()[0])


def column_exists(cursor, table_name: str, column_name: str) -> bool:
    cursor.execute(
        """
        SELECT COUNT(*)
        FROM information_schema.columns
        WHERE table_schema = DATABASE() AND table_name = %s AND column_name = %s
        """,
        (table_name, column_name),
    )
    return bool(cursor.fetchone()[0])


def delete_scoped(cursor, table_name: str, tenant_id: str, shop_id: str) -> int:
    if not table_exists(cursor, table_name):
        return 0
    has_tenant = column_exists(cursor, table_name, "tenant_id")
    has_shop = column_exists(cursor, table_name, "shop_id")
    if has_tenant and has_shop:
        cursor.execute(f"DELETE FROM `{table_name}` WHERE tenant_id=%s AND shop_id=%s", (tenant_id, shop_id))
        return int(cursor.rowcount)
    if has_tenant:
        cursor.execute(f"DELETE FROM `{table_name}` WHERE tenant_id=%s", (tenant_id,))
        return int(cursor.rowcount)
    return 0


def delete_user_scope(cursor, table_name: str, tenant_id: str, shop_id: str) -> int:
    if not table_exists(cursor, table_name):
        return 0
    if table_name == "gateway_users" and column_exists(cursor, table_name, "default_tenant_id"):
        cursor.execute("DELETE FROM gateway_users WHERE default_tenant_id=%s", (tenant_id,))
        return int(cursor.rowcount)
    if table_name == "gateway_tenants" and column_exists(cursor, table_name, "id"):
        cursor.execute("DELETE FROM gateway_tenants WHERE id=%s", (tenant_id,))
        return int(cursor.rowcount)
    if table_name == "gateway_shops" and column_exists(cursor, table_name, "tenant_id") and column_exists(cursor, table_name, "id"):
        cursor.execute("DELETE FROM gateway_shops WHERE tenant_id=%s AND id=%s", (tenant_id, shop_id))
        return int(cursor.rowcount)
    return delete_scoped(cursor, table_name, tenant_id, shop_id)


def main() -> int:
    args = parse_args()
    config = get_db_config()
    database = str(config.get("database") or "")
    if not args.allow_non_dev_database and not any(marker in database.lower() for marker in DEV_DATABASE_MARKERS):
        raise RuntimeError(
            "Refusing to reset database without a dev marker in MYSQL_DATABASE. "
            "Use --allow-non-dev-database only for disposable local databases."
        )

    deleted: dict[str, int] = {}
    with connect(**config) as conn:
        cursor = conn.cursor()
        cursor.execute("SET FOREIGN_KEY_CHECKS=0")
        for table_name in BUSINESS_TABLES:
            deleted[table_name] = delete_scoped(cursor, table_name, args.tenant_id, args.shop_id)
        if args.reset_users:
            for table_name in USER_TABLES:
                deleted[table_name] = delete_user_scope(cursor, table_name, args.tenant_id, args.shop_id)
        else:
            if table_exists(cursor, "gateway_shops"):
                cursor.execute(
                    """
                    UPDATE gateway_shops
                    SET data_status='empty', last_sync_at=NULL, updated_at=NOW()
                    WHERE tenant_id=%s AND id=%s
                    """,
                    (args.tenant_id, args.shop_id),
                )
                deleted["gateway_shops_reset"] = int(cursor.rowcount)
        cursor.execute("SET FOREIGN_KEY_CHECKS=1")
        conn.commit()

    print(json.dumps({
        "status": "ok",
        "database": database,
        "host": config.get("host"),
        "tenantId": args.tenant_id,
        "shopId": args.shop_id,
        "resetUsers": bool(args.reset_users),
        "deleted": deleted,
    }, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
