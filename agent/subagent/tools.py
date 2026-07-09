"""deepagents-native 工具包装器。

业务工具原本是确定性 Python callable，本文件把它们包装成 LangChain `StructuredTool`，并在执行前注入
tenant/shop/user 上下文、RuntimeGuard 计数和 trace。模型只看到安全参数 schema，不直接接触内部身份对象。
"""

from __future__ import annotations

import json
from contextvars import ContextVar
from typing import Any

from langchain_core.tools import StructuredTool

from agent.sandbox.client import SandboxClient
from agent.sandbox.errors import SandboxUnavailableError
from agent.sandbox.models import SandboxResourceLimits, SandboxTask
from agent.tools.registry import tool_registry
from agent.tools.toolbox.business_tools import list_business_tools
from agent.tools.toolbox.knowledge_tools import search_memory, search_reports, search_strategy_candidates
from agent.tools.tool_schemas import TOOL_SCHEMAS
from agent.trace.tracer import tracer


_TOOL_CONTEXT: ContextVar[dict[str, Any]] = ContextVar("deepagents_native_tool_context", default={})


def set_tool_context(context: dict[str, Any]):
    """为当前任务设置工具执行上下文，供 wrapper 读取身份和 guard。"""
    return _TOOL_CONTEXT.set(dict(context))


def reset_tool_context(token: Any) -> None:
    """恢复工具 ContextVar，避免一个任务的身份污染后续任务。"""
    _TOOL_CONTEXT.reset(token)


def native_tools_for_names(tool_names: list[str] | tuple[str, ...], profile: str, *, owner: str = "") -> list[StructuredTool]:
    """按工具名批量构造指定 profile/subagent 可见的 StructuredTool。"""
    return [_build_tool(name, profile, owner=owner) for name in tool_names]


def _build_tool(tool_name: str, profile: str, *, owner: str = "") -> StructuredTool:
    """把一个受控业务工具包装成 LangChain StructuredTool。"""
    schema = TOOL_SCHEMAS.get(tool_name, {})
    description = str(schema.get("description") or f"受控电商工具 {tool_name}")

    def run(
        time_range: str = "last_30d",
        limit: int = 8,
        category: str = "",
        metric_focus: str = "",
        campaign_keyword: str = "",
        product_ids: list[str] | None = None,
        include_inventory: bool | None = None,
        include_campaign: bool | None = None,
        only_low_stock: bool | None = None,
        include_sales_velocity: bool | None = None,
        report_type: str = "business",
        query: str = "",
    ) -> Any:
        params = _compact_params({
            "time_range": time_range,
            "limit": limit,
            "category": category,
            "metric_focus": metric_focus,
            "campaign_keyword": campaign_keyword,
            "product_ids": product_ids,
            "include_inventory": include_inventory,
            "include_campaign": include_campaign,
            "only_low_stock": only_low_stock,
            "include_sales_velocity": include_sales_velocity,
            "report_type": report_type,
            "query": query,
        })
        return _call_native_tool(tool_name, params, profile, owner=owner)

    return StructuredTool.from_function(name=tool_name, description=description, func=run)


