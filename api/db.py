"""Lightweight database helpers for platform API routes."""

from __future__ import annotations

from contextlib import contextmanager
from threading import Lock
from typing import Any, Iterable

from mysql.connector import connect

from agent.platform.db import get_db_config


_platform_schema_ready = False
_platform_schema_lock = Lock()


@contextmanager
def mysql_conn(dictionary: bool = True):
    """Create a MySQL connection and cursor for platform API routes."""
    conn = connect(**get_db_config())
    try:
        yield conn, conn.cursor(dictionary=dictionary)
    finally:
        conn.close()


def fetch_all(sql: str, params: Iterable[Any] | None = None) -> list[dict[str, Any]]:
    """执行 SELECT 并返?dict 列表?"""
    with mysql_conn(dictionary=True) as (_, cursor):
        cursor.execute(sql, tuple(params or ()))
        return list(cursor.fetchall() or [])


def fetch_one(sql: str, params: Iterable[Any] | None = None) -> dict[str, Any] | None:
    """执行 SELECT 并返回单?dict；没有结果时返回 None?"""
    with mysql_conn(dictionary=True) as (_, cursor):
        cursor.execute(sql, tuple(params or ()))
        return cursor.fetchone()


def execute(sql: str, params: Iterable[Any] | None = None) -> int:
    """执行 INSERT/UPDATE/DELETE 并提交事务，返回 affected rows?"""
    with mysql_conn(dictionary=True) as (conn, cursor):
        cursor.execute(sql, tuple(params or ()))
        affected = cursor.rowcount
        conn.commit()
        return affected


def execute_many(sql: str, rows: Iterable[Iterable[Any]]) -> int:
    """批量执行同一条写 SQL，减少请求期反复建连接的开销?"""
    rows = list(rows)
    if not rows:
        return 0
    with mysql_conn(dictionary=True) as (conn, cursor):
        cursor.executemany(sql, rows)
        affected = cursor.rowcount
        conn.commit()
        return affected


def table_exists(table_name: str) -> bool:
    """检查表是否存在，用于请求期幂等初始化平台表?"""
    row = fetch_one(
        """
        SELECT COUNT(*) AS count
        FROM information_schema.tables
        WHERE table_schema = DATABASE() AND table_name = %s
        """,
        (table_name,),
    )
    return bool(row and row.get("count"))


def column_exists(table_name: str, column_name: str) -> bool:
    """检查字段是否存在；兼容?MySQL，不依赖 ADD COLUMN IF NOT EXISTS?"""
    row = fetch_one(
        """
        SELECT COUNT(*) AS count
        FROM information_schema.columns
        WHERE table_schema = DATABASE() AND table_name = %s AND column_name = %s
        """,
        (table_name, column_name),
    )
    return bool(row and row.get("count"))


