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
    candidates: list[MemoryCandidate] = []
    query = task_query.strip()
    lessons = lessons or []

    if _looks_like_preference(query):
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
    return any(pattern in text for pattern in PREFERENCE_PATTERNS) and (
        any(term in text for term in REPORT_FORMAT_TERMS) or len(text) <= 300
    )


def _normalize_preference(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return f"用户偏好/要求：{text[:500]}"


def _preference_tags(text: str) -> list[str]:
    tags = ["preference"]
    if any(term in text for term in REPORT_FORMAT_TERMS):
        tags.append("output_format")
    if "表格" in text:
        tags.append("table")
    if "结论" in text:
        tags.append("conclusion_first")
    return tags


def _is_high_risk(text: str) -> bool:
    return any(term in text for term in HIGH_RISK_TERMS)


def _looks_like_tool_lesson(text: str) -> bool:
    return "状态机" in text and "数据库" in text and "循环" in text
