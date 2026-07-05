import os
from importlib import import_module
from typing import Any, Dict, List

from mysql.connector import connect

from agent.memory.embedding import EmbeddingUnavailable, embed_text, embedding_dimension, embedding_model_name
from agent.memory.schema import MemoryCandidate, MemoryIdentity, namespace_for, utc_now
from agent.core.db import get_db_config


class VectorMemoryUnavailable(RuntimeError):
    pass


def semantic_memory_enabled() -> bool:
    return os.getenv("MEMORY_VECTOR_BACKEND", "milvus").lower() == "milvus"


def vector_search_enabled() -> bool:
    """
    控制“任务前置召回”是否启用 Milvus/BGE 向量检索。

    本地 BGE/部分向量依赖可能在底层原生库初始化失败时直接终止 Python 进程，普通 try/except 捕获不到。
    因此读路径默认关闭，必须显式设置 MEMORY_VECTOR_SEARCH_ENABLED=true 才会进入向量召回；
    未开启时仍会使用 MySQL 记忆检索兜底，保证 Agent 主流程不被可选增强能力拖垮。
    """
    return os.getenv("MEMORY_VECTOR_SEARCH_ENABLED", "false").lower() in {"1", "true", "yes", "on"}


def vector_write_enabled() -> bool:
    """
    控制任务结束后的向量索引写入。

    写路径同样默认关闭，避免任务已经产出结果后，因为 embedding/Milvus 本地环境异常导致进程退出。
    生产环境确认 BGE/Milvus 稳定后，再通过 MEMORY_VECTOR_WRITE_ENABLED=true 打开。
    """
    return os.getenv("MEMORY_VECTOR_WRITE_ENABLED", "false").lower() in {"1", "true", "yes", "on"}


def index_memory_embedding(identity: MemoryIdentity, memory_id: str, candidate: MemoryCandidate) -> None:
    if not semantic_memory_enabled() or not vector_write_enabled():
        return
    try:
        embedding_text = _embedding_text(candidate)
        vector = embed_text(embedding_text)
        _upsert_milvus(identity, memory_id, candidate, vector)
        _upsert_embedding_metadata(memory_id, embedding_text)
    except Exception as error:
        print(f"[MemoryVector] skip embedding index for {memory_id}: {error}")


