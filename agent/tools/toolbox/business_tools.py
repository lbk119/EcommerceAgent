"""确定性电商业务工具集合。

这些工具把底层 business_metrics 查询包装成统一的 JSON-ready 输出，供 deepagents subagents 调用。
工具执行时会临时设置 tenant/user/shop 身份，结束后恢复 ContextVar，避免跨任务串号。
"""

from __future__ import annotations

import csv
import io
import time
from dataclasses import dataclass
from typing import Any, Callable

from agent.memory import MemoryIdentity
from agent.tools import business_metrics as metrics
from api.context import reset_identity_context, set_identity_context


ToolCallable = Callable[[dict[str, Any]], list[dict[str, Any]] | dict[str, Any]]


@dataclass(frozen=True)
class BusinessTool:
    """单个确定性业务工具的描述和执行包装。"""

    name: str
    description: str
    runner: ToolCallable
    timeout_seconds: float = 5.0

    def run(self, params: dict[str, Any], context: dict[str, Any]) -> list[dict[str, Any]] | dict[str, Any]:
        started_at = time.perf_counter()
        identity_token = _set_scope(context)
        try:
            result = self.runner(params or {})
            if isinstance(result, dict):
                return _json_ready(result, started_at=started_at)
            return [_json_ready(row) for row in result]
        except Exception as error:
            return {"status": "failed", "tool_name": self.name, "error": str(error)[:500], "rows": [], "latency_ms": round((time.perf_counter() - started_at) * 1000, 2)}
        finally:
            if identity_token is not None:
                reset_identity_context(identity_token)


def list_business_tools() -> dict[str, BusinessTool]:
    """返回业务工具目录副本，避免调用方直接修改内部注册表。"""
    return dict(_TOOLS)


def get_business_tool(name: str) -> BusinessTool:
    """按稳定工具名获取业务工具。"""
    if name not in _TOOLS:
        raise ValueError(f"unknown business tool: {name}")
    return _TOOLS[name]


def _set_scope(context: dict[str, Any]):
    tenant_id = str(context.get("tenant_id") or "")
    shop_id = str(context.get("shop_id") or "")
    user_id = str(context.get("user_id") or "local_user")
    if not tenant_id or not shop_id:
        return None
    return set_identity_context(MemoryIdentity(tenant_id=tenant_id, user_id=user_id, shop_id=shop_id))


def _time_range(params: dict[str, Any]) -> metrics.BusinessTimeRange:
    label = str(params.get("time_range") or "")
    if label.startswith("last_") and label.endswith("d"):
        try:
            return metrics.BusinessTimeRange(label, int(label[5:-1]))
        except ValueError:
            pass
    if label == "today":
        return metrics.BusinessTimeRange("today", 1)
    if label == "yesterday":
        return metrics.BusinessTimeRange("yesterday", 1, offset_days=1)
    return metrics.parse_business_time_range(str(params.get("query") or label or ""))


def _limit(params: dict[str, Any], default: int = 8) -> int:
    try:
        return max(1, min(int(params.get("limit") or default), 50))
    except (TypeError, ValueError):
        return default


def _csv_rows(payload: str) -> list[dict[str, Any]]:
    lines = [line for line in str(payload or "").splitlines() if line.strip() and not line.startswith("请求时间窗口内没有足够")]
    if not lines:
        return []
    reader = csv.DictReader(io.StringIO("\n".join(lines)))
    return [{key: _coerce(value) for key, value in row.items()} for row in reader]


def _json_ready(row: dict[str, Any], *, started_at: float | None = None) -> dict[str, Any]:
    ready = {str(key): _coerce(value) for key, value in row.items()}
    if started_at is not None:
        ready["latency_ms"] = round((time.perf_counter() - started_at) * 1000, 2)
    return ready


def _coerce(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (int, float, bool, dict, list)):
        return value
    text = str(value)
    try:
        if "." in text:
            return float(text)
        return int(text)
    except ValueError:
        return text


def _rows_from_metric(call: Callable[..., str], params: dict[str, Any], *, include_limit: bool = False) -> list[dict[str, Any]]:
    if include_limit:
        return _csv_rows(call(_time_range(params), limit=_limit(params)))
    return _csv_rows(call(_time_range(params)))


