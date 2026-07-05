USE ecommerce_demo;

-- =====================================================================================
-- EcomPilot SaaS 平台层表结构
-- =====================================================================================
-- 说明：
-- 1. 这些表服务于“电商运营数字员工平台”的产品化能力，不替换已有 Olist/demo 经营表。
-- 2. 所有平台业务表都显式带 tenant_id；与具体店铺有关的表同时带 shop_id。
-- 3. MySQL 8.0 以下环境对 ALTER TABLE ... IF NOT EXISTS 支持不稳定；生产迁移建议使用
--    data/ecommerce_demo/apply_platform_schema.py 这种 information_schema 检查式迁移。
-- =====================================================================================

CREATE TABLE IF NOT EXISTS platform_integrations (
  id VARCHAR(64) PRIMARY KEY,
  tenant_id VARCHAR(64) NOT NULL,
  shop_id VARCHAR(64) NOT NULL,
  platform VARCHAR(64) NOT NULL,
  status VARCHAR(32) NOT NULL DEFAULT 'unauthorized',
  access_token_encrypted TEXT NULL,
  refresh_token_encrypted TEXT NULL,
  expires_at DATETIME NULL,
  last_sync_at DATETIME NULL,
  error_message TEXT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uk_platform_integrations_shop_platform (tenant_id, shop_id, platform),
  KEY idx_platform_integrations_status (tenant_id, shop_id, status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS data_import_jobs (
  id VARCHAR(64) PRIMARY KEY,
  tenant_id VARCHAR(64) NOT NULL,
  shop_id VARCHAR(64) NOT NULL,
  source VARCHAR(32) NOT NULL,
  file_name VARCHAR(255) NULL,
  status VARCHAR(32) NOT NULL DEFAULT 'uploaded',
  rows_count INT NOT NULL DEFAULT 0,
  quality_score INT NOT NULL DEFAULT 0,
  mapping_json JSON NULL,
  error_message TEXT NULL,
  created_by VARCHAR(64) NOT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  KEY idx_data_import_jobs_scope (tenant_id, shop_id, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS business_reports (
  id VARCHAR(64) PRIMARY KEY,
  tenant_id VARCHAR(64) NOT NULL,
  shop_id VARCHAR(64) NOT NULL,
  type VARCHAR(32) NOT NULL,
  title VARCHAR(255) NOT NULL,
  summary TEXT NOT NULL,
  content_markdown MEDIUMTEXT NULL,
  status VARCHAR(32) NOT NULL DEFAULT 'draft',
  source_task_id VARCHAR(64) NULL,
  created_by VARCHAR(64) NOT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  KEY idx_business_reports_scope (tenant_id, shop_id, type, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS agent_jobs (
  id VARCHAR(64) PRIMARY KEY,
  tenant_id VARCHAR(64) NOT NULL,
  shop_id VARCHAR(64) NOT NULL,
  agent_id VARCHAR(64) NOT NULL,
  agent_name VARCHAR(128) NOT NULL,
  job_type VARCHAR(64) NOT NULL,
  title VARCHAR(255) NOT NULL,
  status VARCHAR(32) NOT NULL DEFAULT 'pending',
  task_id VARCHAR(64) NULL,
  conversation_id VARCHAR(64) NULL,
  result_report_id VARCHAR(64) NULL,
  error_message TEXT NULL,
  created_by VARCHAR(64) NOT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  KEY idx_agent_jobs_scope (tenant_id, shop_id, agent_id, created_at),
  KEY idx_agent_jobs_task (task_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS strategy_reviews (
  id VARCHAR(64) PRIMARY KEY,
  tenant_id VARCHAR(64) NOT NULL,
  shop_id VARCHAR(64) NOT NULL,
  title VARCHAR(255) NOT NULL,
  source VARCHAR(128) NOT NULL,
  expected_impact TEXT NOT NULL,
  risk_level VARCHAR(16) NOT NULL DEFAULT 'medium',
  status VARCHAR(32) NOT NULL DEFAULT 'pending',
  source_task_id VARCHAR(64) NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  reviewed_by VARCHAR(64) NULL,
  reviewed_at DATETIME NULL,
  KEY idx_strategy_reviews_scope (tenant_id, shop_id, status, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
