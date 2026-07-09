"""LangGraph checkpointer 适配。

deepagents HITL 和长任务恢复需要稳定的 thread_id/checkpoint_ns。本文件优先构造 Redis-backed checkpointer，
Redis 不可用时在本地开发回退 MemorySaver；生产环境应配置持久化 checkpointer。
"""

import json
import os
import time
from typing import Any, Iterator, Sequence

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (
    BaseCheckpointSaver,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
    ChannelVersions,
    WRITES_IDX_MAP,
    get_checkpoint_id,
    get_checkpoint_metadata,
)
from langgraph.checkpoint.memory import MemorySaver


class RedisCheckpointSaver(BaseCheckpointSaver):
    """基于 Redis 的 LangGraph checkpointer，用于短中期会话状态和 HITL resume。"""

    def __init__(self, redis_url: str, prefix: str = "ecommerce_agent:checkpoint"):
        super().__init__()
        from redis import Redis
        from redis.asyncio import Redis as AsyncRedis

        self.redis = Redis.from_url(redis_url, decode_responses=False)
        self.async_redis = AsyncRedis.from_url(redis_url, decode_responses=False)
        self.prefix = prefix.rstrip(":")
        self.redis.ping()

    def _checkpoint_key(self, thread_id: str, checkpoint_ns: str, checkpoint_id: str) -> str:
        return f"{self.prefix}:ckpt:{thread_id}:{checkpoint_ns}:{checkpoint_id}"

    def _checkpoint_index_key(self, thread_id: str, checkpoint_ns: str) -> str:
        return f"{self.prefix}:index:{thread_id}:{checkpoint_ns}"

    def _blob_key(self, thread_id: str, checkpoint_ns: str, channel: str, version: Any) -> str:
        return f"{self.prefix}:blob:{thread_id}:{checkpoint_ns}:{channel}:{version}"

    def _writes_key(self, thread_id: str, checkpoint_ns: str, checkpoint_id: str) -> str:
        return f"{self.prefix}:writes:{thread_id}:{checkpoint_ns}:{checkpoint_id}"

    def put(self, config: RunnableConfig, checkpoint: Checkpoint, metadata: CheckpointMetadata, new_versions: ChannelVersions) -> RunnableConfig:
        checkpoint_copy = checkpoint.copy()
        thread_id = config["configurable"]["thread_id"]
        checkpoint_ns = config["configurable"].get("checkpoint_ns", "")
        checkpoint_id = checkpoint["id"]
        parent_checkpoint_id = config["configurable"].get("checkpoint_id")
        channel_values: dict[str, Any] = checkpoint_copy.pop("channel_values", {})  # type: ignore[misc]

        pipe = self.redis.pipeline()
        for channel, version in new_versions.items():
            serializer_type, payload = self.serde.dumps_typed(channel_values[channel]) if channel in channel_values else ("empty", b"")
            pipe.hset(self._blob_key(thread_id, checkpoint_ns, channel, version), mapping={
                "type": serializer_type,
                "payload": payload,
            })

        checkpoint_type, checkpoint_payload = self.serde.dumps_typed(checkpoint_copy)
        metadata_type, metadata_payload = self.serde.dumps_typed(get_checkpoint_metadata(config, metadata))
        pipe.hset(self._checkpoint_key(thread_id, checkpoint_ns, checkpoint_id), mapping={
            "checkpoint_type": checkpoint_type,
            "checkpoint_payload": checkpoint_payload,
            "metadata_type": metadata_type,
            "metadata_payload": metadata_payload,
            "parent_checkpoint_id": parent_checkpoint_id or "",
        })
        pipe.zadd(self._checkpoint_index_key(thread_id, checkpoint_ns), {checkpoint_id: time.time_ns()})
        pipe.execute()
        return {"configurable": {"thread_id": thread_id, "checkpoint_ns": checkpoint_ns, "checkpoint_id": checkpoint_id}}

    async def aput(self, config: RunnableConfig, checkpoint: Checkpoint, metadata: CheckpointMetadata, new_versions: ChannelVersions) -> RunnableConfig:
        checkpoint_copy = checkpoint.copy()
        thread_id = config["configurable"]["thread_id"]
        checkpoint_ns = config["configurable"].get("checkpoint_ns", "")
        checkpoint_id = checkpoint["id"]
        parent_checkpoint_id = config["configurable"].get("checkpoint_id")
        channel_values: dict[str, Any] = checkpoint_copy.pop("channel_values", {})  # type: ignore[misc]

        pipe = self.async_redis.pipeline()
        for channel, version in new_versions.items():
            serializer_type, payload = self.serde.dumps_typed(channel_values[channel]) if channel in channel_values else ("empty", b"")
            pipe.hset(self._blob_key(thread_id, checkpoint_ns, channel, version), mapping={
                "type": serializer_type,
                "payload": payload,
            })

        checkpoint_type, checkpoint_payload = self.serde.dumps_typed(checkpoint_copy)
        metadata_type, metadata_payload = self.serde.dumps_typed(get_checkpoint_metadata(config, metadata))
        pipe.hset(self._checkpoint_key(thread_id, checkpoint_ns, checkpoint_id), mapping={
            "checkpoint_type": checkpoint_type,
            "checkpoint_payload": checkpoint_payload,
            "metadata_type": metadata_type,
            "metadata_payload": metadata_payload,
            "parent_checkpoint_id": parent_checkpoint_id or "",
        })
        pipe.zadd(self._checkpoint_index_key(thread_id, checkpoint_ns), {checkpoint_id: time.time_ns()})
        await pipe.execute()
        return {"configurable": {"thread_id": thread_id, "checkpoint_ns": checkpoint_ns, "checkpoint_id": checkpoint_id}}

    def get_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        thread_id = config["configurable"]["thread_id"]
        checkpoint_ns = config["configurable"].get("checkpoint_ns", "")
        checkpoint_id = get_checkpoint_id(config)
        if not checkpoint_id:
            latest = self.redis.zrevrange(self._checkpoint_index_key(thread_id, checkpoint_ns), 0, 0)
            if not latest:
                return None
            checkpoint_id = latest[0].decode("utf-8") if isinstance(latest[0], bytes) else latest[0]

        row = self.redis.hgetall(self._checkpoint_key(thread_id, checkpoint_ns, checkpoint_id))
        if not row:
            return None

        checkpoint = self.serde.loads_typed((_decode(row[b"checkpoint_type"]), row[b"checkpoint_payload"]))
        metadata = self.serde.loads_typed((_decode(row[b"metadata_type"]), row[b"metadata_payload"]))
        parent_checkpoint_id = _decode(row.get(b"parent_checkpoint_id", b""))
        pending_writes = self._load_writes(thread_id, checkpoint_ns, checkpoint_id)
        checkpoint_with_values = {
            **checkpoint,
            "channel_values": self._load_blobs(thread_id, checkpoint_ns, checkpoint.get("channel_versions", {})),
        }
        return CheckpointTuple(
            config={"configurable": {"thread_id": thread_id, "checkpoint_ns": checkpoint_ns, "checkpoint_id": checkpoint_id}},
            checkpoint=checkpoint_with_values,
            metadata=metadata,
            pending_writes=pending_writes,
            parent_config={"configurable": {"thread_id": thread_id, "checkpoint_ns": checkpoint_ns, "checkpoint_id": parent_checkpoint_id}} if parent_checkpoint_id else None,
        )

    async def aget_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        thread_id = config["configurable"]["thread_id"]
        checkpoint_ns = config["configurable"].get("checkpoint_ns", "")
        checkpoint_id = get_checkpoint_id(config)
        if not checkpoint_id:
            latest = await self.async_redis.zrevrange(self._checkpoint_index_key(thread_id, checkpoint_ns), 0, 0)
            if not latest:
                return None
            checkpoint_id = latest[0].decode("utf-8") if isinstance(latest[0], bytes) else latest[0]

        row = await self.async_redis.hgetall(self._checkpoint_key(thread_id, checkpoint_ns, checkpoint_id))
        if not row:
            return None

        checkpoint = self.serde.loads_typed((_decode(row[b"checkpoint_type"]), row[b"checkpoint_payload"]))
        metadata = self.serde.loads_typed((_decode(row[b"metadata_type"]), row[b"metadata_payload"]))
        parent_checkpoint_id = _decode(row.get(b"parent_checkpoint_id", b""))
        pending_writes = await self._aload_writes(thread_id, checkpoint_ns, checkpoint_id)
        checkpoint_with_values = {
            **checkpoint,
            "channel_values": await self._aload_blobs(thread_id, checkpoint_ns, checkpoint.get("channel_versions", {})),
        }
        return CheckpointTuple(
            config={"configurable": {"thread_id": thread_id, "checkpoint_ns": checkpoint_ns, "checkpoint_id": checkpoint_id}},
            checkpoint=checkpoint_with_values,
            metadata=metadata,
            pending_writes=pending_writes,
            parent_config={"configurable": {"thread_id": thread_id, "checkpoint_ns": checkpoint_ns, "checkpoint_id": parent_checkpoint_id}} if parent_checkpoint_id else None,
        )

    def list(self, config: RunnableConfig | None, *, filter: dict[str, Any] | None = None, before: RunnableConfig | None = None, limit: int | None = None) -> Iterator[CheckpointTuple]:
        if not config:
            return iter(())
        thread_id = config["configurable"]["thread_id"]
        checkpoint_ns = config["configurable"].get("checkpoint_ns", "")
        max_id = get_checkpoint_id(before) if before else None
        checkpoint_ids = self.redis.zrevrange(self._checkpoint_index_key(thread_id, checkpoint_ns), 0, -1)
        yielded = 0
        for raw_checkpoint_id in checkpoint_ids:
            checkpoint_id = raw_checkpoint_id.decode("utf-8") if isinstance(raw_checkpoint_id, bytes) else raw_checkpoint_id
            if max_id and checkpoint_id >= max_id:
                continue
            item = self.get_tuple({"configurable": {"thread_id": thread_id, "checkpoint_ns": checkpoint_ns, "checkpoint_id": checkpoint_id}})
            if not item:
                continue
            if filter and not all(item.metadata.get(key) == value for key, value in filter.items()):
                continue
            yield item
            yielded += 1
            if limit and yielded >= limit:
                break

    async def alist(self, config: RunnableConfig | None, *, filter: dict[str, Any] | None = None, before: RunnableConfig | None = None, limit: int | None = None):
        if not config:
            return
        thread_id = config["configurable"]["thread_id"]
        checkpoint_ns = config["configurable"].get("checkpoint_ns", "")
        max_id = get_checkpoint_id(before) if before else None
        checkpoint_ids = await self.async_redis.zrevrange(self._checkpoint_index_key(thread_id, checkpoint_ns), 0, -1)
        yielded = 0
        for raw_checkpoint_id in checkpoint_ids:
            checkpoint_id = raw_checkpoint_id.decode("utf-8") if isinstance(raw_checkpoint_id, bytes) else raw_checkpoint_id
            if max_id and checkpoint_id >= max_id:
                continue
            item = await self.aget_tuple({"configurable": {"thread_id": thread_id, "checkpoint_ns": checkpoint_ns, "checkpoint_id": checkpoint_id}})
            if not item:
                continue
            if filter and not all(item.metadata.get(key) == value for key, value in filter.items()):
                continue
            yield item
            yielded += 1
            if limit and yielded >= limit:
                break

    def put_writes(self, config: RunnableConfig, writes: Sequence[tuple[str, Any]], task_id: str, task_path: str = "") -> None:
        thread_id = config["configurable"]["thread_id"]
        checkpoint_ns = config["configurable"].get("checkpoint_ns", "")
        checkpoint_id = config["configurable"]["checkpoint_id"]
        key = self._writes_key(thread_id, checkpoint_ns, checkpoint_id)
        pipe = self.redis.pipeline()
        for index, (channel, value) in enumerate(writes):
            write_index = WRITES_IDX_MAP.get(channel, index)
            field = f"{task_id}:{write_index}"
            if write_index >= 0 and self.redis.hexists(key, field):
                continue
            serializer_type, payload = self.serde.dumps_typed(value)
            pipe.hset(key, field, json.dumps({
                "task_id": task_id,
                "channel": channel,
                "type": serializer_type,
                "payload": payload.decode("latin1"),
                "task_path": task_path,
            }, ensure_ascii=False))
        pipe.execute()

    async def aput_writes(self, config: RunnableConfig, writes: Sequence[tuple[str, Any]], task_id: str, task_path: str = "") -> None:
        thread_id = config["configurable"]["thread_id"]
        checkpoint_ns = config["configurable"].get("checkpoint_ns", "")
        checkpoint_id = config["configurable"]["checkpoint_id"]
        key = self._writes_key(thread_id, checkpoint_ns, checkpoint_id)
        pipe = self.async_redis.pipeline()
        for index, (channel, value) in enumerate(writes):
            write_index = WRITES_IDX_MAP.get(channel, index)
            field = f"{task_id}:{write_index}"
            if write_index >= 0 and await self.async_redis.hexists(key, field):
                continue
            serializer_type, payload = self.serde.dumps_typed(value)
            pipe.hset(key, field, json.dumps({
                "task_id": task_id,
                "channel": channel,
                "type": serializer_type,
                "payload": payload.decode("latin1"),
                "task_path": task_path,
            }, ensure_ascii=False))
        await pipe.execute()

    def _load_blobs(self, thread_id: str, checkpoint_ns: str, versions: ChannelVersions) -> dict[str, Any]:
        result = {}
        for channel, version in versions.items():
            row = self.redis.hgetall(self._blob_key(thread_id, checkpoint_ns, channel, version))
            if not row:
                continue
            serializer_type = _decode(row[b"type"])
            if serializer_type == "empty":
                continue
            result[channel] = self.serde.loads_typed((serializer_type, row[b"payload"]))
        return result

    def _load_writes(self, thread_id: str, checkpoint_ns: str, checkpoint_id: str):
        writes = []
        raw_writes = self.redis.hgetall(self._writes_key(thread_id, checkpoint_ns, checkpoint_id))
        for _, raw_value in sorted(raw_writes.items(), key=lambda item: _decode(item[0])):
            value = json.loads(_decode(raw_value))
            writes.append((
                value["task_id"],
                value["channel"],
                self.serde.loads_typed((value["type"], value["payload"].encode("latin1"))),
            ))
        return writes

    async def _aload_blobs(self, thread_id: str, checkpoint_ns: str, versions: ChannelVersions) -> dict[str, Any]:
        result = {}
        for channel, version in versions.items():
            row = await self.async_redis.hgetall(self._blob_key(thread_id, checkpoint_ns, channel, version))
            if not row:
                continue
            serializer_type = _decode(row[b"type"])
            if serializer_type == "empty":
                continue
            result[channel] = self.serde.loads_typed((serializer_type, row[b"payload"]))
        return result

    async def _aload_writes(self, thread_id: str, checkpoint_ns: str, checkpoint_id: str):
        writes = []
        raw_writes = await self.async_redis.hgetall(self._writes_key(thread_id, checkpoint_ns, checkpoint_id))
        for _, raw_value in sorted(raw_writes.items(), key=lambda item: _decode(item[0])):
            value = json.loads(_decode(raw_value))
            writes.append((
                value["task_id"],
                value["channel"],
                self.serde.loads_typed((value["type"], value["payload"].encode("latin1"))),
            ))
        return writes


def build_checkpointer():
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    if os.getenv("CHECKPOINTER_BACKEND", "redis").lower() != "redis":
        return MemorySaver()
    try:
        return RedisCheckpointSaver(redis_url)
    except Exception as error:
        print(f"[Checkpoint] Redis unavailable, fallback to MemorySaver: {error}")
        return MemorySaver()


def _decode(value: Any) -> str:
    return value.decode("utf-8") if isinstance(value, bytes) else str(value)


