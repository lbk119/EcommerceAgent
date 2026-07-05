CREATE DATABASE IF NOT EXISTS ecommerce_demo CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE ecommerce_demo;

SET FOREIGN_KEY_CHECKS = 0;
DROP TABLE IF EXISTS customer_service_tickets;
DROP TABLE IF EXISTS refunds;
DROP TABLE IF EXISTS campaign_product_stats;
DROP TABLE IF EXISTS campaigns;
DROP TABLE IF EXISTS traffic_stats;
DROP TABLE IF EXISTS inventory;
DROP TABLE IF EXISTS reviews;
DROP TABLE IF EXISTS payments;
DROP TABLE IF EXISTS order_items;
DROP TABLE IF EXISTS orders;
DROP TABLE IF EXISTS products;
DROP TABLE IF EXISTS sellers;
DROP TABLE IF EXISTS customers;
SET FOREIGN_KEY_CHECKS = 1;

CREATE TABLE customers (
  customer_id VARCHAR(64) PRIMARY KEY,
  customer_unique_id VARCHAR(64),
  customer_zip_code_prefix INT,
  customer_city VARCHAR(128),
  customer_state VARCHAR(32),
  INDEX idx_customer_unique_id (customer_unique_id),
  INDEX idx_customer_location (customer_state, customer_city)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE sellers (
  seller_id VARCHAR(64) PRIMARY KEY,
  seller_zip_code_prefix INT,
  seller_city VARCHAR(128),
  seller_state VARCHAR(32),
  INDEX idx_seller_location (seller_state, seller_city)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE products (
  product_id VARCHAR(64) PRIMARY KEY,
  category_name VARCHAR(128),
  category_name_en VARCHAR(128),
  product_name_length INT,
  product_description_length INT,
  product_photos_qty INT,
  product_weight_g INT,
  product_length_cm INT,
  product_height_cm INT,
  product_width_cm INT,
  INDEX idx_product_category (category_name_en)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE orders (
  order_id VARCHAR(64) PRIMARY KEY,
  customer_id VARCHAR(64),
  order_status VARCHAR(32),
  order_purchase_timestamp DATETIME,
  order_approved_at DATETIME NULL,
  order_delivered_carrier_date DATETIME NULL,
  order_delivered_customer_date DATETIME NULL,
  order_estimated_delivery_date DATETIME NULL,
  INDEX idx_orders_customer (customer_id),
  INDEX idx_orders_purchase_time (order_purchase_timestamp),
  INDEX idx_orders_status (order_status),
  CONSTRAINT fk_orders_customer FOREIGN KEY (customer_id) REFERENCES customers(customer_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE order_items (
  order_id VARCHAR(64),
  order_item_id INT,
  product_id VARCHAR(64),
  seller_id VARCHAR(64),
  shipping_limit_date DATETIME NULL,
  price DECIMAL(12,2),
  freight_value DECIMAL(12,2),
  PRIMARY KEY (order_id, order_item_id),
  INDEX idx_order_items_product (product_id),
  INDEX idx_order_items_seller (seller_id),
  CONSTRAINT fk_order_items_order FOREIGN KEY (order_id) REFERENCES orders(order_id),
  CONSTRAINT fk_order_items_product FOREIGN KEY (product_id) REFERENCES products(product_id),
  CONSTRAINT fk_order_items_seller FOREIGN KEY (seller_id) REFERENCES sellers(seller_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE payments (
  order_id VARCHAR(64),
  payment_sequential INT,
  payment_type VARCHAR(64),
  payment_installments INT,
  payment_value DECIMAL(12,2),
  PRIMARY KEY (order_id, payment_sequential),
  INDEX idx_payments_type (payment_type),
  CONSTRAINT fk_payments_order FOREIGN KEY (order_id) REFERENCES orders(order_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE reviews (
  review_id VARCHAR(64),
  order_id VARCHAR(64),
  review_score INT,
  review_comment_title TEXT,
  review_comment_message TEXT,
  review_creation_date DATETIME NULL,
  review_answer_timestamp DATETIME NULL,
  PRIMARY KEY (review_id, order_id),
  INDEX idx_reviews_order (order_id),
  INDEX idx_reviews_score (review_score),
  CONSTRAINT fk_reviews_order FOREIGN KEY (order_id) REFERENCES orders(order_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE inventory (
  product_id VARCHAR(64) PRIMARY KEY,
  stock INT NOT NULL,
  safety_stock INT NOT NULL,
  warehouse VARCHAR(64) NOT NULL,
  updated_at DATETIME NOT NULL,
  INDEX idx_inventory_stock (stock, safety_stock),
  CONSTRAINT fk_inventory_product FOREIGN KEY (product_id) REFERENCES products(product_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE traffic_stats (
  stat_id BIGINT AUTO_INCREMENT PRIMARY KEY,
  stat_date DATE NOT NULL,
  product_id VARCHAR(64) NOT NULL,
  views INT NOT NULL,
  visitors INT NOT NULL,
  add_to_cart INT NOT NULL,
  favorites INT NOT NULL,
  conversions INT NOT NULL,
  INDEX idx_traffic_date (stat_date),
  INDEX idx_traffic_product_date (product_id, stat_date),
  CONSTRAINT fk_traffic_product FOREIGN KEY (product_id) REFERENCES products(product_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE campaigns (
  campaign_id VARCHAR(64) PRIMARY KEY,
  campaign_name VARCHAR(128) NOT NULL,
  channel VARCHAR(64) NOT NULL,
  start_time DATETIME NOT NULL,
  end_time DATETIME NOT NULL,
  budget DECIMAL(12,2) NOT NULL,
  status VARCHAR(32) NOT NULL,
  INDEX idx_campaign_time (start_time, end_time)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE campaign_product_stats (
  campaign_id VARCHAR(64),
  product_id VARCHAR(64),
  impressions INT NOT NULL,
  clicks INT NOT NULL,
  orders_count INT NOT NULL,
  revenue DECIMAL(12,2) NOT NULL,
  spend DECIMAL(12,2) NOT NULL,
  PRIMARY KEY (campaign_id, product_id),
  INDEX idx_campaign_product (product_id),
  CONSTRAINT fk_campaign_stats_campaign FOREIGN KEY (campaign_id) REFERENCES campaigns(campaign_id),
  CONSTRAINT fk_campaign_stats_product FOREIGN KEY (product_id) REFERENCES products(product_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE refunds (
  refund_id VARCHAR(64) PRIMARY KEY,
  order_id VARCHAR(64) NOT NULL,
  product_id VARCHAR(64) NOT NULL,
  refund_time DATETIME NOT NULL,
  refund_amount DECIMAL(12,2) NOT NULL,
  refund_reason VARCHAR(128) NOT NULL,
  refund_status VARCHAR(32) NOT NULL,
  INDEX idx_refunds_order (order_id),
  INDEX idx_refunds_product_time (product_id, refund_time),
  CONSTRAINT fk_refunds_order FOREIGN KEY (order_id) REFERENCES orders(order_id),
  CONSTRAINT fk_refunds_product FOREIGN KEY (product_id) REFERENCES products(product_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE customer_service_tickets (
  ticket_id VARCHAR(64) PRIMARY KEY,
  order_id VARCHAR(64) NULL,
  product_id VARCHAR(64) NULL,
  ticket_time DATETIME NOT NULL,
  issue_type VARCHAR(64) NOT NULL,
  channel VARCHAR(64) NOT NULL,
  sentiment VARCHAR(32) NOT NULL,
  status VARCHAR(32) NOT NULL,
  summary TEXT,
  INDEX idx_tickets_time (ticket_time),
  INDEX idx_tickets_issue (issue_type),
  INDEX idx_tickets_product (product_id),
  CONSTRAINT fk_tickets_order FOREIGN KEY (order_id) REFERENCES orders(order_id),
  CONSTRAINT fk_tickets_product FOREIGN KEY (product_id) REFERENCES products(product_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
