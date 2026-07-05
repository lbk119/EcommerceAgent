import argparse
import csv
import hashlib
import os
import random
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Iterable, List, Sequence, Tuple

from dotenv import load_dotenv
from mysql.connector import connect

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OLIST_DIR = PROJECT_ROOT / "data" / "olist"
DEFAULT_SCHEMA = Path(__file__).with_name("schema.sql")
BATCH_SIZE = 1000


def parse_args():
    parser = argparse.ArgumentParser(description="Create ecommerce_demo MySQL database from Olist CSV files.")
    parser.add_argument("--olist-dir", default=str(DEFAULT_OLIST_DIR), help="Directory containing Olist CSV files.")
    parser.add_argument("--schema", default=str(DEFAULT_SCHEMA), help="Path to schema.sql.")
    parser.add_argument("--database", default="ecommerce_demo", help="Target MySQL database name.")
    parser.add_argument("--reset", action="store_true", help="Drop and recreate demo tables before import.")
    parser.add_argument("--limit", type=int, default=0, help="Optional row limit per Olist CSV for faster smoke tests.")
    parser.add_argument("--seed", type=int, default=20260702, help="Random seed for generated operation data.")
    return parser.parse_args()


def db_config(database: str | None = None):
    load_dotenv(PROJECT_ROOT / ".env")
    config = {
        "host": os.getenv("MYSQL_HOST", "localhost"),
        "port": int(os.getenv("MYSQL_PORT", "3306")),
        "user": os.getenv("MYSQL_USER"),
        "password": os.getenv("MYSQL_PASSWORD"),
        "charset": os.getenv("MYSQL_CHARSET", "utf8mb4"),
        "collation": os.getenv("MYSQL_COLLATION", "utf8mb4_unicode_ci"),
        "autocommit": False,
    }
    if database:
        config["database"] = database
    missing = [key for key in ["user", "password"] if not config.get(key)]
    if missing:
        raise RuntimeError(f"Missing MySQL settings in .env: {', '.join(missing)}")
    return config


def read_csv(path: Path, limit: int = 0):
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        for index, row in enumerate(reader):
            if limit and index >= limit:
                break
            yield {key: empty_to_none(value) for key, value in row.items()}


def empty_to_none(value):
    if value is None:
        return None
    value = value.strip()
    return value if value != "" else None


def to_int(value):
    return int(float(value)) if value not in (None, "") else None


def to_decimal(value):
    return Decimal(value) if value not in (None, "") else None


def to_datetime(value):
    if not value:
        return None
    return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")


def execute_schema(schema_path: Path, database: str):
    sql = schema_path.read_text(encoding="utf-8").replace("ecommerce_demo", database)
    statements = [statement.strip() for statement in sql.split(";") if statement.strip()]
    with connect(**db_config()) as conn:
        with conn.cursor() as cursor:
            for statement in statements:
                cursor.execute(statement)
        conn.commit()


def insert_many(cursor, sql: str, rows: Sequence[Tuple]):
    if not rows:
        return
    for start in range(0, len(rows), BATCH_SIZE):
        cursor.executemany(sql, rows[start:start + BATCH_SIZE])


def load_translation(olist_dir: Path, limit: int):
    translations = {}
    for row in read_csv(olist_dir / "product_category_name_translation.csv", limit):
        translations[row["product_category_name"]] = row["product_category_name_english"]
    return translations


