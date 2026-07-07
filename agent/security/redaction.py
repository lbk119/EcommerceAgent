"""
敏感信息脱敏工具。

权限控制解决“能不能调用工具”，但 trace、memory、task_events 还需要解决“能不能保存这段文本”。
这里先做轻量规则脱敏，覆盖 API key、Bearer token、数据库连接串、密码字段等常见泄露形态。
"""

import re
from typing import Any, Dict


SECRET_PATTERNS = (
    # key=value / key: value 形式的密钥字段。
    re.compile(r"(?i)(api[_-]?key|secret|token|password|passwd|pwd)\s*[:=]\s*['\"]?[^'\"\s,;}]+"),
    # HTTP Authorization 常见格式。
    re.compile(r"(?i)bearer\s+[a-z0-9._\-]+"),
    # 数据库或缓存连接串。
    re.compile(r"(?i)(mysql|postgresql|postgres|redis)://[^\s]+"),
    # OpenAI 风格 sk-* token。
    re.compile(r"sk-[a-zA-Z0-9]{20,}"),
)


def redact_secrets(value: Any) -> Any:
    """
    递归脱敏字符串、list 和 dict。

    函数保持输入结构不变，只替换叶子字符串中的敏感片段，方便 trace metadata 和 memory payload
    在写入前统一调用。
    """
    if isinstance(value, str):
        redacted = value
        for pattern in SECRET_PATTERNS:
            redacted = pattern.sub(_replace_secret, redacted)
        return redacted
    if isinstance(value, list):
        return [redact_secrets(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_secrets(item) for item in value)
    if isinstance(value, dict):
        return _redact_dict(value)
    return value


def _redact_dict(payload: Dict[Any, Any]) -> Dict[Any, Any]:
    """脱敏 dict：敏感 key 直接整值替换，其它字段递归处理。"""
    safe_payload: Dict[Any, Any] = {}
    for key, item in payload.items():
        if isinstance(key, str) and re.search(r"(?i)(api[_-]?key|secret|token|password|passwd|pwd)", key):
            safe_payload[key] = "[REDACTED]"
        else:
            safe_payload[key] = redact_secrets(item)
    return safe_payload


def _replace_secret(match: re.Match) -> str:
    """根据匹配形态生成可读的脱敏占位。"""
    text = match.group(0)
    if "://" in text:
        scheme = text.split("://", 1)[0]
        return f"{scheme}://[REDACTED]"
    if text.lower().startswith("bearer "):
        return "Bearer [REDACTED]"
    if text.startswith("sk-"):
        return "sk-[REDACTED]"
    separator = "=" if "=" in text else ":"
    key = text.split(separator, 1)[0].strip()
    return f"{key}{separator} [REDACTED]"