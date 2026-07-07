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
    """一次执行的结构化结果。

    content 是兼容旧链路的 Markdown 文本；其它字段用于标记结果来源、workflow 结构化产物和
    fallback 状态。这样 API 可以继续把结果当字符串保存，同时前端能优先展示 structured_result。
    """

    # 最终 Markdown 文本，仍是复制/导出和旧接口的主结果。
    content: str
    # 结果来源，例如 deepagent、workflow_fast、workflow、boundary。
    source: str
    # 命中的 workflow 名称；DeepAgent 直接执行时为空。
    workflow_name: str = ""
    # workflow 每个 section 的原始文本，便于质量门控识别 section_error。
    sections: Dict[str, str] = field(default_factory=dict)
    # 本次查询解析出的业务时间范围，例如最近 30 天、618 活动期等。
    time_range: Dict[str, Any] = field(default_factory=dict)
    # 计划注册表中的 workflow 定义快照，便于 trace 和诊断还原执行路径。
    workflow_definition: Dict[str, Any] = field(default_factory=dict)
    # 输出格式要求，供 reducer/fast polish 参考。
    output_requirements: str = ""
    # 从 workflow 回落 DeepAgent 的原因。
    fallback_reason: str = ""
    attempted_workflow: str = ""
    workflow_failed: bool = False
    # 给前端结构化卡片使用的结果对象，例如 conclusion/evidence/actions/risks。
    structured_result: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_deepagent(cls, content: str, *, fallback_reason: str = "", attempted_workflow: str = "", workflow_failed: bool = False) -> "ExecutionResult":
        """把 DeepAgent 字符串结果包装成统一 ExecutionResult。"""
        return cls(
            content=content,
            source="deepagent",
            fallback_reason=fallback_reason,
            attempted_workflow=attempted_workflow,
            workflow_failed=workflow_failed,
        )

    def to_text(self) -> str:
        """兼容旧调用方：只需要文本时返回 Markdown content。"""
        return self.content

    def to_final_result(self, content: str | None = None) -> "FinalResult":
        """转换为跨 service/API 层传递的最终结果对象。"""
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
        """旧代码把 FinalResult 转字符串时仍得到 Markdown。"""
        return self.content
