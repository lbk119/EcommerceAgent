"""MySQL 长期记忆存储。

MySQL 是长期记忆的主存：保存已写入记忆和待审核候选。Milvus/向量库只是可选索引层，
检索命中后仍回到 MySQL 按 tenant/user/shop 做权限过滤。
"""

import json
from functools import lru_cache
from typing import Any, Dict, List

from mysql.connector import Error, connect

from agent.memory.schema import (
    MemoryCandidate,
    MemoryIdentity,
    candidate_from_json,
    candidate_to_json,
    namespace_for,
    utc_now,
)
from agent.core.db import get_db_config

try:
    from agent_extensions.semantic_memory.milvus_store import index_memory_embedding
except Exception:
    # 向量索引是可选扩展；不可用时 MySQL LIKE 检索仍能工作。
    index_memory_embedding = None


class MySQLMemoryStore:
    """基于 MySQL 的长期记忆 store。"""

    def __init__(self):
        self.config = get_db_config()
        self._init_schema()

    def _connect(self):
        """创建 MySQL 连接。"""
        return connect(**self.config)

    def _init_schema(self) -> None:
        """幂等初始化记忆表和审核表。"""
        with self._connect() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS agent_memories (
              id VARCHAR(64) PRIMARY KEY,
              tenant_id VARCHAR(64) NOT NULL,
              user_id VARCHAR(64),
              shop_id VARCHAR(64),
              namespace VARCHAR(128) NOT NULL,
              memory_type VARCHAR(64) NOT NULL,
              key_name VARCHAR(255),
              content TEXT NOT NULL,
              summary TEXT,
              source_type VARCHAR(64),
              source_thread_id VARCHAR(64),
              source_task_id VARCHAR(64),
              confidence FLOAT DEFAULT 0.8,
              importance INT DEFAULT 3,
              tags JSON,
              expires_at DATETIME NULL,
              created_at DATETIME NOT NULL,
              updated_at DATETIME NOT NULL,
              deleted_at DATETIME NULL,
              INDEX idx_agent_memories_scope (tenant_id, user_id, shop_id, deleted_at),
              INDEX idx_agent_memories_type (memory_type, importance, confidence)
            ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
            """)
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS agent_memory_reviews (
              id VARCHAR(64) PRIMARY KEY,
              tenant_id VARCHAR(64) NOT NULL,
              user_id VARCHAR(64),
              shop_id VARCHAR(64),
              conversation_id VARCHAR(64),
              task_id VARCHAR(64),
              memory_type VARCHAR(64) NOT NULL,
              scope VARCHAR(32) NOT NULL,
              content TEXT NOT NULL,
              candidate_json JSON NOT NULL,
              status VARCHAR(32) NOT NULL DEFAULT 'pending',
              reviewer_id VARCHAR(64),
              review_comment TEXT,
              memory_id VARCHAR(64),
              created_at DATETIME NOT NULL,
              updated_at DATETIME NOT NULL,
              reviewed_at DATETIME NULL,
              INDEX idx_agent_memory_reviews_scope (tenant_id, user_id, shop_id, status),
              INDEX idx_agent_memory_reviews_status (status, created_at)
            ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
            """)
            conn.commit()

    def upsert_candidate(self, identity: MemoryIdentity, candidate: MemoryCandidate) -> str:
        """直接写入一条候选记忆，并同步可选向量索引。"""
        with self._connect() as conn:
            cursor = conn.cursor()
            memory_id = self._upsert_candidate_with_cursor(cursor, identity, candidate)
            conn.commit()
        if index_memory_embedding:
            index_memory_embedding(identity, memory_id, candidate)
        return memory_id

    def _upsert_candidate_with_cursor(self, cursor, identity: MemoryIdentity, candidate: MemoryCandidate) -> str:
        """在已有事务 cursor 中幂等写入候选。"""
        memory_id = candidate.stable_id(identity)
        now = _mysql_now()
        namespace = namespace_for(identity, candidate.scope, candidate.memory_type)
        user_id = identity.user_id if candidate.scope == "user" else None
        shop_id = identity.shop_id if candidate.scope == "shop" else None
        tags = json.dumps(candidate.tags, ensure_ascii=False)
        cursor.execute("""
        INSERT INTO agent_memories (
          id, tenant_id, user_id, shop_id, namespace, memory_type, key_name,
          content, summary, source_type, source_thread_id, source_task_id,
          confidence, importance, tags, expires_at, created_at, updated_at, deleted_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NULL)
        ON DUPLICATE KEY UPDATE
          content = VALUES(content),
          summary = VALUES(summary),
          source_type = VALUES(source_type),
          source_thread_id = VALUES(source_thread_id),
          source_task_id = VALUES(source_task_id),
          confidence = GREATEST(confidence, VALUES(confidence)),
          importance = GREATEST(importance, VALUES(importance)),
          tags = VALUES(tags),
          expires_at = VALUES(expires_at),
          updated_at = VALUES(updated_at),
          deleted_at = NULL
        """, (
            memory_id,
            identity.tenant_id,
            user_id,
            shop_id,
            namespace,
            candidate.memory_type,
            candidate.key_name,
            candidate.content,
            candidate.summary,
            candidate.source_type,
            identity.conversation_id,
            identity.task_id,
            candidate.confidence,
            candidate.importance,
            tags,
            candidate.expires_at,
            now,
            now,
        ))
        return memory_id

    def get_by_ids(self, identity: MemoryIdentity, memory_ids: List[str]) -> List[Dict[str, Any]]:
        """按 ID 批量读取记忆，并强制 tenant/user/shop 可见性过滤。"""
        if not memory_ids:
            return []
        placeholders = ",".join(["%s"] * len(memory_ids))
        params: list[Any] = [identity.tenant_id, identity.user_id, identity.shop_id, *memory_ids]
        with self._connect() as conn:
            cursor = conn.cursor(dictionary=True)
            cursor.execute(f"""
                SELECT id, tenant_id, user_id, shop_id, namespace, memory_type, key_name,
                       content, summary, source_type, source_thread_id, source_task_id,
                       confidence, importance, JSON_UNQUOTE(JSON_EXTRACT(tags, '$')) AS tags,
                       expires_at, created_at, updated_at
                FROM agent_memories
                WHERE tenant_id = %s
                  AND deleted_at IS NULL
                  AND (user_id IS NULL OR user_id = %s)
                  AND (shop_id IS NULL OR shop_id = %s)
                  AND id IN ({placeholders})
            """, params)
            rows = cursor.fetchall()
        order = {memory_id: index for index, memory_id in enumerate(memory_ids)}
        rows.sort(key=lambda row: order.get(row["id"], len(order)))
        return rows

    def search(self, identity: MemoryIdentity, query: str = "", top_k: int = 5) -> List[Dict[str, Any]]:
        """MySQL LIKE fallback 检索。"""
        params: list[Any] = [identity.tenant_id, identity.user_id, identity.shop_id]
        sql = """
        SELECT id, tenant_id, user_id, shop_id, namespace, memory_type, key_name,
               content, summary, source_type, source_thread_id, source_task_id,
               confidence, importance, JSON_UNQUOTE(JSON_EXTRACT(tags, '$')) AS tags,
               expires_at, created_at, updated_at
        FROM agent_memories
        WHERE tenant_id = %s
          AND deleted_at IS NULL
          AND (user_id IS NULL OR user_id = %s)
          AND (shop_id IS NULL OR shop_id = %s)
        """
        for term in _query_terms(query)[:6]:
            # 最多取 6 个关键词，避免用户长问题生成过长 LIKE 条件。
            sql += " AND (content LIKE %s OR summary LIKE %s OR key_name LIKE %s OR JSON_EXTRACT(tags, '$') LIKE %s)"
            like = f"%{term}%"
            params.extend([like, like, like, like])
        sql += " ORDER BY importance DESC, confidence DESC, updated_at DESC LIMIT %s"
        params.append(top_k)
        try:
            with self._connect() as conn:
                cursor = conn.cursor(dictionary=True)
                cursor.execute(sql, params)
                rows = cursor.fetchall()
                if rows or not query:
                    return rows
                # 带关键词搜不到时，回退到高重要性/高置信度记忆，保证 prompt 至少有可参考经验。
                cursor.execute("""
                    SELECT id, tenant_id, user_id, shop_id, namespace, memory_type, key_name,
                           content, summary, source_type, source_thread_id, source_task_id,
                           confidence, importance, JSON_UNQUOTE(JSON_EXTRACT(tags, '$')) AS tags,
                           expires_at, created_at, updated_at
                    FROM agent_memories
                    WHERE tenant_id = %s
                      AND deleted_at IS NULL
                      AND (user_id IS NULL OR user_id = %s)
                      AND (shop_id IS NULL OR shop_id = %s)
                    ORDER BY importance DESC, confidence DESC, updated_at DESC
                    LIMIT %s
                """, (identity.tenant_id, identity.user_id, identity.shop_id, top_k))
                return cursor.fetchall()
        except Error:
            return []

    def create_review(self, identity: MemoryIdentity, candidate: MemoryCandidate) -> str:
        """创建或更新一条待审核记忆候选。"""
        review_id = candidate.stable_id(identity)
        now = _mysql_now()
        candidate_json = candidate_to_json(candidate)
        user_id = identity.user_id if candidate.scope == "user" else None
        shop_id = identity.shop_id if candidate.scope == "shop" else None
        with self._connect() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            INSERT INTO agent_memory_reviews (
              id, tenant_id, user_id, shop_id, conversation_id, task_id,
              memory_type, scope, content, candidate_json, status,
              created_at, updated_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'pending', %s, %s)
            ON DUPLICATE KEY UPDATE
              content = VALUES(content),
              candidate_json = VALUES(candidate_json),
              status = IF(status = 'pending', 'pending', status),
              updated_at = VALUES(updated_at)
            """, (
                review_id,
                identity.tenant_id,
                user_id,
                shop_id,
                identity.conversation_id,
                identity.task_id,
                candidate.memory_type,
                candidate.scope,
                candidate.content,
                candidate_json,
                now,
                now,
            ))
            conn.commit()
        return review_id

    def list_reviews(self, identity: MemoryIdentity, status: str = "pending", limit: int = 50) -> List[Dict[str, Any]]:
        """列出当前身份可见的审核记录。"""
        params: list[Any] = [identity.tenant_id, identity.user_id, identity.shop_id]
        sql = """
        SELECT id, tenant_id, user_id, shop_id, conversation_id, task_id,
               memory_type, scope, content, status, reviewer_id, review_comment,
               memory_id, created_at, updated_at, reviewed_at
        FROM agent_memory_reviews
        WHERE tenant_id = %s
          AND (user_id IS NULL OR user_id = %s)
          AND (shop_id IS NULL OR shop_id = %s)
        """
        if status:
            sql += " AND status = %s"
            params.append(status)
        sql += " ORDER BY created_at DESC LIMIT %s"
        params.append(limit)
        with self._connect() as conn:
            cursor = conn.cursor(dictionary=True)
            cursor.execute(sql, params)
            return cursor.fetchall()

    def approve_review(self, review_id: str, reviewer_id: str = "local_user", comment: str = "") -> Dict[str, Any]:
        """批准审核候选：在同一事务中写入正式记忆并更新审核状态。"""
        now = _mysql_now()
        with self._connect() as conn:
            cursor = conn.cursor(dictionary=True)
            cursor.execute("SELECT * FROM agent_memory_reviews WHERE id = %s", (review_id,))
            review = cursor.fetchone()
            if not review:
                raise ValueError("记忆审核记录不存在")
            if review["status"] != "pending":
                raise ValueError(f"记忆审核记录已处理：{review['status']}")

            identity = MemoryIdentity(
                tenant_id=review["tenant_id"],
                user_id=review.get("user_id") or "local_user",
                shop_id=review.get("shop_id") or "default_shop",
                conversation_id=review.get("conversation_id") or "",
                task_id=review.get("task_id") or "",
            )
            candidate = candidate_from_json(review["candidate_json"])
            memory_id = self._upsert_candidate_with_cursor(cursor, identity, candidate)
            cursor.execute("""
                UPDATE agent_memory_reviews
                SET status = 'approved', reviewer_id = %s, review_comment = %s,
                    memory_id = %s, reviewed_at = %s, updated_at = %s
                WHERE id = %s
            """, (reviewer_id, comment, memory_id, now, now, review_id))
            conn.commit()
        if index_memory_embedding:
            index_memory_embedding(identity, memory_id, candidate)
        return {**review, "status": "approved", "memory_id": memory_id, "reviewer_id": reviewer_id, "review_comment": comment}

    def reject_review(self, review_id: str, reviewer_id: str = "local_user", comment: str = "") -> Dict[str, Any]:
        """拒绝审核候选，只更新审核状态，不写入正式记忆。"""
        now = _mysql_now()
        with self._connect() as conn:
            cursor = conn.cursor(dictionary=True)
            cursor.execute("SELECT * FROM agent_memory_reviews WHERE id = %s", (review_id,))
            review = cursor.fetchone()
            if not review:
                raise ValueError("记忆审核记录不存在")
            if review["status"] != "pending":
                raise ValueError(f"记忆审核记录已处理：{review['status']}")
            cursor.execute("""
                UPDATE agent_memory_reviews
                SET status = 'rejected', reviewer_id = %s, review_comment = %s,
                    reviewed_at = %s, updated_at = %s
                WHERE id = %s
            """, (reviewer_id, comment, now, now, review_id))
            conn.commit()
            return {**review, "status": "rejected", "reviewer_id": reviewer_id, "review_comment": comment}


def _query_terms(query: str) -> list[str]:
    """把用户问题切成简单 LIKE 关键词。"""
    return [term.strip() for term in query.replace("，", " ").replace("。", " ").split() if term.strip()]


def _mysql_now() -> str:
    """返回 MySQL DATETIME 友好的 UTC 字符串。"""
    return utc_now().replace("T", " ").replace("+00:00", "")


@lru_cache(maxsize=1)
def get_memory_store() -> MySQLMemoryStore:
    """进程级单例 store，避免每次任务重复建表检查。"""
    return MySQLMemoryStore()


def init_memory_store() -> MySQLMemoryStore:
    """显式初始化入口，供 FastAPI 启动或 smoke 调用。"""
    return get_memory_store()