def _call_native_tool(tool_name: str, params: dict[str, Any], profile: str, *, owner: str = "") -> Any:
    context = {**_TOOL_CONTEXT.get(), "profile": profile, "active_subagent": owner or _TOOL_CONTEXT.get().get("active_subagent")}
    spec = tool_registry._specs.get(tool_name)
    if profile == "realtime":
        return _tool_error(tool_name, "realtime profile cannot execute tools")
    if spec is None:
        return _tool_error(tool_name, "unknown tool metadata; default deny")
    if profile not in spec.allowed_profiles:
        return _tool_error(tool_name, f"tool is not allowed in {profile} profile")
    guard = context.get("guard")
    if guard is not None and hasattr(guard, "record_tool_call"):
        guard.record_tool_call(tool_name, params)
    tracer.emit(
        "deepagents_tool_call_started",
        trace_id=str(context.get("task_id") or ""),
        task_id=str(context.get("task_id") or ""),
        conversation_id=str(context.get("conversation_id") or ""),
        agent_name=str(context.get("active_subagent") or "deepagents_tool"),
        metadata={"tool_name": tool_name, "params_hash": _hashable(params), "profile": profile},
    )
    if spec.sandbox_required or spec.execution_mode == "sandbox":
        return _call_sandbox_tool(tool_name, params, profile, context, spec)
    if tool_name in list_business_tools():
        return list_business_tools()[tool_name].run(params, context)
    if tool_name == "search_memory":
        return search_memory(params, context)
    if tool_name == "search_reports":
        return search_reports(params, context)
    if tool_name == "search_strategy_candidates":
        return search_strategy_candidates(params, context)
    if tool_name == "internet_search":
        if profile != "deep":
            return {"status": "failed", "rows": [], "error": "network_search is disabled outside deep profile"}
        return {"status": "ok", "rows": [], "summary": "外部搜索工具未配置，已返回安全空结果。", "needs_verification": True}
    if tool_name == "schema_lookup":
        return {"status": "ok", "rows": [], "summary": "schema lookup 当前为安全空结果。"}
    if tool_name == "safe_read_sql":
        return {"status": "failed", "rows": [], "error": "safe_read_sql requires approved readonly query compiler"}
    raise ValueError(f"unknown native deepagents tool: {tool_name}")


def _call_sandbox_tool(tool_name: str, params: dict[str, Any], profile: str, context: dict[str, Any], spec: Any) -> Any:
    try:
        timeout_seconds = int(context.get("sandbox_timeout_seconds") or 30)
        runtime = spec.sandbox_runtime or "python"
        command = params.get("command") if isinstance(params.get("command"), list) else []
        code = str(params.get("code") or "") or _default_sandbox_code(tool_name, params)
        task = SandboxTask(
            task_id=str(context.get("task_id") or SandboxTask.new_id()),
            conversation_id=str(context.get("conversation_id") or context.get("task_id") or "sandbox"),
            tenant_id=str(context.get("tenant_id") or "default_tenant"),
            user_id=str(context.get("user_id") or "local_user"),
            shop_id=str(context.get("shop_id") or "default_shop"),
            profile=profile,
            agent_id=str(context.get("active_subagent") or "deepagents_tool"),
            tool_name=tool_name,
            runtime=runtime,
            command=command,
            code=code,
            timeout_seconds=timeout_seconds,
            resource_limits=SandboxResourceLimits(timeout_seconds=timeout_seconds),
            metadata={"params_hash": _hashable(params)},
        )
        result = SandboxClient().execute(task)
        if not result.ok:
            return _tool_error(tool_name, result.denied_reason or result.stderr or "sandbox execution failed", sandbox=result.model_dump(mode="json"))
        return {"status": "ok", "stdout": result.stdout, "stderr": result.stderr, "output_files": [item.model_dump(mode="json") for item in result.output_files], "sandbox_id": result.sandbox_id, "duration_ms": result.duration_ms}
    except SandboxUnavailableError as error:
        return _tool_error(tool_name, f"sandbox server unavailable: {error}")
    except Exception as error:
        return _tool_error(tool_name, str(error)[:500])


def _default_sandbox_code(tool_name: str, params: dict[str, Any]) -> str:
    return "print('sandbox tool executed: ' + " + json.dumps(tool_name) + ")"


def _tool_error(tool_name: str, reason: str, *, sandbox: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"status": "failed", "error_type": "ToolSandboxError", "tool_name": tool_name, "error": reason, "sandbox": sandbox or {"ok": False, "denied_reason": reason}}


def _compact_params(params: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in params.items() if value not in (None, "", [])}


def _hashable(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, default=str)[:500]

