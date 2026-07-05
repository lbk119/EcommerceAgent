"""
主 Agent 图定义与入口委托。

这个文件现在只保留两类职责：
1. build_main_agent / reload_agent_policy：负责把模型、工具、子 Agent、prompt、checkpointer 组装成 DeepAgents 图。
2. run_deep_agent：保持 API 层调用入口不变，真实阶段编排委托给 AgentRuntime。

任务目录、上传文件、ContextVar、长期记忆检索在 task_context.py；
流式执行、循环检测、人工中断恢复在 agent_runner.py；
AgentRuntime 负责 prepare_context / retrieve_memory / execute_agent / run_critic / persist_result /
write_memory / finalize_trace；具体的结果落盘和记忆写入仍复用 result_pipeline.py。
"""

from deepagents import create_deep_agent

from agent import prompts
from agent.core.tool_registry import MAIN_AGENT_PERMISSIONS, MAIN_AGENT_TOOLS, tool_registry
from agent.llm import get_reasoning_model
from agent.memory.checkpoint import build_checkpointer
from agent.runtime.agent_runtime import AgentRuntime
from agent.sub_agents.database_query_agent import database_query_agent, database_query_agent_spec
from agent.sub_agents.knowledge_base_agent import knowledge_base_agent, knowledge_base_agent_spec
from agent.sub_agents.network_search_agent import network_search_agent, network_search_agent_spec


# DeepAgents 子 Agent 仍然由各自模块定义；这里仅负责组装主图。
subagents_list = [
    knowledge_base_agent,
    database_query_agent,
    network_search_agent,
]
subagent_specs = [
    knowledge_base_agent_spec,
    database_query_agent_spec,
    network_search_agent_spec,
]

agent_checkpointer = build_checkpointer()


def build_main_agent():
    """
    构建主 DeepAgents 图。

    主 Agent 的工具不直接 import 裸函数，而是通过 ToolRegistry 取 guarded tools。
    这样 ToolSpec.permissions/risk 会在运行时真正生效，后续权限治理也只需要扩展 registry/security 层。
    """
    return create_deep_agent(
        model=get_reasoning_model(),
        tools=tool_registry.tools(MAIN_AGENT_TOOLS, MAIN_AGENT_PERMISSIONS, "main_agent"),
        subagents=subagents_list,
        system_prompt=prompts.main_agent_content["system_prompt"],
        checkpointer=agent_checkpointer,
    )


main_agent = build_main_agent()


def reload_agent_policy():
    """热重载 prompt/policy 后重建主图，保持 API 层入口不变。"""
    global main_agent
    prompts.reload_prompts()
    main_agent = build_main_agent()


async def run_deep_agent(
    task_query,
    conversation_id,
    task_id=None,
    tenant_id="default_tenant",
    user_id="local_user",
    shop_id="default_shop",
):
    """
    FastAPI 后台任务调用的稳定入口。

    这里刻意只保留兼容入口：API、任务队列和旧调用方仍然调用 run_deep_agent，但阶段拆分、异常处理、
    Critic retry、记忆写入和 trace 收尾都由 AgentRuntime 统一编排。
    """
    print(f"当前会话的main_agent开始执行了！ conversation_id:{conversation_id} task_id:{task_id}")
    runtime = AgentRuntime(main_agent, subagent_specs)
    return await runtime.run(
        task_query,
        conversation_id=conversation_id,
        task_id=task_id,
        tenant_id=tenant_id,
        user_id=user_id,
        shop_id=shop_id,
    )
