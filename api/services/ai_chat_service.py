"""AI Chat 商业化持久化服务。

这个模块只负责 MySQL 状态，不直接调用模型，也不生成伪回答。AI Chat 的真实执行仍由
task_queue -> start_agent_task -> run_deep_agent -> AgentRuntime 完成；这里提供三类能力：
1. 接收用户问题时创建 conversation/message/run 记录；
2. AgentRuntime 完成或失败后回写 assistant 内容和状态；
3. 前端刷新页面或 WebSocket 断线后查询历史消息和任务状态。
"""

from __future__ import annotations

import uuid
from typing import Any

from api.db import ensure_platform_schema, execute, fetch_all, fetch_one, mysql_conn
from api.services.result_payload import parse_structured_json


def create_chat_run(*, tenant_id: str, shop_id: str, user_id: str, conversation_id: str | None, user_content: str, intent: str, task_id: str) -> dict[str, str]:
    """创建一次 AI Chat run，并预置一条 assistant 占位消息。

    表设计上 user message 和 assistant message 分开存放，run.message_id 指向 assistant message。
    这样前端可以先展示“Agent 已接收任务”，等任务完成后用同一个 message_id 替换内容。
    """
    ensure_platform_schema()
    conversation_id = conversation_id or str(uuid.uuid4())
    user_message_id = str(uuid.uuid4())
    assistant_message_id = str(uuid.uuid4())
    run_id = str(uuid.uuid4())
    title = _conversation_title(user_content)

    with mysql_conn(dictionary=True) as (conn, cursor):
        cursor.execute(
            """
            INSERT INTO ai_chat_conversations (id, tenant_id, shop_id, user_id, title, status)
            VALUES (%s, %s, %s, %s, %s, 'active')
            ON DUPLICATE KEY UPDATE updated_at=NOW()
            """,
            (conversation_id, tenant_id, shop_id, user_id, title),
        )
        cursor.execute(
            """
            INSERT INTO ai_chat_messages (id, tenant_id, shop_id, user_id, conversation_id, role, content, status)
            VALUES (%s, %s, %s, %s, %s, 'user', %s, 'completed')
            """,
            (user_message_id, tenant_id, shop_id, user_id, conversation_id, user_content),
        )
        cursor.execute(
            """
            INSERT INTO ai_chat_messages (id, tenant_id, shop_id, user_id, conversation_id, role, content, source, status, task_id, intent)
            VALUES (%s, %s, %s, %s, %s, 'assistant', %s, 'agent', 'running', %s, %s)
            """,
            (assistant_message_id, tenant_id, shop_id, user_id, conversation_id, "Agent 已接收任务，正在进入运行队列。", task_id, intent),
        )
        cursor.execute(
            """
            INSERT INTO ai_chat_runs (id, tenant_id, shop_id, user_id, conversation_id, message_id, task_id, user_content, intent, status, source)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'running', 'agent')
            """,
            (run_id, tenant_id, shop_id, user_id, conversation_id, assistant_message_id, task_id, user_content, intent),
        )
        conn.commit()
    return {"run_id": run_id, "message_id": assistant_message_id, "conversation_id": conversation_id, "task_id": task_id}


def complete_chat_run(*, tenant_id: str, shop_id: str, user_id: str, task_id: str, assistant_content: str, source: str, structured_result_json: str | None = None) -> None:
    """Agent 成功完成后回写 run 和 assistant 消息。"""
    run = _find_run(tenant_id, shop_id, user_id, task_id)
    if not run:
        return
    content = assistant_content.strip() or "Agent 已完成分析，但没有生成可展示内容。"
    execute(
        """
        UPDATE ai_chat_runs
        SET assistant_content=%s, structured_json=%s, status='completed', source=%s, error_message=NULL, completed_at=NOW(), updated_at=NOW()
        WHERE tenant_id=%s AND shop_id=%s AND user_id=%s AND task_id=%s
        """,
        (content, structured_result_json, source, tenant_id, shop_id, user_id, task_id),
    )
    execute(
        """
        UPDATE ai_chat_messages
        SET content=%s, structured_json=%s, status='completed', source=%s, error_message=NULL, updated_at=NOW()
        WHERE tenant_id=%s AND shop_id=%s AND user_id=%s AND id=%s
        """,
        (content, structured_result_json, source, tenant_id, shop_id, user_id, run["message_id"]),
    )


