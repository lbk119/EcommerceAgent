import os
import re
from pathlib import Path

from dotenv import load_dotenv
from mysql.connector import Error, connect


PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")


def get_db_config() -> dict:
    """Get database configuration from environment variables."""
    config = {
        "host": os.getenv("MYSQL_HOST", "localhost"),
        "port": int(os.getenv("MYSQL_PORT", "3306")),
        "user": os.getenv("MYSQL_USER"),
        "password": os.getenv("MYSQL_PASSWORD"),
        "database": os.getenv("MYSQL_DATABASE"),
        "charset": os.getenv("MYSQL_CHARSET", "utf8mb4"),
        "collation": os.getenv("MYSQL_COLLATION", "utf8mb4_unicode_ci"),
        "autocommit": True,
        "sql_mode": os.getenv("MYSQL_SQL_MODE", "TRADITIONAL"),
    }
    config = {key: value for key, value in config.items() if value is not None}

    missing_keys = [key for key in ("user", "password", "database") if key not in config]
    if missing_keys:
        raise ValueError(f"缺失数据库核心配置：{', '.join(missing_keys)}")
    return config


def list_sql_tables_raw() -> str:
    """Return available table names in the configured database."""
    try:
        with connect(**get_db_config()) as conn:
            with conn.cursor() as cursor:
                cursor.execute("show tables")
                tables = cursor.fetchall()
                if not tables:
                    return "没有可用的表"
                table_names = [table[0] for table in tables]
                return f"可用的表有：{', '.join(table_names)}"
    except Error as error:
        return f"查询出现异常：{str(error)}"


def get_table_schema_raw(table_name: str) -> str:
    """Return column metadata for one table."""
    try:
        with connect(**get_db_config()) as conn:
            with conn.cursor() as cursor:
                cursor.execute(f"SHOW COLUMNS FROM {table_name}")
                rows = cursor.fetchall()
                if not rows:
                    return f"数据表：{table_name}不存在或没有字段信息！"
                return "字段名,类型,可为空,键,默认值,额外\n" + "\n".join(
                    ",".join("" if value is None else str(value) for value in row) for row in rows
                )
    except Error as error:
        return f"查询出现异常：{str(error)}"


def get_table_data_raw(table_name: str, limit: int = 20) -> str:
    """Return limited table data as CSV-style text."""
    limit = max(1, min(int(limit), 100))
    try:
        with connect(**get_db_config()) as conn:
            with conn.cursor() as cursor:
                cursor.execute(f"select * from {table_name} limit {limit}")
                description = cursor.description
                if not description:
                    return f"数据表：{table_name}为空没有数据！"
                columns = [desc[0] for desc in description]
                rows = cursor.fetchall()
                results = [",".join(map(str, row)) for row in rows]
                return f"{','.join(columns)}\n{chr(10).join(results)}"
    except Error as error:
        return f"查询出现异常：{str(error)}"


def execute_read_sql_raw(query: str) -> str:
    """Execute a read-only SQL statement and return CSV-style text."""
    config = get_db_config()
    stripped_query = query.strip().rstrip(";")
    if not re.match(r"^(select|with|show|describe|desc)\b", stripped_query, re.IGNORECASE):
        return "查询被拒绝：只允许执行 SELECT、WITH、SHOW、DESCRIBE/DESC 只读语句。"
    if re.match(r"^select\b", stripped_query, re.IGNORECASE) and not re.search(r"\blimit\b", stripped_query, re.IGNORECASE):
        stripped_query = f"{stripped_query} LIMIT 100"

    try:
        with connect(**config) as conn:
            with conn.cursor() as cursor:
                cursor.execute(stripped_query)
                description = cursor.description
                if not description:
                    return f"执行自定义SQL语句查询没有结果，sql为：{stripped_query}！"
                columns = [desc[0] for desc in description]
                rows = cursor.fetchall()
                results = [",".join(map(str, row)) for row in rows]
                return f"{','.join(columns)}\n{chr(10).join(results)}"
    except Error as error:
        return f"查询出现异常：{str(error)}"


def execute_write_sql_raw(query: str, target_database: str | None = None) -> dict:
    """
    Internal write executor for controlled workflow nodes.

    It is intentionally not a LangChain tool. Tool wrappers and deterministic workflow nodes may call it after
    their own permission checks.
    """
    stripped_query = query.strip().rstrip(";")
    statement_head = stripped_query.split(None, 1)[0].lower() if stripped_query else ""
    if statement_head not in {"insert", "update", "delete", "create", "alter", "drop", "truncate"}:
        return {"ok": False, "error": "只允许 INSERT、UPDATE、DELETE、CREATE、ALTER、DROP、TRUNCATE 写入/DDL 语句。"}

    if statement_head in {"update", "delete"} and not re.search(r"\bwhere\b", stripped_query, re.IGNORECASE):
        return {"ok": False, "error": "UPDATE/DELETE 必须包含 WHERE 条件，已拒绝执行。"}

    if statement_head in {"drop", "truncate"} and os.getenv("DB_ALLOW_DANGEROUS_DDL", "false").lower() != "true":
        return {"ok": False, "error": "DROP/TRUNCATE 属于高危 DDL，默认禁止执行。"}

    config = get_db_config()
    if target_database:
        config["database"] = target_database
    config["autocommit"] = False

    try:
        with connect(**config) as conn:
            with conn.cursor() as cursor:
                cursor.execute(stripped_query)
                affected_rows = cursor.rowcount
            conn.commit()
            return {
                "ok": True,
                "database": config.get("database"),
                "statement": statement_head,
                "affected_rows": affected_rows,
                "message": "SQL 已在目标数据库事务中执行并提交。",
            }
    except Error as error:
        return {"ok": False, "database": config.get("database"), "error": str(error)}
