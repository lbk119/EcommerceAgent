from __future__ import annotations

import argparse
import csv
import random
from datetime import datetime, timedelta
from pathlib import Path


PRODUCTS = [
    {"sku": "SKU-ORANGE-001", "name": "\u8d63\u5357\u8110\u6a59\u793c\u76d2 5kg", "category": "fresh_orange", "price": 89.0, "stock": 42, "safety": 120, "roi": "high"},
    {"sku": "SKU-ORANGE-002", "name": "\u51b0\u7cd6\u6a59\u5bb6\u5ead\u88c5 9\u65a4", "category": "fresh_orange", "price": 59.0, "stock": 780, "safety": 180, "roi": "medium"},
    {"sku": "SKU-JUICE-003", "name": "NFC \u9c9c\u69a8\u6a59\u6c41 12\u74f6", "category": "juice", "price": 129.0, "stock": 188, "safety": 90, "roi": "high"},
    {"sku": "SKU-GIFT-004", "name": "\u4f01\u4e1a\u798f\u5229\u6a59\u793c\u76d2", "category": "gift_box", "price": 168.0, "stock": 24, "safety": 80, "roi": "high"},
    {"sku": "SKU-TRIAL-005", "name": "\u5c0f\u679c\u8bd5\u5403\u88c5 3\u65a4", "category": "fresh_orange", "price": 29.9, "stock": 1180, "safety": 150, "roi": "low"},
    {"sku": "SKU-JAM-006", "name": "\u624b\u5de5\u6a59\u76ae\u679c\u9171 2\u74f6", "category": "orange_jam", "price": 49.0, "stock": 310, "safety": 100, "roi": "low"},
    {"sku": "SKU-TEA-007", "name": "\u6a59\u9999\u51b7\u6ce1\u8336\u7ec4\u5408", "category": "tea", "price": 69.0, "stock": 96, "safety": 110, "roi": "medium"},
    {"sku": "SKU-DRIED-008", "name": "\u4f4e\u7cd6\u6a59\u7247\u96f6\u98df", "category": "snack", "price": 39.0, "stock": 520, "safety": 130, "roi": "medium"},
    {"sku": "SKU-BOX-009", "name": "\u4eb2\u5b50\u6a59\u5b50\u91c7\u6458\u76d2", "category": "gift_box", "price": 99.0, "stock": 64, "safety": 100, "roi": "high"},
    {"sku": "SKU-HONEY-010", "name": "\u8702\u871c\u6a59\u5b50\u8336\u996e", "category": "drink", "price": 79.0, "stock": 36, "safety": 95, "roi": "low"},
    {"sku": "SKU-POMELO-011", "name": "\u7ea2\u5fc3\u67da\u5b50\u6df7\u642d\u88c5", "category": "citrus_mix", "price": 75.0, "stock": 240, "safety": 120, "roi": "medium"},
    {"sku": "SKU-LEMON-012", "name": "\u9999\u6c34\u67e0\u6aac\u793c\u888b", "category": "citrus_mix", "price": 45.0, "stock": 460, "safety": 140, "roi": "low"},
    {"sku": "SKU-PREMIUM-013", "name": "\u7279\u7ea7\u679c\u56ed\u76f4\u53d1\u793c\u76d2", "category": "premium", "price": 198.0, "stock": 18, "safety": 70, "roi": "high"},
    {"sku": "SKU-BUNDLE-014", "name": "\u529e\u516c\u5ba4\u8865\u7ed9\u6df7\u5408\u7bb1", "category": "bundle", "price": 139.0, "stock": 150, "safety": 90, "roi": "medium"},
    {"sku": "SKU-FAMILY-015", "name": "\u5468\u672b\u5bb6\u5ead\u6c34\u679c\u7bb1", "category": "bundle", "price": 119.0, "stock": 88, "safety": 95, "roi": "medium"},
    {"sku": "SKU-VIP-016", "name": "VIP \u5ba2\u6237\u5b9a\u5236\u793c\u76d2", "category": "premium", "price": 258.0, "stock": 12, "safety": 60, "roi": "high"},
]

CAMPAIGNS = [
    ("Harvest Livestream", "Douyin", 1.1),
    ("Member Day Coupon", "Tmall", 0.8),
    ("Fresh Gift Bundle", "JD", 1.4),
    ("Search Ads", "Taobao", 0.6),
]

