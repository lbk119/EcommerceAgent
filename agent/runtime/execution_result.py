"""AgentRuntime 内部和 API 边界共享的执行结果对象。

`ExecutionResult` 保留 workflow、sections、fallback 和 structured_result 等运行时细节；
`FinalResult` 是跨 API/service 边界的稳定结果，字符串化后仍兼容旧前端。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ExecutionResult:
    """AgentRuntime 产生的内部结构化执行结果。"""

    content: str
    source: str
    workflow_name: str = ""
    sections: dict[str, str] = field(default_factory=dict)
    time_range: dict[str, Any] = field(default_factory=dict)
    workflow_definition: dict[str, Any] = field(default_factory=dict)
    output_requirements: str = ""
    fallback_reason: str = ""
    attempted_workflow: str = ""
    workflow_failed: bool = False
    structured_result: dict[str, Any] = field(default_factory=dict)

    def to_text(self) -> str:
        """返回纯文本内容，兼容旧调用方。"""
        return self.content

    def to_final_result(self, content: str | None = None) -> "FinalResult":
        """转换为跨 API/service 边界传递的最终结果。"""
        return FinalResult(content=content or self.content, structured_result=self.structured_result)

    def section_errors(self) -> dict[str, str]:
        """提取 section 级错误，供 trace 和前端报告展示。"""
        return {
            name: content
            for name, content in self.sections.items()
            if content.strip().startswith("section_error:")
        }


@dataclass(frozen=True)
class FinalResult:
    """跨 API/service 边界传递的最终结果。"""

    content: str
    structured_result: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        return self.content