def fail_chat_run(*, tenant_id: str, shop_id: str, user_id: str, task_id: str, error_message: str, status: str = "failed") -> None:
    """Agent 失败、取消或超时后回写真实错误，不返回规则兜底答案。"""
    run = _find_run(tenant_id, shop_id, user_id, task_id)
    if not run:
        return
    message = (error_message or "后端 Agent 暂不可用，无法完成智能分析。")[:1000]
    execute(
        """
        UPDATE ai_chat_runs
        SET status=%s, source='error', error_message=%s, completed_at=NOW(), updated_at=NOW()
        WHERE tenant_id=%s AND shop_id=%s AND user_id=%s AND task_id=%s
        """,
        (status, message, tenant_id, shop_id, user_id, task_id),
    )
    execute(
        """
        UPDATE ai_chat_messages
        SET content=%s, status=%s, source='error', error_message=%s, updated_at=NOW()
        WHERE tenant_id=%s AND shop_id=%s AND user_id=%s AND id=%s
        """,
        (message, status, message, tenant_id, shop_id, user_id, run["message_id"]),
    )


def list_conversations(tenant_id: str, shop_id: str, user_id: str) -> list[dict[str, Any]]:
    """列出当前用户在当前店铺下的 AI Chat 会话。"""
    ensure_platform_schema()
    return fetch_all(
        """
        SELECT id, title, status, created_at AS createdAt, updated_at AS updatedAt
        FROM ai_chat_conversations
        WHERE tenant_id=%s AND shop_id=%s AND user_id=%s
        ORDER BY updated_at DESC
        LIMIT 50
        """,
        (tenant_id, shop_id, user_id),
    )


def list_messages(tenant_id: str, shop_id: str, user_id: str, conversation_id: str) -> list[dict[str, Any]]:
    """按会话返回可直接渲染的消息列表。"""
    ensure_platform_schema()
    rows = fetch_all(
        """
        SELECT id, role, content, structured_json AS structuredJson, source, status, task_id AS taskId, intent, error_message AS errorMessage,
               conversation_id AS conversationId, created_at AS createdAt, updated_at AS updatedAt
        FROM ai_chat_messages
        WHERE tenant_id=%s AND shop_id=%s AND user_id=%s AND conversation_id=%s
        ORDER BY created_at ASC
        """,
        (tenant_id, shop_id, user_id, conversation_id),
    )
    return [_hydrate_message(row) for row in rows]


def get_message(tenant_id: str, shop_id: str, user_id: str, message_id: str) -> dict[str, Any] | None:
    """查询单条 assistant 消息，前端轮询和刷新恢复都走这里。"""
    ensure_platform_schema()
    row = fetch_one(
        """
        SELECT id, role, content, structured_json AS structuredJson, source, status, task_id AS taskId, intent, error_message AS errorMessage,
               conversation_id AS conversationId, created_at AS createdAt, updated_at AS updatedAt
        FROM ai_chat_messages
        WHERE tenant_id=%s AND shop_id=%s AND user_id=%s AND id=%s
        LIMIT 1
        """,
        (tenant_id, shop_id, user_id, message_id),
    )
    return _hydrate_message(row) if row else None


def get_run_by_task(tenant_id: str, shop_id: str, user_id: str, task_id: str) -> dict[str, Any] | None:
    """按 task_id 查询 run，供 timeline 和 smoke 验收确认任务仍可追踪。"""
    ensure_platform_schema()
    return _find_run(tenant_id, shop_id, user_id, task_id)


def _find_run(tenant_id: str, shop_id: str, user_id: str, task_id: str) -> dict[str, Any] | None:
    return fetch_one(
        """
        SELECT id, tenant_id, shop_id, user_id, conversation_id, message_id, task_id, user_content,
               assistant_content, structured_json AS structuredJson, intent, status, source, error_message
        FROM ai_chat_runs
        WHERE tenant_id=%s AND shop_id=%s AND user_id=%s AND task_id=%s
        LIMIT 1
        """,
        (tenant_id, shop_id, user_id, task_id),
    )


def _hydrate_message(row: dict[str, Any]) -> dict[str, Any]:
    structured = parse_structured_json(row.pop("structuredJson", None))
    row["structuredResult"] = structured
    return row


def _conversation_title(content: str) -> str:
    title = " ".join(content.strip().split())[:60]
    return title or "AI 对话"