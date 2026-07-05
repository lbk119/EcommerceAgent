"""
ExecutionResult 是 AgentRuntime 内部的执行结果结构。

对外 API 仍返回字符串；运行时内部保留 source、workflow sections 和 time_range，便于 Critic
修正、trace 和后续审计不丢失 deterministic workflow 的执行上下文。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict


@dataclass(frozen=True)
class ExecutionResult:
    """一次执行的结构化结果。"""

    content: str
    source: str
    workflow_name: str = ""
    sections: Dict[str, str] = field(default_factory=dict)
    time_range: Dict[str, Any] = field(default_factory=dict)
    workflow_definition: Dict[str, Any] = field(default_factory=dict)
    output_requirements: str = ""
    fallback_reason: str = ""
    attempted_workflow: str = ""
    workflow_failed: bool = False

    @classmethod
    def from_deepagent(cls, content: str, *, fallback_reason: str = "", attempted_workflow: str = "", workflow_failed: bool = False) -> "ExecutionResult":
        return cls(
            content=content,
            source="deepagent",
            fallback_reason=fallback_reason,
            attempted_workflow=attempted_workflow,
            workflow_failed=workflow_failed,
        )

    def to_text(self) -> str:
        return self.content

    def section_errors(self) -> Dict[str, str]:
        """返回 workflow 中失败或缺失的 section，供记忆质量门控使用。"""
        return {
            name: content
            for name, content in self.sections.items()
            if content.strip().startswith("section_error:")
        }
