"""
观测事件模型。

TraceEvent 是所有 JSONL trace 的统一数据结构。字段保持扁平，是为了方便后续：
- 用 jq/PowerShell 直接排查；
- 导入 MySQL/ClickHouse/ELK；
- 前端或运维页面按 trace_id/task_id/event_type 过滤。
"""

from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class TraceEvent:
    """
    单条平台观测事件。

    固定字段用于跨事件统一检索；metadata 用于承载事件特有信息，例如工具风险、召回数量、
    模型 profile 等。不要把完整 prompt、API key、数据库密码等敏感内容放进 metadata。
    """

    trace_id: str
    event_type: str
    task_id: Optional[str] = None
    conversation_id: Optional[str] = None
    agent_name: Optional[str] = None
    latency_ms: Optional[float] = None
    token_input: Optional[int] = None
    token_output: Optional[int] = None
    cost: Optional[float] = None
    error: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)