CITY_POOL = ["Hangzhou", "Shanghai", "Guangzhou", "Shenzhen", "Chengdu", "Wuhan", "Nanjing", "Beijing", "Suzhou", "Xiamen"]
REFUND_REASONS = ["damaged fruit", "late delivery", "wrong package", "customer changed mind"]
FIELDNAMES = [
    "order_id",
    "customer_id",
    "sku_code",
    "product_id",
    "product_name",
    "category",
    "order_time",
    "unit_price",
    "quantity",
    "pay_amount",
    "customer_city",
    "stock",
    "safety_stock",
    "visitors",
    "conversions",
    "campaign_name",
    "platform",
    "ad_spend",
    "refund_flag",
    "refund_amount",
    "refund_reason",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a clean 200-order ecommerce import CSV.")
    parser.add_argument("--output", default=str(Path("data") / "sample_import_200_orders.csv"))
    parser.add_argument("--rows", type=int, default=200)
    parser.add_argument("--seed", type=int, default=20250217)
    return parser.parse_args()


def weighted_product(index: int, rng: random.Random) -> dict[str, object]:
    if index % 10 in {1, 2, 3}:
        return rng.choice([PRODUCTS[0], PRODUCTS[2], PRODUCTS[3], PRODUCTS[12], PRODUCTS[15]])
    if index % 10 in {8, 9}:
        return rng.choice([PRODUCTS[4], PRODUCTS[5], PRODUCTS[9], PRODUCTS[11]])
    return rng.choice(PRODUCTS)


def metrics_for(product: dict[str, object], index: int, rng: random.Random) -> tuple[int, int, float]:
    roi = str(product["roi"])
    if roi == "high":
        visitors = rng.randint(90, 180)
        conversions = rng.randint(12, 28)
        spend = round(rng.uniform(18, 45), 2)
    elif roi == "low":
        visitors = rng.randint(120, 260)
        conversions = rng.randint(1, 7)
        spend = round(rng.uniform(55, 115), 2)
    else:
        visitors = rng.randint(80, 190)
        conversions = rng.randint(6, 18)
        spend = round(rng.uniform(28, 75), 2)
    if index % 17 == 0:
        conversions = max(1, conversions // 2)
    return visitors, conversions, spend


def build_rows(row_count: int, seed: int) -> list[dict[str, object]]:
    rng = random.Random(seed)
    base_time = datetime.now().replace(minute=0, second=0, microsecond=0)
    rows: list[dict[str, object]] = []
    refund_indexes = set(rng.sample(range(1, row_count + 1), max(1, int(row_count * 0.14))))
    for index in range(1, row_count + 1):
        product = weighted_product(index, rng)
        campaign_name, platform, campaign_factor = CAMPAIGNS[index % len(CAMPAIGNS)]
        quantity = 2 if index % 11 in {0, 3} else 1
        if str(product["roi"]) == "high" and index % 13 == 0:
            quantity += 1
        unit_price = float(product["price"])
        discount = 0.92 if index % 19 == 0 else 1.0
        pay_amount = round(unit_price * quantity * discount, 2)
        visitors, conversions, spend = metrics_for(product, index, rng)
        spend = round(spend * campaign_factor, 2)
        refund = index in refund_indexes
        order_time = base_time - timedelta(days=rng.randint(0, 29), hours=rng.randint(0, 23))
        rows.append({
            "order_id": f"FRESH-{index:04d}",
            "customer_id": f"CUST-{(index % 96) + 1:03d}",
            "sku_code": product["sku"],
            "product_id": product["sku"],
            "product_name": product["name"],
            "category": product["category"],
            "order_time": order_time.strftime("%Y-%m-%d %H:%M:%S"),
            "unit_price": f"{unit_price:.2f}",
            "quantity": quantity,
            "pay_amount": f"{pay_amount:.2f}",
            "customer_city": rng.choice(CITY_POOL),
            "stock": product["stock"],
            "safety_stock": product["safety"],
            "visitors": visitors,
            "conversions": conversions,
            "campaign_name": campaign_name,
            "platform": platform,
            "ad_spend": f"{spend:.2f}",
            "refund_flag": "Y" if refund else "N",
            "refund_amount": f"{round(pay_amount * rng.uniform(0.35, 0.9), 2):.2f}" if refund else "0.00",
            "refund_reason": rng.choice(REFUND_REASONS) if refund else "",
        })
    return rows


def main() -> int:
    args = parse_args()
    output = Path(args.output)
    if not output.is_absolute():
        output = Path.cwd() / output
    output.parent.mkdir(parents=True, exist_ok=True)
    rows = build_rows(args.rows, args.seed)
    with output.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)
    print(str(output))
    print(f"rows={len(rows)}")
    print(f"products={len({row['sku_code'] for row in rows})}")
    print(f"refunds={sum(1 for row in rows if row['refund_flag'] == 'Y')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())