"""从任务输入/输出中提取长期记忆候选。

当前提取器是规则型：只提取明确用户偏好、任务复盘经验和少量工具使用经验。它不会把业务报表中的
具体订单/商品结论直接写成长期记忆，避免过期经营数据污染后续任务。
"""

import re
from typing import List

from agent.memory.schema import MemoryCandidate


PREFERENCE_PATTERNS = (
    "我喜欢", "我希望", "以后", "默认", "偏好", "不要", "尽量", "建议把", "我建议"
)
REPORT_FORMAT_TERMS = ("表格", "结论先行", "Markdown", "日报", "报告", "格式", "排版")
LESSON_TERMS = ("以后遇到", "下次", "经验", "复盘", "应该", "不要再")
HIGH_RISK_TERMS = ("价格", "库存", "安全库存", "投放预算", "生产库", "删除", "更新", "写入", "策略规则")


def extract_memory_candidates(task_query: str, final_result: str, lessons: list[str] = None) -> List[MemoryCandidate]:
    """生成记忆候选列表。

    高风险候选会标记 requires_review，由 writer 写入人工审核表，而不是直接进入长期记忆。
    """
    candidates: list[MemoryCandidate] = []
    query = task_query.strip()
    lessons = lessons or []

    if _looks_like_preference(query):
        # 用户在问题里明确表达偏好时优先沉淀为 user scope，后续只影响该用户。
        candidates.append(MemoryCandidate(
            memory_type="user_preference",
            scope="user",
            content=_normalize_preference(query),
            summary="用户明确表达的交互或输出偏好",
            confidence=0.9,
            importance=4,
            tags=_preference_tags(query),
            key_name="explicit_user_preference",
            requires_review=_is_high_risk(query),
            source_type="user_request",
        ))

    for lesson in lessons[:3]:
        if lesson and len(lesson.strip()) >= 8:
            # 反思经验默认 global scope，但高风险内容仍需要审核。
            candidates.append(MemoryCandidate(
                memory_type="task_lesson",
                scope="global",
                content=lesson.strip()[:500],
                summary="任务复盘沉淀的通用经验",
                confidence=0.75,
                importance=3,
                tags=["task_lesson"],
                requires_review=_is_high_risk(lesson),
            ))

    if _looks_like_tool_lesson(final_result):
        candidates.append(MemoryCandidate(
            memory_type="tool_lesson",
            scope="global",
            content="数据库经营分析优先使用状态机和专用分析工具，避免子 Agent 自由循环摸表。",
            summary="数据库工具使用经验",
            confidence=0.8,
            importance=3,
            tags=["database", "workflow", "loop_guard"],
        ))

    return candidates


def _looks_like_preference(text: str) -> bool:
    """判断文本是否像用户偏好/输出要求。"""
    return any(pattern in text for pattern in PREFERENCE_PATTERNS) and (
        any(term in text for term in REPORT_FORMAT_TERMS) or len(text) <= 300
    )


def _normalize_preference(text: str) -> str:
    """把用户偏好统一包装成可读句式。"""
    text = re.sub(r"\s+", " ", text).strip()
    return f"用户偏好/要求：{text[:500]}"


def _preference_tags(text: str) -> list[str]:
    """给偏好候选打标签，方便检索和前端筛选。"""
    tags = ["preference"]
    if any(term in text for term in REPORT_FORMAT_TERMS):
        tags.append("output_format")
    if "表格" in text:
        tags.append("table")
    if "结论" in text:
        tags.append("conclusion_first")
    return tags


def _is_high_risk(text: str) -> bool:
    """识别可能影响价格、库存、写库或策略规则的高风险记忆。"""
    return any(term in text for term in HIGH_RISK_TERMS)


def _looks_like_tool_lesson(text: str) -> bool:
    """识别数据库状态机/循环治理相关工具经验。"""
    return "状态机" in text and "数据库" in text and "循环" in text
