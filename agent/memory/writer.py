"""长期记忆写入编排。

任务结束后，result_pipeline 会把脱敏后的 query/result/lessons 传到这里。writer 负责：
- 调 extractor 生成候选；
- 按执行质量做 skip/degrade/write/review；
- 写入 MySQLMemoryStore 或人工审核表；
- 记录 memory_* task_event，方便审计和排障。
"""

from typing import Dict, List

from agent.memory.evolution_memory import append_task_event
from agent.memory.extractor import extract_memory_candidates
from agent.memory.schema import MemoryIdentity
from agent.memory.store import get_memory_store


def write_memories_after_task(identity: MemoryIdentity, task_query: str, final_result: str, lessons: list[str] = None, execution_metadata: dict | None = None) -> Dict[str, int]:
    """任务完成后的长期记忆写入入口。"""
    execution_metadata = execution_metadata or {}
    candidates = extract_memory_candidates(task_query, final_result, lessons or [])
    if not candidates:
        return {"candidates": 0, "written": 0, "review": 0, "skipped": 0, "degraded": 0}

    store = get_memory_store()
    written = 0
    review = 0
    skipped = 0
    degraded = 0
    for candidate in candidates:
        quality_action = _memory_quality_action(candidate.memory_type, execution_metadata)
        if quality_action["action"] == "skip":
            skipped += 1
            _append_candidate_quality_event(identity, candidate, execution_metadata, quality_action["reason"])
            continue
        if quality_action["action"] == "degrade":
            # workflow/Critic 存在质量问题时，工具经验可以保留但降低置信度，避免误导后续任务。
            degraded += 1
            candidate.confidence = min(candidate.confidence, 0.45)
            if quality_action["reason"] not in candidate.tags:
                candidate.tags.append(quality_action["reason"])
            _append_candidate_quality_event(identity, candidate, execution_metadata, quality_action["reason"], degraded=True)
        if candidate.requires_review:
            # 高风险记忆先进入审核队列，人工批准后才会写入 agent_memories。
            review += 1
            review_id = store.create_review(identity, candidate)
            append_task_event("memory_review_required", identity.task_id or identity.conversation_id, {
                "review_id": review_id,
                "tenant_id": identity.tenant_id,
                "user_id": identity.user_id,
                "shop_id": identity.shop_id,
                "conversation_id": identity.conversation_id,
                "memory_type": candidate.memory_type,
                "content": candidate.content,
                "tags": candidate.tags,
            })
            continue
        store.upsert_candidate(identity, candidate)
        written += 1

    return {"candidates": len(candidates), "written": written, "review": review, "skipped": skipped, "degraded": degraded}


def _memory_quality_issue_reason(execution_metadata: dict) -> str:
    """返回执行质量问题原因；空字符串表示可作为高质量结果处理。"""
    if execution_metadata.get("section_errors"):
        return "workflow_section_errors"
    if execution_metadata.get("critic_status") == "failed":
        return "critic_failed"
    return ""


def _memory_quality_action(memory_type: str, execution_metadata: dict) -> dict:
    """按记忆类型细化质量门控：偏好照写、任务经验跳过、工具经验降置信度。"""
    reason = _memory_quality_issue_reason(execution_metadata)
    if not reason:
        return {"action": "write", "reason": ""}
    if memory_type == "user_preference":
        return {"action": "write", "reason": reason}
    if memory_type == "tool_lesson":
        return {"action": "degrade", "reason": reason}
    return {"action": "skip", "reason": reason}


def _append_candidate_quality_event(identity: MemoryIdentity, candidate, execution_metadata: dict, reason: str, degraded: bool = False) -> None:
    """记录候选被跳过或降级的原因，便于诊断记忆为什么没有写入。"""
    append_task_event("memory_candidate_degraded" if degraded else "memory_candidate_skipped", identity.task_id or identity.conversation_id, {
        "reason": reason,
        "memory_type": candidate.memory_type,
        "confidence": candidate.confidence,
        "workflow_name": execution_metadata.get("workflow_name", ""),
        "attempted_workflow": execution_metadata.get("attempted_workflow", ""),
        "time_range": execution_metadata.get("time_range", {}),
        "fallback_reason": execution_metadata.get("fallback_reason", ""),
        "workflow_failed": execution_metadata.get("workflow_failed", False),
        "section_errors": execution_metadata.get("section_errors", {}),
        "critic_status": execution_metadata.get("critic_status"),
    })
