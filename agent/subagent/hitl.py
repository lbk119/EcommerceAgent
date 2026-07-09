"""Human-in-the-loop 中断策略。

本文件只生成 deepagents/LangGraph `interrupt_on` 配置，不创建 human_approval subagent。
高风险工具由图执行层暂停，用户 approve/edit/reject 后用同一个 thread_id/checkpointer 恢复。
"""

from __future__ import annotations

from typing import Any

from agent.subagent.config import get_deepagents_profile


HIGH_RISK_TOOLS = {
    "write_report_file",
    "edit_report_file",
    "delete_file",
    "shell_execute",
    "external_write_api",
    "adjust_campaign_budget",
    "create_replenishment_order",
    "update_price",
    "risky_sql",
    "run_database_workflow",
}


def interrupt_on_for_profile(profile: str) -> dict[str, bool | dict[str, Any]]:
    """返回当前 profile 需要 HITL 审批的高风险工具集合。"""
    config = get_deepagents_profile(profile)
    if not config.enable_hitl or config.name == "realtime":
        return {}
    if config.name == "standard":
        return {name: True for name in HIGH_RISK_TOOLS if name not in {"shell_execute", "external_write_api"}}
    return {name: True for name in HIGH_RISK_TOOLS}


def hitl_required_for_tool(tool_name: str, profile: str) -> bool:
    """判断某个工具在当前 profile 下是否必须触发人工审批。"""
    return tool_name in interrupt_on_for_profile(profile)