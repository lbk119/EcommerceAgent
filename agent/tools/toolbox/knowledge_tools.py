"""知识库、历史报告和策略候选检索工具。

当前实现返回安全空结果，保留 deepagents-native 工具契约和 trace 形态；后续接入持久化 memory store、
报告索引或向量检索时，只需要替换这些 runner 的内部实现。
"""

from __future__ import annotations

from typing import Any


def search_memory(params: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    """检索长期记忆；未配置索引时返回可审计的安全空结果。"""
    return {"status": "ok", "rows": [], "summary": "知识库检索当前为安全空结果。"}


def search_reports(params: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    """检索历史报告；未配置索引时返回安全空结果。"""
    return {"status": "ok", "rows": [], "summary": "历史报告检索当前为安全空结果。"}


def search_strategy_candidates(params: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    """检索历史策略候选；未配置索引时返回安全空结果。"""
    return {"status": "ok", "rows": [], "summary": "历史策略候选当前为安全空结果。"}