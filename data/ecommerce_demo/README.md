# Ecommerce Demo 数据库

这个目录用于把 Olist 电商 CSV 导入 MySQL，并额外生成适合 EcomAgent 演示的运营模拟数据。

## 数据来源

Olist Brazilian E-Commerce Public Dataset:

```text
https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce
```

CSV 默认放在：

```text
data/olist/
```

## 生成的业务表

真实 Olist 数据导入为：

```text
customers
sellers
products
orders
order_items
payments
reviews
```

模拟运营数据生成：

```text
inventory
traffic_stats
campaigns
campaign_product_stats
refunds
customer_service_tickets
```

这些表可以支撑前端快捷任务：经营日报、爆品分析、库存预警、活动复盘、退款异常、客服知识库缺口分析。

## 前置条件

确保 MySQL 正在运行，并且 `.env` 中有：

```env
MYSQL_HOST=localhost
MYSQL_PORT=3306
MYSQL_USER=root
MYSQL_PASSWORD=your-password
MYSQL_DATABASE=ecommerce_demo
```

## 导入完整数据

在项目根目录执行：

```powershell
.\.venv\Scripts\python.exe .\data\ecommerce_demo\seed_ecommerce_demo.py --reset --database ecommerce_demo
```

## 快速烟测导入

如果想先少量导入验证脚本，可以限制每个 CSV 最多导入 1000 行：

```powershell
.\.venv\Scripts\python.exe .\data\ecommerce_demo\seed_ecommerce_demo.py --reset --database ecommerce_demo --limit 1000
```

## 验证 SQL

```sql
USE ecommerce_demo;
SHOW TABLES;
SELECT COUNT(*) FROM orders;
SELECT COUNT(*) FROM order_items;
SELECT COUNT(*) FROM inventory;
SELECT COUNT(*) FROM traffic_stats;
```

经营日报可用查询示例：

```sql
SELECT
  DATE(o.order_purchase_timestamp) AS stat_date,
  COUNT(DISTINCT o.order_id) AS orders_count,
  ROUND(SUM(oi.price + oi.freight_value), 2) AS gmv,
  ROUND(SUM(oi.price + oi.freight_value) / COUNT(DISTINCT o.order_id), 2) AS avg_order_value
FROM orders o
JOIN order_items oi ON oi.order_id = o.order_id
WHERE o.order_status IN ('delivered', 'shipped', 'invoiced')
GROUP BY DATE(o.order_purchase_timestamp)
ORDER BY stat_date DESC
LIMIT 14;
```

库存风险查询示例：

```sql
SELECT
  p.product_id,
  COALESCE(p.category_name_en, p.category_name) AS category,
  i.stock,
  i.safety_stock,
  i.warehouse
FROM inventory i
JOIN products p ON p.product_id = i.product_id
WHERE i.stock <= i.safety_stock
ORDER BY i.stock ASC
LIMIT 50;
```