def ensure_platform_schema() -> None:
    """Create or patch local platform tables idempotently for development."""
    global _platform_schema_ready
    if _platform_schema_ready:
        return
    with _platform_schema_lock:
        if _platform_schema_ready:
            return

    statements = [
        """
        CREATE TABLE IF NOT EXISTS gateway_tenants (
          id VARCHAR(64) PRIMARY KEY,
          name VARCHAR(128) NOT NULL,
          status VARCHAR(32) NOT NULL DEFAULT 'active',
          created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """,
        """
        CREATE TABLE IF NOT EXISTS gateway_shops (
          id VARCHAR(64) NOT NULL,
          tenant_id VARCHAR(64) NOT NULL,
          name VARCHAR(128) NOT NULL,
          category VARCHAR(128) NOT NULL DEFAULT '',
          platform VARCHAR(64) NOT NULL DEFAULT 'taobao_tmall',
          shop_type VARCHAR(32) NOT NULL DEFAULT 'brand_owned',
          business_stage VARCHAR(32) NOT NULL DEFAULT 'growth',
          status VARCHAR(32) NOT NULL DEFAULT 'active',
          auth_status VARCHAR(32) NOT NULL DEFAULT 'pending',
          data_status VARCHAR(32) NOT NULL DEFAULT 'empty',
          last_sync_at DATETIME NULL,
          created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
          PRIMARY KEY (tenant_id, id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """,
        """
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
          UNIQUE KEY uk_platform_integrations_shop_platform (tenant_id, shop_id, platform)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """,
        """
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
          updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """,
        """
        CREATE TABLE IF NOT EXISTS business_reports (
          id VARCHAR(64) PRIMARY KEY,
          tenant_id VARCHAR(64) NOT NULL,
          shop_id VARCHAR(64) NOT NULL,
          type VARCHAR(32) NOT NULL,
          title VARCHAR(255) NOT NULL,
          summary TEXT NOT NULL,
          content_markdown MEDIUMTEXT NULL,
          structured_json JSON NULL,
          status VARCHAR(32) NOT NULL DEFAULT 'draft',
          source_task_id VARCHAR(64) NULL,
          created_by VARCHAR(64) NOT NULL,
          created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """,
        """
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
          params_json JSON NULL,
          result_report_id VARCHAR(64) NULL,
          result_summary_json JSON NULL,
          error_message TEXT NULL,
          created_by VARCHAR(64) NOT NULL,
          created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """,
        """
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
          reviewed_at DATETIME NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """,
                """
                CREATE TABLE IF NOT EXISTS ai_chat_conversations (
                    id VARCHAR(64) PRIMARY KEY,
                    tenant_id VARCHAR(64) NOT NULL,
                    shop_id VARCHAR(64) NOT NULL,
                    user_id VARCHAR(64) NOT NULL,
                    title VARCHAR(255) NOT NULL,
                    status VARCHAR(32) NOT NULL DEFAULT 'active',
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    KEY idx_ai_chat_conversations_scope (tenant_id, shop_id, user_id, updated_at)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """,
                """
                CREATE TABLE IF NOT EXISTS ai_chat_messages (
                    id VARCHAR(64) PRIMARY KEY,
                    tenant_id VARCHAR(64) NOT NULL,
                    shop_id VARCHAR(64) NOT NULL,
                    user_id VARCHAR(64) NOT NULL,
                    conversation_id VARCHAR(64) NOT NULL,
                    role VARCHAR(16) NOT NULL,
                    content MEDIUMTEXT NULL,
                    structured_json JSON NULL,
                    source VARCHAR(32) NULL,
                    status VARCHAR(32) NULL,
                    task_id VARCHAR(64) NULL,
                    intent VARCHAR(64) NULL,
                    error_message TEXT NULL,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    KEY idx_ai_chat_messages_conversation (tenant_id, shop_id, user_id, conversation_id, created_at),
                    KEY idx_ai_chat_messages_task (tenant_id, shop_id, task_id)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """,
                """
                CREATE TABLE IF NOT EXISTS ai_chat_runs (
                    id VARCHAR(64) PRIMARY KEY,
                    tenant_id VARCHAR(64) NOT NULL,
                    shop_id VARCHAR(64) NOT NULL,
                    user_id VARCHAR(64) NOT NULL,
                    conversation_id VARCHAR(64) NOT NULL,
                    message_id VARCHAR(64) NOT NULL,
                    task_id VARCHAR(64) NOT NULL,
                    user_content MEDIUMTEXT NOT NULL,
                    assistant_content MEDIUMTEXT NULL,
                    structured_json JSON NULL,
                    intent VARCHAR(64) NULL,
                    status VARCHAR(32) NOT NULL DEFAULT 'queued',
                    source VARCHAR(32) NOT NULL DEFAULT 'agent',
                    error_message TEXT NULL,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    completed_at DATETIME NULL,
                    UNIQUE KEY uk_ai_chat_runs_task (task_id),
                    KEY idx_ai_chat_runs_conversation (tenant_id, shop_id, user_id, conversation_id, created_at),
                    KEY idx_ai_chat_runs_message (tenant_id, shop_id, message_id)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """,
    ]
    for statement in statements:
        execute(statement)

    # 老版?gateway_shops 可能只有 auth_status/data_status；逐列补齐产品字段?
    for column_name, ddl in {
        "category": "ALTER TABLE gateway_shops ADD COLUMN category VARCHAR(128) NOT NULL DEFAULT ''",
        "platform": "ALTER TABLE gateway_shops ADD COLUMN platform VARCHAR(64) NOT NULL DEFAULT 'taobao_tmall'",
        "shop_type": "ALTER TABLE gateway_shops ADD COLUMN shop_type VARCHAR(32) NOT NULL DEFAULT 'brand_owned'",
        "business_stage": "ALTER TABLE gateway_shops ADD COLUMN business_stage VARCHAR(32) NOT NULL DEFAULT 'growth'",
        "status": "ALTER TABLE gateway_shops ADD COLUMN status VARCHAR(32) NOT NULL DEFAULT 'active'",
        "last_sync_at": "ALTER TABLE gateway_shops ADD COLUMN last_sync_at DATETIME NULL",
    }.items():
        if table_exists("gateway_shops") and not column_exists("gateway_shops", column_name):
            execute(ddl)
    for column_name, ddl in {
        "params_json": "ALTER TABLE agent_jobs ADD COLUMN params_json JSON NULL",
        "result_report_id": "ALTER TABLE agent_jobs ADD COLUMN result_report_id VARCHAR(64) NULL",
        "result_summary_json": "ALTER TABLE agent_jobs ADD COLUMN result_summary_json JSON NULL",
    }.items():
        if table_exists("agent_jobs") and not column_exists("agent_jobs", column_name):
            execute(ddl)
    for table_name, column_name, ddl in (
        ("business_reports", "structured_json", "ALTER TABLE business_reports ADD COLUMN structured_json JSON NULL"),
        ("ai_chat_messages", "structured_json", "ALTER TABLE ai_chat_messages ADD COLUMN structured_json JSON NULL"),
        ("ai_chat_runs", "structured_json", "ALTER TABLE ai_chat_runs ADD COLUMN structured_json JSON NULL"),
    ):
        if table_exists(table_name) and not column_exists(table_name, column_name):
            execute(ddl)
    _platform_schema_ready = True
