"""任务运行上下文准备。

Agent 执行前需要完成几件和模型无关的准备工作：
- 为当前 conversation 创建 output/session_xxx 工作目录；
- 将 updated/session_xxx 中的上传文件复制到 output/session_xxx，方便前端统一下载；
- 设置 ContextVar，让工具能拿到 session_dir、thread_id、tenant/user/shop 身份；
- 构造 LangGraph/DeepAgents 运行 config；
- 构造给模型看的“工作目录 + 上传文件 + 长期记忆”提示片段。

这些逻辑拆到这里后，主入口只需要创建 TaskRunContext 并交给 runner，避免文件系统、身份、
记忆召回和图配置散落在多个执行阶段。
"""

import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from api.context import reset_session_context, set_identity_context, set_session_context, set_thread_context
from api.monitor import monitor
from agent.memory import MemoryIdentity


PROJECT_ROOT = Path(__file__).parents[2].resolve()


@dataclass
class TaskRunContext:
    """
    单次任务运行时的上下文快照。

    这个对象只在一次 run_agent_task 调用中使用，不跨任务复用。它把文件目录、身份、LangGraph
    config 和 prompt 片段放在一起，避免函数之间传一长串松散参数。
    """

    query: str
    conversation_id: str
    task_id: str
    identity: MemoryIdentity
    session_dir: Path
    session_dir_str: str
    relative_session_dir_str: str
    path_instruction: str
    config: Dict[str, Any]
    session_dir_token: Any
    thread_token: Any
    identity_token: Any

    def cleanup(self) -> None:
        """释放 ContextVar token，防止当前任务上下文污染后续请求。"""
        reset_session_context(self.session_dir_token, self.thread_token, self.identity_token)


def build_task_context(
    task_query: str,
    conversation_id: str,
    task_id: str,
    tenant_id: str,
    user_id: str,
    shop_id: str,
) -> TaskRunContext:
    """
    准备 Agent 执行所需的全部运行上下文。

    注意：这个函数会设置 ContextVar，所以调用方必须在 finally 中调用 context.cleanup()。
    """
    identity = MemoryIdentity(
        tenant_id=tenant_id,
        user_id=user_id,
        shop_id=shop_id,
        conversation_id=conversation_id,
        task_id=task_id,
    )
    session_dir = PROJECT_ROOT / "output" / f"session_{conversation_id}"
    session_dir.mkdir(parents=True, exist_ok=True)
    # Agent prompt 和工具统一使用正斜杠路径，减少 Windows 反斜杠被模型转义或误读的概率。
    session_dir_str = str(session_dir).replace("\\", "/")
    relative_session_dir_str = str(session_dir.relative_to(PROJECT_ROOT)).replace("\\", "/")

    updated_info_prompt = _copy_uploaded_files(conversation_id, session_dir)

    # ContextVar 必须在调用 runner 前设置，数据库工具、文件工具、trace/monitor 都依赖这些上下文。
    session_dir_token = set_session_context(session_dir_str)
    thread_token = set_thread_context(conversation_id)
    identity_token = set_identity_context(identity)
    monitor.report_session_dir(session_dir_str)

    config = {
        "configurable": {
            # LangGraph/checkpointer 使用 thread_id 区分会话；task_id/tenant/shop 用于审计和工具上下文。
            "thread_id": conversation_id,
            "checkpoint_ns": "main_agent",
            "task_id": task_id,
            "tenant_id": tenant_id,
            "user_id": user_id,
            "shop_id": shop_id,
        },
        "recursion_limit": int(os.getenv("AGENT_RECURSION_LIMIT", "50")),
    }
    path_instruction = _build_path_instruction(identity, task_query, task_id, conversation_id, relative_session_dir_str, updated_info_prompt)

    return TaskRunContext(
        query=task_query,
        conversation_id=conversation_id,
        task_id=task_id,
        identity=identity,
        session_dir=session_dir,
        session_dir_str=session_dir_str,
        relative_session_dir_str=relative_session_dir_str,
        path_instruction=path_instruction,
        config=config,
        session_dir_token=session_dir_token,
        thread_token=thread_token,
        identity_token=identity_token,
    )


def _copy_uploaded_files(conversation_id: str, session_dir: Path) -> str:
    """
    把上传目录中的文件复制到本次 output/session 目录，并返回给模型看的上传文件提示。

    工具层只允许读取 session_dir 内的文件。这里做一次复制，可以把“用户上传文件”和“Agent 产物”
    都统一放到一个安全工作目录中，前端下载和工具读取也更简单。
    """
    updated_dir = PROJECT_ROOT / "updated" / f"session_{conversation_id}"
    if not updated_dir.exists():
        return ""

    files = [file.name for file in updated_dir.iterdir() if file.is_file()]
    if not files:
        return ""

    for filename in files:
        shutil.copy2(updated_dir / filename, session_dir / filename)
    return (
        "\n    [已上传文件] 已加载到工作目录:\n"
        + "\n".join([f"    - {filename}" for filename in files])
        + "\n    请优先使用工具（read_file_content）读取并参考这些文件。"
    )


def _build_path_instruction(
    identity: MemoryIdentity,
    task_query: str,
    task_id: str,
    conversation_id: str,
    relative_session_dir_str: str,
    updated_info_prompt: str,
) -> str:
    """构造追加到用户请求后的工作环境提示。

    DeepAgents store 是唯一长期记忆后端。本项目不再在外层 runtime 额外执行 MySQL 记忆召回。
    """
    return f"""
    【工作环境指令】
    工作目录: {relative_session_dir_str}
    {updated_info_prompt}

    规则:
    1. 新生成文件必须保存到工作目录 {relative_session_dir_str}/filename
    2. 读取已上传文件时，直接将文件名作为 filename 参数传入 read_file_content，不要带目录前缀。
    3. 使用相对路径，禁止使用绝对路径。
    4. 若存在上传文件，请先分析内容
    """
