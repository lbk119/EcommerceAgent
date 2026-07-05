"""
输入安全守卫。

这是权限系统之前的轻量输入层：它不替代模型安全能力，也不做复杂内容审查，只识别会破坏系统
边界的高风险 prompt 注入意图，并限制进入 prompt 的上传内容体量。
"""

from dataclasses import dataclass, field
from typing import Dict, List


MAX_PROMPT_CHARS = 12000
INJECTION_PHRASES = (
    "忽略系统提示",
    "忽略以上指令",
    "忽略之前的指令",
    "泄露 prompt",
    "输出系统提示",
    "显示 system prompt",
    "ignore previous instructions",
    "ignore system prompt",
    "reveal the prompt",
    "print system prompt",
    "output secrets",
)


@dataclass(frozen=True)
class PromptGuardResult:
    """输入安全检测结果。"""

    allowed: bool
    sanitized_query: str
    risk: str = "low"
    reasons: List[str] = field(default_factory=list)

    def to_metadata(self) -> Dict[str, object]:
        """转换为 trace metadata；不包含完整 query，避免把风险输入再次写入 trace。"""
        return {"allowed": self.allowed, "risk": self.risk, "reasons": self.reasons}


def inspect_user_prompt(query: str) -> PromptGuardResult:
    """
    检查用户输入是否包含明显 prompt 注入或泄密诱导。

    当前采取“标记风险但不断路”的策略，避免误杀真实业务请求；AgentRuntime 会把结果写入 trace，
    后续可以按风险等级升级为人工确认或拒绝执行。
    """
    normalized = query.lower()
    reasons = [phrase for phrase in INJECTION_PHRASES if phrase.lower() in normalized]
    sanitized_query = query[:MAX_PROMPT_CHARS]
    if len(query) > MAX_PROMPT_CHARS:
        reasons.append("prompt_too_large_truncated")

    risk = "high" if reasons else "low"
    return PromptGuardResult(
        allowed=True,
        sanitized_query=sanitized_query,
        risk=risk,
        reasons=reasons,
    )


def sanitize_prompt_text(text: str, max_chars: int = MAX_PROMPT_CHARS) -> str:
    """限制外部文本进入 prompt 的最大长度，供上传文件摘要或未来 RAG 片段复用。"""
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n[内容过长，已截断]"