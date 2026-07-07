"""Agent 侧 MySQL 访问工具。

这个模块服务两类场景：
1. LangChain/DeepAgents 工具需要读取电商经营表、展示 schema 或执行只读 SQL；
2. 受控 workflow 节点需要在明确权限下执行少量写入/DDL。

安全边界：
- 经营数据表必须绑定当前 ContextVar 中的 tenant_id/shop_id；
- 自由 SQL 默认只允许只读语句，并要求访问经营表时显式包含 tenant_id/shop_id；
- 写 SQL 不暴露为 LangChain tool，只供受控节点在额外权限检查后调用。
"""

import os
import re
from pathlib import Path

from dotenv import load_dotenv
from mysql.connector import Error, connect

from api.context import get_identity_context


PROJECT_ROOT = Path(__file__).resolve().parents[2]
# 加载仓库根目录 .env，保证脚本、FastAPI 和 Agent 工具读到同一套 MySQL 配置。
load_dotenv(PROJECT_ROOT / ".env")

# 这些表包含租户/店铺经营数据，任何读取都必须走 tenant/shop 隔离兜底。
TENANT_SCOPED_TABLES = {
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
}


def get_db_config() -> dict:
    """从环境变量读取 MySQL 连接配置。

    Returns:
        dict: mysql.connector.connect 可直接使用的配置字典。

    Raises:
        ValueError: 缺少 MYSQL_USER / MYSQL_PASSWORD / MYSQL_DATABASE 等核心配置。
    """
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

    # user/password/database 是本项目访问 MySQL 的最低要求；缺失时尽早失败，避免下游报错含糊。
    missing_keys = [key for key in ("user", "password", "database") if key not in config]
    if missing_keys:
        raise ValueError(f"缺失数据库核心配置：{', '.join(missing_keys)}")
    return config


def list_sql_tables_raw() -> str:
    """列出当前数据库可用表名，返回适合模型阅读的纯文本。"""
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
    """返回单表字段元数据。

    该函数用于数据库助手理解字段结构；这里只读 SHOW COLUMNS，不返回真实业务数据。
    """
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
    """返回有限行数的表数据，格式为 CSV 风格文本。

    对经营表会自动拼接当前 tenant/shop 条件；没有可信身份上下文时直接拒绝，防止本地工具被
    LLM 或测试脚本误用成跨租户读数入口。
    """
    if table_name in TENANT_SCOPED_TABLES:
        scope = current_data_scope_sql(table_name)
        if not scope:
            return "查询被拒绝：缺少可信 tenant/shop 上下文，不能读取店铺经营数据。"
    # 硬限制最多 100 行，避免模型一次拉取过多数据造成性能和隐私风险。
    limit = max(1, min(int(limit), 100))
    try:
        with connect(**get_db_config()) as conn:
            with conn.cursor() as cursor:
                where_clause = f" WHERE {scope}" if table_name in TENANT_SCOPED_TABLES else ""
                cursor.execute(f"select * from {table_name}{where_clause} limit {limit}")
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
    """执行只读 SQL，并以 CSV 风格文本返回结果。

    允许 SELECT/WITH/SHOW/DESCRIBE/DESC；SELECT 若未写 LIMIT，会自动追加 LIMIT 100。
    注意这里不尝试重写 LLM 生成的复杂 SQL，只做“是否只读”和“是否显式租户隔离”的兜底检查。
    """
    config = get_db_config()
    stripped_query = query.strip().rstrip(";")
    if not re.match(r"^(select|with|show|describe|desc)\b", stripped_query, re.IGNORECASE):
        return "查询被拒绝：只允许执行 SELECT、WITH、SHOW、DESCRIBE/DESC 只读语句。"
    if re.match(r"^select\b", stripped_query, re.IGNORECASE) and not re.search(r"\blimit\b", stripped_query, re.IGNORECASE):
        stripped_query = f"{stripped_query} LIMIT 100"

    # 自由 SQL 是最容易跨租户泄漏的入口，因此在真正执行前统一做隔离校验。
    isolation_error = validate_tenant_scoped_read_sql(stripped_query)
    if isolation_error:
        return isolation_error

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


def current_data_scope_sql(alias: str | None = None) -> str:
    """生成当前请求的 tenant/shop SQL 条件；没有可信上下文时返回空字符串。

    Args:
        alias: 可选表别名；传入后生成 `alias.tenant_id = ...` 形式，方便 JOIN 查询复用。
    """
    identity = get_identity_context()
    if not identity or not identity.tenant_id or not identity.shop_id:
        return ""
    prefix = f"{alias}." if alias else ""
    return f"{prefix}tenant_id = '{_escape_sql_literal(identity.tenant_id)}' AND {prefix}shop_id = '{_escape_sql_literal(identity.shop_id)}'"


def validate_tenant_scoped_read_sql(query: str) -> str:
    """
    自由 SQL 的租户隔离兜底。

    固定 workflow 会显式拼接 current_data_scope_sql；通用 execute_sql_query 允许 LLM 写 SQL，但只要碰到
    经营表，就必须显式包含 tenant_id 和 shop_id，防止跨店铺扫数。这里是治理兜底，不做 SQL 重写。
    """
    lowered = query.lower()
    touched_tables = [table for table in TENANT_SCOPED_TABLES if re.search(rf"\b{re.escape(table)}\b", lowered)]
    if not touched_tables:
        return ""
    if not current_data_scope_sql():
        return "查询被拒绝：缺少可信 tenant/shop 上下文，不能读取店铺经营数据。"
    if "tenant_id" not in lowered or "shop_id" not in lowered:
        return "查询被拒绝：访问经营数据表必须显式包含 tenant_id 和 shop_id 过滤条件。"
    return ""


def _escape_sql_literal(value: str) -> str:
    """转义 SQL 字符串字面量中的单引号。

    这里仅用于拼接受控的 tenant/shop 条件；普通业务查询仍应优先使用参数化 SQL。
    """
    return value.replace("'", "''")


def execute_write_sql_raw(query: str, target_database: str | None = None) -> dict:
    """
    受控 workflow 节点内部使用的写 SQL 执行器。

    它刻意不是 LangChain tool。工具 wrapper 或确定性 workflow 节点在完成自己的权限检查、sandbox
    检查、人工审核后，才可以调用这里执行真正写入。
    """
    stripped_query = query.strip().rstrip(";")
    statement_head = stripped_query.split(None, 1)[0].lower() if stripped_query else ""
    if statement_head not in {"insert", "update", "delete", "create", "alter", "drop", "truncate"}:
        return {"ok": False, "error": "只允许 INSERT、UPDATE、DELETE、CREATE、ALTER、DROP、TRUNCATE 写入/DDL 语句。"}

    if statement_head in {"update", "delete"} and not re.search(r"\bwhere\b", stripped_query, re.IGNORECASE):
        return {"ok": False, "error": "UPDATE/DELETE 必须包含 WHERE 条件，已拒绝执行。"}

    # DROP/TRUNCATE 默认禁止，只有本地显式设置 DB_ALLOW_DANGEROUS_DDL=true 才允许。
    if statement_head in {"drop", "truncate"} and os.getenv("DB_ALLOW_DANGEROUS_DDL", "false").lower() != "true":
        return {"ok": False, "error": "DROP/TRUNCATE 属于高危 DDL，默认禁止执行。"}

    config = get_db_config()
    if target_database:
        config["database"] = target_database
    # 写操作使用显式事务；执行成功后 commit，异常时连接关闭会回滚未提交事务。
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
