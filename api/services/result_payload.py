"""Agent 最终结果载荷工具。

运行时仍兼容历史字符串返回；商业化 UI 则优先消费 structuredResult。
这个模块集中处理两者的拆包，避免 API service 到处判断对象属性。
"""

from __future__ import annotations

import json
from typing import Any


def result_markdown(result: Any) -> str:
    """返回可复制/导出的 Markdown 文本。"""
    content = getattr(result, "content", result)
    return str(content or "")


def result_structured(result: Any) -> dict[str, Any]:
    """返回前端主 UI 使用的结构化结果。"""
    structured = getattr(result, "structured_result", None)
    return structured if isinstance(structured, dict) else {}


def structured_json(result: Any) -> str | None:
    """转成 MySQL JSON 字段可写入的字符串。"""
    structured = result_structured(result)
    if not structured:
        return None
    return json.dumps(structured, ensure_ascii=False)


def parse_structured_json(value: Any) -> dict[str, Any] | None:
    """把 MySQL JSON/TEXT 返回值转成 dict，空值保持 None。"""
    if not value:
        return None
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(str(value))
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None