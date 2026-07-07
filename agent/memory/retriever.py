"""长期记忆检索入口。

优先使用可选语义向量检索（agent_extensions.semantic_memory），不可用时回退 MySQL LIKE 检索。
调用方不需要关心底层检索方式，只会拿到同一结构的 memory dict 列表。
"""

from typing import Dict, List

from agent.memory.schema import MemoryIdentity
from agent.memory.store import get_memory_store

try:
    from agent_extensions.semantic_memory.milvus_store import search_memory_embeddings
except Exception:
    # semantic memory 是可选扩展；导入失败不能影响主 Agent 启动和基础 MySQL 记忆能力。
    search_memory_embeddings = None


def retrieve_long_term_memories(identity: MemoryIdentity, query: str, top_k: int = 5) -> List[Dict]:
    """按当前身份和 query 检索长期记忆。"""
    store = get_memory_store()
    if search_memory_embeddings:
        # 向量库只返回 memory_id/score，真实内容仍从 MySQL 读取，保证权限过滤和主存一致。
        hits = search_memory_embeddings(identity, query, top_k=top_k)
        memory_ids = [hit["memory_id"] for hit in hits if hit.get("memory_id")]
        if memory_ids:
            memories = store.get_by_ids(identity, memory_ids)
            scores = {hit["memory_id"]: hit.get("score", 0) for hit in hits}
            for memory in memories:
                memory["score"] = scores.get(memory["id"], 0)
                memory["retrieval"] = "milvus"
            return memories[:top_k]
    memories = store.search(identity, query, top_k=top_k)
    for memory in memories:
        memory["retrieval"] = "mysql_like"
    return memories[:top_k]


def format_long_term_memory_context(memories: List[Dict]) -> str:
    """把记忆列表渲染成可追加到 Agent prompt 的上下文片段。"""
    if not memories:
        return ""

    lines = [
        "\n    【相关长期记忆】",
        "    以下内容只作为偏好、经验和策略参考；若与数据库实时经营数据冲突，以实时数据为准。",
    ]
    for index, memory in enumerate(memories, start=1):
        memory_type = memory.get("memory_type") or "task_lesson"
        content = memory.get("content") or memory.get("summary") or ""
        confidence = memory.get("confidence") or memory.get("score") or 0
        lines.append(f"    {index}. 类型：{memory_type}；置信度：{confidence}；内容：{content}")
    lines.append("    使用规则：不要泄露其他用户、店铺或租户的记忆；低置信度记忆需要核验。")
    return "\n".join(lines)
