USE ecommerce_demo;

SET @tenant_id = 'tenant_demo';
SET @shop_id = 'default_shop';

ALTER TABLE customers ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(64) NOT NULL DEFAULT 'tenant_demo' FIRST;
ALTER TABLE customers ADD COLUMN IF NOT EXISTS shop_id VARCHAR(64) NOT NULL DEFAULT 'default_shop' AFTER tenant_id;

ALTER TABLE sellers ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(64) NOT NULL DEFAULT 'tenant_demo' FIRST;
ALTER TABLE sellers ADD COLUMN IF NOT EXISTS shop_id VARCHAR(64) NOT NULL DEFAULT 'default_shop' AFTER tenant_id;

ALTER TABLE products ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(64) NOT NULL DEFAULT 'tenant_demo' FIRST;
ALTER TABLE products ADD COLUMN IF NOT EXISTS shop_id VARCHAR(64) NOT NULL DEFAULT 'default_shop' AFTER tenant_id;

ALTER TABLE orders ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(64) NOT NULL DEFAULT 'tenant_demo' FIRST;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS shop_id VARCHAR(64) NOT NULL DEFAULT 'default_shop' AFTER tenant_id;

ALTER TABLE order_items ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(64) NOT NULL DEFAULT 'tenant_demo' FIRST;
ALTER TABLE order_items ADD COLUMN IF NOT EXISTS shop_id VARCHAR(64) NOT NULL DEFAULT 'default_shop' AFTER tenant_id;

ALTER TABLE payments ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(64) NOT NULL DEFAULT 'tenant_demo' FIRST;
ALTER TABLE payments ADD COLUMN IF NOT EXISTS shop_id VARCHAR(64) NOT NULL DEFAULT 'default_shop' AFTER tenant_id;

ALTER TABLE reviews ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(64) NOT NULL DEFAULT 'tenant_demo' FIRST;
ALTER TABLE reviews ADD COLUMN IF NOT EXISTS shop_id VARCHAR(64) NOT NULL DEFAULT 'default_shop' AFTER tenant_id;

ALTER TABLE inventory ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(64) NOT NULL DEFAULT 'tenant_demo' FIRST;
ALTER TABLE inventory ADD COLUMN IF NOT EXISTS shop_id VARCHAR(64) NOT NULL DEFAULT 'default_shop' AFTER tenant_id;

ALTER TABLE traffic_stats ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(64) NOT NULL DEFAULT 'tenant_demo' AFTER stat_id;
ALTER TABLE traffic_stats ADD COLUMN IF NOT EXISTS shop_id VARCHAR(64) NOT NULL DEFAULT 'default_shop' AFTER tenant_id;

ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(64) NOT NULL DEFAULT 'tenant_demo' FIRST;
ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS shop_id VARCHAR(64) NOT NULL DEFAULT 'default_shop' AFTER tenant_id;

ALTER TABLE campaign_product_stats ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(64) NOT NULL DEFAULT 'tenant_demo' FIRST;
ALTER TABLE campaign_product_stats ADD COLUMN IF NOT EXISTS shop_id VARCHAR(64) NOT NULL DEFAULT 'default_shop' AFTER tenant_id;

ALTER TABLE refunds ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(64) NOT NULL DEFAULT 'tenant_demo' FIRST;
ALTER TABLE refunds ADD COLUMN IF NOT EXISTS shop_id VARCHAR(64) NOT NULL DEFAULT 'default_shop' AFTER tenant_id;

ALTER TABLE customer_service_tickets ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(64) NOT NULL DEFAULT 'tenant_demo' FIRST;
ALTER TABLE customer_service_tickets ADD COLUMN IF NOT EXISTS shop_id VARCHAR(64) NOT NULL DEFAULT 'default_shop' AFTER tenant_id;
