"""
ExecutionResult 是 AgentRuntime 内部的执行结果结构。

对外 API 仍兼容字符串；运行时内部保留 source、workflow sections、time_range 和 structured_result，
便于前端用结构化卡片展示，同时保留 Markdown 给复制和导出。
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
    structured_result: Dict[str, Any] = field(default_factory=dict)

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

    def to_final_result(self, content: str | None = None) -> "FinalResult":
        return FinalResult(content=content or self.content, structured_result=self.structured_result)

    def section_errors(self) -> Dict[str, str]:
        """返回 workflow 中失败或缺失的 section，供记忆质量门控使用。"""
        return {
            name: content
            for name, content in self.sections.items()
            if content.strip().startswith("section_error:")
        }


@dataclass(frozen=True)
class FinalResult:
    """跨 API/service 层传递的最终结果；旧代码把它转成 str 仍得到 Markdown。"""

    content: str
    structured_result: Dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        return self.content