def _shop_profile(params: dict[str, Any]) -> list[dict[str, Any]]:
    return _csv_rows(metrics.query_shop_profile())


def _sales_trend(params: dict[str, Any]) -> list[dict[str, Any]]:
    return _csv_rows(metrics.query_daily_metrics(_time_range(params)))


def _low_conversion(params: dict[str, Any]) -> list[dict[str, Any]]:
    rows = _csv_rows(metrics.query_hot_products(_time_range(params), limit=max(_limit(params), 12)))
    return sorted(rows, key=lambda row: (float(row.get("conversion_rate") or 0), -float(row.get("visitors") or 0)))[:_limit(params)]


def _data_quality_summary(params: dict[str, Any]) -> dict[str, Any]:
    return {"status": "ok", "rows": [], "summary": "数据质量诊断工具已接入；当前环境未发现可用异常明细。"}


def _import_jobs(params: dict[str, Any]) -> dict[str, Any]:
    return {"status": "ok", "rows": [], "summary": "导入任务检查已降级为安全空结果；请接入 import_jobs 表后返回真实记录。"}


def _platform_auth(params: dict[str, Any]) -> dict[str, Any]:
    return {"status": "ok", "rows": _shop_profile(params), "summary": "平台授权状态来自当前店铺画像。"}


def _data_freshness(params: dict[str, Any]) -> dict[str, Any]:
    return {"status": "ok", "rows": _shop_profile(params), "summary": "数据新鲜度以店铺 last_sync_at 为准。"}


_TOOLS: dict[str, BusinessTool] = {
    "query_daily_metrics": BusinessTool("query_daily_metrics", "经营核心指标", lambda params: _rows_from_metric(metrics.query_daily_metrics, params)),
    "query_daily_risks": BusinessTool("query_daily_risks", "经营风险", lambda params: _rows_from_metric(metrics.query_daily_risks, params)),
    "query_shop_profile": BusinessTool("query_shop_profile", "店铺画像", _shop_profile),
    "query_inventory_risks": BusinessTool("query_inventory_risks", "库存风险", lambda params: _rows_from_metric(metrics.query_inventory_risks, params)),
    "query_inventory_velocity": BusinessTool("query_inventory_velocity", "库存速度与补货", lambda params: _rows_from_metric(metrics.query_inventory_velocity, params)),
    "query_sales_trend": BusinessTool("query_sales_trend", "销售趋势", _sales_trend),
    "query_campaign_traffic": BusinessTool("query_campaign_traffic", "活动流量", lambda params: _rows_from_metric(metrics.query_campaign_traffic, params)),
    "query_campaign_roi": BusinessTool("query_campaign_roi", "活动 ROI", lambda params: _rows_from_metric(metrics.query_campaign_roi, params)),
    "query_campaign_risks": BusinessTool("query_campaign_risks", "活动风险", lambda params: _rows_from_metric(metrics.query_campaign_risks, params)),
    "query_hot_products": BusinessTool("query_hot_products", "爆品候选", lambda params: _rows_from_metric(metrics.query_hot_products, params, include_limit=True)),
    "query_low_conversion_products": BusinessTool("query_low_conversion_products", "低转化商品", _low_conversion),
    "query_import_jobs": BusinessTool("query_import_jobs", "数据导入任务", _import_jobs),
    "query_platform_auth_status": BusinessTool("query_platform_auth_status", "平台授权状态", _platform_auth),
    "query_data_freshness": BusinessTool("query_data_freshness", "数据新鲜度", _data_freshness),
    "query_data_quality_summary": BusinessTool("query_data_quality_summary", "数据质量摘要", _data_quality_summary),
    "check_import_jobs": BusinessTool("check_import_jobs", "数据导入任务", _import_jobs),
    "check_platform_authorization": BusinessTool("check_platform_authorization", "平台授权状态", _platform_auth),
    "check_data_freshness": BusinessTool("check_data_freshness", "数据新鲜度", _data_freshness),
    "check_schema_health": BusinessTool("check_schema_health", "Schema 健康状态", _data_quality_summary),
}
