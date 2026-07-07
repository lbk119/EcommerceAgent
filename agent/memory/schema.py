"""长期记忆数据结构。

长期记忆分为身份（MemoryIdentity）和候选（MemoryCandidate）：
- Identity 决定记忆属于哪个 tenant/user/shop/conversation/task；
- Candidate 描述要写入的内容、作用域、置信度、重要性、标签和是否需要人工审核。

这些结构会被 MySQLMemoryStore、extractor、writer、retriever 共同使用，因此保持轻量且可 JSON 化。
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional
import hashlib
import json


@dataclass(frozen=True)
class MemoryIdentity:
    """一次任务/会话的记忆身份上下文。"""

    tenant_id: str = "default_tenant"
    user_id: str = "local_user"
    shop_id: str = "default_shop"
    conversation_id: str = ""
    task_id: str = ""


@dataclass
class MemoryCandidate:
    """待写入或待审核的记忆候选。"""

    # 记忆类型，例如 user_preference、task_lesson、tool_lesson。
    memory_type: str
    # 记忆正文。写入前由上游做脱敏和质量门控。
    content: str
    # 作用域：user 只给当前用户；shop 给当前店铺；global 给当前租户。
    scope: str = "user"
    confidence: float = 0.8
    importance: int = 3
    tags: list[str] = field(default_factory=list)
    key_name: Optional[str] = None
    summary: Optional[str] = None
    expires_at: Optional[str] = None
    requires_review: bool = False
    source_type: str = "task_result"

    def stable_id(self, identity: MemoryIdentity) -> str:
        """生成稳定 ID，用于幂等 upsert。

        同一租户/作用域/类型/key/content 会得到同一个 ID，避免相同偏好或经验被反复写入多条。
        """
        raw = json.dumps({
            "tenant_id": identity.tenant_id,
            "user_id": identity.user_id if self.scope == "user" else None,
            "shop_id": identity.shop_id if self.scope == "shop" else None,
            "scope": self.scope,
            "memory_type": self.memory_type,
            "key_name": self.key_name,
            "content": self.content,
        }, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def utc_now() -> str:
    """返回去掉微秒的 UTC ISO 时间，便于 MySQL 和 JSONL 共用。"""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def namespace_for(identity: MemoryIdentity, scope: str, memory_type: str) -> str:
    """按作用域生成记忆 namespace，便于未来向量库/检索按命名空间隔离。"""
    if scope == "user":
        return f"tenant/{identity.tenant_id}/user/{identity.user_id}/{memory_type}"
    if scope == "shop":
        return f"tenant/{identity.tenant_id}/shop/{identity.shop_id}/{memory_type}"
    return f"tenant/{identity.tenant_id}/global/{memory_type}"


def candidate_to_json(candidate: MemoryCandidate) -> str:
    """把候选序列化为 JSON，供人工审核表保存原始候选。"""
    return json.dumps({
        "memory_type": candidate.memory_type,
        "content": candidate.content,
        "scope": candidate.scope,
        "confidence": candidate.confidence,
        "importance": candidate.importance,
        "tags": candidate.tags,
        "key_name": candidate.key_name,
        "summary": candidate.summary,
        "expires_at": candidate.expires_at,
        "requires_review": candidate.requires_review,
        "source_type": candidate.source_type,
    }, ensure_ascii=False)


def candidate_from_json(payload: str) -> MemoryCandidate:
    """从审核表中的 JSON 还原 MemoryCandidate。"""
    data = json.loads(payload)
    return MemoryCandidate(**data)
