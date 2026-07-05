import os

from dotenv import load_dotenv
from mysql.connector import connect

from seed_ecommerce_demo import PROJECT_ROOT, db_config


TABLES = [
    "customers",
    "sellers",
    "products",
    "orders",
    "order_items",
    "payments",
    "reviews",
    "inventory",
    "traffic_stats",
    "campaigns",
    "campaign_product_stats",
    "refunds",
    "customer_service_tickets",
]


def main() -> None:
    load_dotenv(PROJECT_ROOT / ".env")
    target_database = os.getenv("MYSQL_DATABASE", "ecommerce_demo")
    with connect(**db_config(target_database)) as conn:
        with conn.cursor() as cursor:
            for table in TABLES:
                ensure_column(cursor, target_database, table, "tenant_id", "VARCHAR(64) NOT NULL DEFAULT 'tenant_demo'", "FIRST")
                ensure_column(cursor, target_database, table, "shop_id", "VARCHAR(64) NOT NULL DEFAULT 'default_shop'", "AFTER tenant_id")
        conn.commit()
    print("tenant/shop migration applied")


def ensure_column(cursor, database: str, table: str, column: str, definition: str, position: str) -> None:
    cursor.execute(
        """
        SELECT COUNT(*)
        FROM information_schema.columns
        WHERE table_schema = %s AND table_name = %s AND column_name = %s
        """,
        (database, table, column),
    )
    if cursor.fetchone()[0] > 0:
        return
    cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition} {position}")


if __name__ == "__main__":
    main()