def search_memory_embeddings(identity: MemoryIdentity, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
    if not semantic_memory_enabled() or not vector_search_enabled() or not query.strip():
        return []
    try:
        vector = embed_text(query)
        collection = _get_collection()
        expr = _identity_expr(identity)
        results = collection.search(
            data=[vector],
            anns_field="embedding",
            param={"metric_type": "COSINE", "params": _search_params()},
            limit=top_k,
            expr=expr,
            output_fields=["memory_id"],
        )
        hits = []
        for hit in results[0]:
            hits.append({"memory_id": hit.entity.get("memory_id"), "score": float(hit.score)})
        return hits
    except Exception as error:
        print(f"[MemoryVector] semantic search unavailable: {error}")
        return []


def _embedding_text(candidate: MemoryCandidate) -> str:
    tags = " ".join(candidate.tags)
    return "\n".join([
        f"memory_type: {candidate.memory_type}",
        f"scope: {candidate.scope}",
        f"key: {candidate.key_name or ''}",
        f"summary: {candidate.summary or ''}",
        f"content: {candidate.content}",
        f"tags: {tags}",
    ])


def _get_collection():
    try:
        pymilvus = import_module("pymilvus")
    except ImportError as error:
        raise VectorMemoryUnavailable("pymilvus is not installed") from error

    Collection = pymilvus.Collection
    CollectionSchema = pymilvus.CollectionSchema
    DataType = pymilvus.DataType
    FieldSchema = pymilvus.FieldSchema
    connections = pymilvus.connections
    utility = pymilvus.utility

    alias = "ecommerce_memory"
    host = os.getenv("MILVUS_HOST", "localhost")
    port = os.getenv("MILVUS_PORT", "19530")
    user = os.getenv("MILVUS_USER") or "admin"
    password = os.getenv("MILVUS_PASSWORD") or "admin"
    database = os.getenv("MILVUS_DATABASE") or "default"
    collection_name = os.getenv("MEMORY_MILVUS_COLLECTION", "agent_memories")
    kwargs = {"alias": alias, "host": host, "port": port, "db_name": database}
    if user:
        kwargs.update({"user": user, "password": password})
    connections.connect(**kwargs)

    if not utility.has_collection(collection_name, using=alias):
        fields = [
            FieldSchema(name="memory_id", dtype=DataType.VARCHAR, is_primary=True, max_length=64),
            FieldSchema(name="tenant_id", dtype=DataType.VARCHAR, max_length=64),
            FieldSchema(name="user_id", dtype=DataType.VARCHAR, max_length=64),
            FieldSchema(name="shop_id", dtype=DataType.VARCHAR, max_length=64),
            FieldSchema(name="embedding", dtype=DataType.FLOAT_VECTOR, dim=embedding_dimension()),
        ]
        schema = CollectionSchema(fields=fields, description="EcommerceAgent long-term memory embeddings")
        collection = Collection(collection_name, schema=schema, using=alias)
        collection.create_index(
            field_name="embedding",
            index_params={"index_type": os.getenv("MILVUS_INDEX_TYPE", "HNSW"), "metric_type": "COSINE", "params": {"M": 16, "efConstruction": 200}},
        )
    else:
        collection = Collection(collection_name, using=alias)
    collection.load()
    return collection


def _upsert_milvus(identity: MemoryIdentity, memory_id: str, candidate: MemoryCandidate, vector: List[float]) -> None:
    collection = _get_collection()
    vector_identity = _vector_identity(identity, candidate)
    collection.upsert([{
        "memory_id": memory_id,
        "tenant_id": vector_identity["tenant_id"],
        "user_id": vector_identity["user_id"],
        "shop_id": vector_identity["shop_id"],
        "embedding": vector,
    }])
    collection.flush()


def _vector_identity(identity: MemoryIdentity, candidate: MemoryCandidate) -> Dict[str, str]:
    if candidate.scope == "global":
        return {"tenant_id": identity.tenant_id, "user_id": "", "shop_id": ""}
    if candidate.scope == "shop":
        return {"tenant_id": identity.tenant_id, "user_id": "", "shop_id": identity.shop_id or ""}
    return {"tenant_id": identity.tenant_id, "user_id": identity.user_id or "", "shop_id": identity.shop_id or ""}


def _upsert_embedding_metadata(memory_id: str, embedding_text: str) -> None:
    now = utc_now().replace("T", " ").replace("+00:00", "")
    with connect(**get_db_config()) as conn:
        cursor = conn.cursor()
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS agent_memory_embeddings (
          memory_id VARCHAR(64) PRIMARY KEY,
          embedding_model VARCHAR(128),
          embedding_text TEXT,
          vector_backend VARCHAR(64),
          external_id VARCHAR(128),
          updated_at DATETIME NOT NULL
        ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
        """)
        cursor.execute("""
        INSERT INTO agent_memory_embeddings (
          memory_id, embedding_model, embedding_text, vector_backend, external_id, updated_at
        ) VALUES (%s, %s, %s, 'milvus', %s, %s)
        ON DUPLICATE KEY UPDATE
          embedding_model = VALUES(embedding_model),
          embedding_text = VALUES(embedding_text),
          vector_backend = VALUES(vector_backend),
          external_id = VALUES(external_id),
          updated_at = VALUES(updated_at)
        """, (memory_id, embedding_model_name(), embedding_text, memory_id, now))
        conn.commit()


def _search_params() -> Dict[str, int]:
    index_type = os.getenv("MILVUS_INDEX_TYPE", "HNSW").upper()
    if index_type == "HNSW":
        return {"ef": int(os.getenv("MILVUS_EF", "64"))}
    return {"nprobe": int(os.getenv("MILVUS_NPROBE", "10"))}


def _identity_expr(identity: MemoryIdentity) -> str:
    tenant = _escape(identity.tenant_id)
    user = _escape(identity.user_id or "")
    shop = _escape(identity.shop_id or "")
    return f'tenant_id == "{tenant}" and (user_id == "" or user_id == "{user}") and (shop_id == "" or shop_id == "{shop}")'


def _escape(value: str) -> str:
    return value.replace('\\', '\\\\').replace('"', '\\"')
