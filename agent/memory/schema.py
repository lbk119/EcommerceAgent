from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional
import hashlib
import json


@dataclass(frozen=True)
class MemoryIdentity:
    tenant_id: str = "default_tenant"
    user_id: str = "local_user"
    shop_id: str = "default_shop"
    conversation_id: str = ""
    task_id: str = ""


@dataclass
class MemoryCandidate:
    memory_type: str
    content: str
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
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def namespace_for(identity: MemoryIdentity, scope: str, memory_type: str) -> str:
    if scope == "user":
        return f"tenant/{identity.tenant_id}/user/{identity.user_id}/{memory_type}"
    if scope == "shop":
        return f"tenant/{identity.tenant_id}/shop/{identity.shop_id}/{memory_type}"
    return f"tenant/{identity.tenant_id}/global/{memory_type}"


def candidate_to_json(candidate: MemoryCandidate) -> str:
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
    data = json.loads(payload)
    return MemoryCandidate(**data)
