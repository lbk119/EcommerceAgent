"""
运行时上下文适配层。

api.context 已经用 ContextVar 保存当前会话、任务和租户身份。观测、权限、工具审计等平台层
不应该直接到处读取多个 ContextVar，因此这里把它们整理成一个 RuntimeContext。

注意：RuntimeContext 是“当前协程链路”的快照，不是数据库实体，也不应该跨请求缓存。
"""

from dataclasses import dataclass
from typing import Optional

from api.context import get_identity_context, get_thread_context


@dataclass(frozen=True)
class RuntimeContext:
    """
    当前执行链路的最小上下文。

    trace_id 优先使用 task_id，其次 conversation_id，最后回退 system。
    这样后台启动、导入检查或无用户上下文的系统事件也能写 trace，不会因为缺少 task_id 报错。
    """

    trace_id: str
    conversation_id: Optional[str]
    task_id: Optional[str]
    tenant_id: Optional[str]
    user_id: Optional[str]
    shop_id: Optional[str]


def current_runtime_context() -> RuntimeContext:
    """从 ContextVar 中读取当前身份，并组装为平台层统一使用的 RuntimeContext。"""
    identity = get_identity_context()
    conversation_id = identity.conversation_id if identity else get_thread_context()
    task_id = identity.task_id if identity else None
    trace_id = task_id or conversation_id or "system"
    return RuntimeContext(
        trace_id=trace_id,
        conversation_id=conversation_id,
        task_id=task_id,
        tenant_id=identity.tenant_id if identity else None,
        user_id=identity.user_id if identity else None,
        shop_id=identity.shop_id if identity else None,
    )