def import_olist(olist_dir: Path, database: str, limit: int):
    translations = load_translation(olist_dir, 0)
    imported_order_ids = set()
    with connect(**db_config(database)) as conn:
        with conn.cursor() as cursor:
            print("Import customers...")
            rows = [(
                row["customer_id"], row["customer_unique_id"], to_int(row["customer_zip_code_prefix"]),
                row["customer_city"], row["customer_state"]
            ) for row in read_csv(olist_dir / "olist_customers_dataset.csv", 0)]
            insert_many(cursor, """
                INSERT INTO customers (customer_id, customer_unique_id, customer_zip_code_prefix, customer_city, customer_state)
                VALUES (%s, %s, %s, %s, %s)
            """, rows)

            print("Import sellers...")
            rows = [(
                row["seller_id"], to_int(row["seller_zip_code_prefix"]), row["seller_city"], row["seller_state"]
            ) for row in read_csv(olist_dir / "olist_sellers_dataset.csv", 0)]
            insert_many(cursor, """
                INSERT INTO sellers (seller_id, seller_zip_code_prefix, seller_city, seller_state)
                VALUES (%s, %s, %s, %s)
            """, rows)

            print("Import products...")
            rows = []
            for row in read_csv(olist_dir / "olist_products_dataset.csv", 0):
                category = row["product_category_name"]
                rows.append((
                    row["product_id"], category, translations.get(category), to_int(row["product_name_lenght"]),
                    to_int(row["product_description_lenght"]), to_int(row["product_photos_qty"]),
                    to_int(row["product_weight_g"]), to_int(row["product_length_cm"]),
                    to_int(row["product_height_cm"]), to_int(row["product_width_cm"])
                ))
            insert_many(cursor, """
                INSERT INTO products (
                    product_id, category_name, category_name_en, product_name_length, product_description_length,
                    product_photos_qty, product_weight_g, product_length_cm, product_height_cm, product_width_cm
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, rows)

            print("Import orders...")
            rows = []
            for row in read_csv(olist_dir / "olist_orders_dataset.csv", limit):
                imported_order_ids.add(row["order_id"])
                rows.append((
                    row["order_id"], row["customer_id"], row["order_status"], to_datetime(row["order_purchase_timestamp"]),
                    to_datetime(row["order_approved_at"]), to_datetime(row["order_delivered_carrier_date"]),
                    to_datetime(row["order_delivered_customer_date"]), to_datetime(row["order_estimated_delivery_date"])
                ))
            insert_many(cursor, """
                INSERT INTO orders (
                    order_id, customer_id, order_status, order_purchase_timestamp, order_approved_at,
                    order_delivered_carrier_date, order_delivered_customer_date, order_estimated_delivery_date
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """, rows)

            print("Import order items...")
            rows = [(
                row["order_id"], to_int(row["order_item_id"]), row["product_id"], row["seller_id"],
                to_datetime(row["shipping_limit_date"]), to_decimal(row["price"]), to_decimal(row["freight_value"])
            ) for row in read_csv(olist_dir / "olist_order_items_dataset.csv", 0) if row["order_id"] in imported_order_ids]
            insert_many(cursor, """
                INSERT INTO order_items (order_id, order_item_id, product_id, seller_id, shipping_limit_date, price, freight_value)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, rows)

            print("Import payments...")
            rows = [(
                row["order_id"], to_int(row["payment_sequential"]), row["payment_type"],
                to_int(row["payment_installments"]), to_decimal(row["payment_value"])
            ) for row in read_csv(olist_dir / "olist_order_payments_dataset.csv", 0) if row["order_id"] in imported_order_ids]
            insert_many(cursor, """
                INSERT INTO payments (order_id, payment_sequential, payment_type, payment_installments, payment_value)
                VALUES (%s, %s, %s, %s, %s)
            """, rows)

            print("Import reviews...")
            rows = [(
                row["review_id"], row["order_id"], to_int(row["review_score"]), row["review_comment_title"],
                row["review_comment_message"], to_datetime(row["review_creation_date"]), to_datetime(row["review_answer_timestamp"])
            ) for row in read_csv(olist_dir / "olist_order_reviews_dataset.csv", 0) if row["order_id"] in imported_order_ids]
            insert_many(cursor, """
                INSERT IGNORE INTO reviews (
                    review_id, order_id, review_score, review_comment_title, review_comment_message,
                    review_creation_date, review_answer_timestamp
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, rows)
        conn.commit()


def stable_int(text: str, modulo: int):
    return int(hashlib.sha1(text.encode("utf-8")).hexdigest()[:8], 16) % modulo


def fetch_all(cursor, sql: str):
    cursor.execute(sql)
    return cursor.fetchall()


def generate_operation_data(database: str, seed: int):
    random.seed(seed)
    warehouses = ["华东仓", "华南仓", "华北仓", "西南仓"]
    refund_reasons = ["七天无理由", "商品破损", "描述不符", "物流延迟", "质量问题", "拍错规格"]
    ticket_issues = ["物流咨询", "退款进度", "商品参数", "发票问题", "优惠券使用", "售后政策", "尺码/规格咨询"]
    channels = ["天猫", "京东", "抖音", "小红书", "站内搜索", "直播间"]

    with connect(**db_config(database)) as conn:
        with conn.cursor() as cursor:
            product_sales_rows = fetch_all(cursor, """
                SELECT product_id, COUNT(*) AS sold_count, COALESCE(SUM(price), 0) AS revenue
                FROM order_items
                GROUP BY product_id
            """)
            product_sales = {product_id: sold_count for product_id, sold_count, _revenue in product_sales_rows}
            products = [row[0] for row in fetch_all(cursor, "SELECT product_id FROM products")]
            order_items = fetch_all(cursor, """
                SELECT oi.order_id, oi.product_id, oi.price, o.order_purchase_timestamp
                FROM order_items oi
                JOIN orders o ON o.order_id = oi.order_id
                WHERE o.order_purchase_timestamp IS NOT NULL
            """)
            max_date_row = fetch_all(cursor, "SELECT MAX(order_purchase_timestamp) FROM orders")
            anchor_date = max_date_row[0][0].date() if max_date_row and max_date_row[0][0] else datetime.now().date()

            print("Generate inventory...")
            rows = []
            for product_id in products:
                sold_count = int(product_sales.get(product_id, 0))
                safety_stock = 5 + stable_int(product_id, 45)
                stock = max(0, safety_stock + stable_int(product_id + "stock", 220) - sold_count // 3)
                rows.append((product_id, stock, safety_stock, warehouses[stable_int(product_id, len(warehouses))], datetime.now()))
            insert_many(cursor, """
                INSERT INTO inventory (product_id, stock, safety_stock, warehouse, updated_at)
                VALUES (%s, %s, %s, %s, %s)
            """, rows)

            print("Generate traffic stats...")
            top_products = [product_id for product_id, _ in Counter([row[1] for row in order_items]).most_common(500)]
            rows = []
            for day_offset in range(30):
                stat_date = anchor_date - timedelta(days=day_offset)
                for product_id in top_products:
                    base_orders = stable_int(product_id + str(day_offset), 12)
                    conversions = max(0, base_orders)
                    visitors = conversions * random.randint(8, 24) + random.randint(20, 260)
                    views = visitors * random.randint(1, 4)
                    add_to_cart = max(conversions, int(visitors * random.uniform(0.05, 0.22)))
                    favorites = int(visitors * random.uniform(0.02, 0.18))
                    rows.append((stat_date, product_id, views, visitors, add_to_cart, favorites, conversions))
            insert_many(cursor, """
                INSERT INTO traffic_stats (stat_date, product_id, views, visitors, add_to_cart, favorites, conversions)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, rows)

            print("Generate campaigns...")
            campaigns = []
            for index, name in enumerate(["618大促", "夏季上新", "会员复购日", "直播爆品专场", "清仓季", "平台满减周"], start=1):
                start_time = datetime.combine(anchor_date - timedelta(days=45 - index * 7), datetime.min.time())
                end_time = start_time + timedelta(days=random.randint(3, 8))
                campaign_id = f"camp_{index:03d}"
                campaigns.append((campaign_id, name, channels[index % len(channels)], start_time, end_time, Decimal(random.randint(8000, 60000)), "finished"))
            insert_many(cursor, """
                INSERT INTO campaigns (campaign_id, campaign_name, channel, start_time, end_time, budget, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, campaigns)

            rows = []
            campaign_ids = [row[0] for row in campaigns]
            campaign_products = top_products[:120]
            for campaign_id in campaign_ids:
                for product_id in random.sample(campaign_products, min(30, len(campaign_products))):
                    impressions = random.randint(2000, 90000)
                    clicks = int(impressions * random.uniform(0.015, 0.12))
                    orders_count = int(clicks * random.uniform(0.02, 0.16))
                    avg_price = Decimal(stable_int(product_id + "price", 20000) / 100 + 20).quantize(Decimal("0.01"))
                    revenue = (avg_price * orders_count).quantize(Decimal("0.01"))
                    spend = Decimal(random.randint(300, 8000)).quantize(Decimal("0.01"))
                    rows.append((campaign_id, product_id, impressions, clicks, orders_count, revenue, spend))
            insert_many(cursor, """
                INSERT INTO campaign_product_stats (campaign_id, product_id, impressions, clicks, orders_count, revenue, spend)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, rows)

            print("Generate refunds...")
            rows = []
            refundable_items = [row for row in order_items if row[3] is not None]
            sample_size = min(3500, max(1, len(refundable_items) // 35))
            for index, (order_id, product_id, price, purchase_time) in enumerate(random.sample(refundable_items, sample_size), start=1):
                refund_time = purchase_time + timedelta(days=random.randint(1, 20))
                refund_amount = (Decimal(price) * Decimal(random.uniform(0.35, 1.0))).quantize(Decimal("0.01"))
                rows.append((f"ref_{index:06d}", order_id, product_id, refund_time, refund_amount, random.choice(refund_reasons), random.choice(["approved", "processing", "rejected"])))
            insert_many(cursor, """
                INSERT INTO refunds (refund_id, order_id, product_id, refund_time, refund_amount, refund_reason, refund_status)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, rows)

            print("Generate customer service tickets...")
            rows = []
            ticket_items = random.sample(refundable_items, min(5000, len(refundable_items)))
            for index, (order_id, product_id, _price, purchase_time) in enumerate(ticket_items, start=1):
                issue_type = random.choice(ticket_issues)
                ticket_time = purchase_time + timedelta(days=random.randint(0, 15), hours=random.randint(0, 23))
                rows.append((
                    f"ticket_{index:06d}", order_id, product_id, ticket_time, issue_type,
                    random.choice(["在线客服", "电话", "平台工单", "店铺私信"]),
                    random.choice(["positive", "neutral", "negative"]),
                    random.choice(["open", "resolved", "pending"]),
                    f"用户咨询{issue_type}，需要结合订单与商品信息处理。"
                ))
            insert_many(cursor, """
                INSERT INTO customer_service_tickets (
                    ticket_id, order_id, product_id, ticket_time, issue_type, channel, sentiment, status, summary
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, rows)
        conn.commit()


def print_summary(database: str):
    tables = [
        "customers", "sellers", "products", "orders", "order_items", "payments", "reviews",
        "inventory", "traffic_stats", "campaigns", "campaign_product_stats", "refunds", "customer_service_tickets"
    ]
    with connect(**db_config(database)) as conn:
        with conn.cursor() as cursor:
            print("\nImport summary:")
            for table in tables:
                cursor.execute(f"SELECT COUNT(*) FROM {table}")
                print(f"  {table}: {cursor.fetchone()[0]}")


def main():
    args = parse_args()
    olist_dir = Path(args.olist_dir)
    schema_path = Path(args.schema)
    if not olist_dir.exists():
        raise FileNotFoundError(f"Olist directory not found: {olist_dir}")
    if not schema_path.exists():
        raise FileNotFoundError(f"Schema not found: {schema_path}")

    if args.reset:
        print(f"Apply schema to database `{args.database}`...")
        execute_schema(schema_path, args.database)
    else:
        print("Skip schema reset. Use --reset to recreate tables.")

    import_olist(olist_dir, args.database, args.limit)
    generate_operation_data(args.database, args.seed)
    print_summary(args.database)


if __name__ == "__main__":
    main()
