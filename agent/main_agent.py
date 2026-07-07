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

from agent import prompts
from agent.core.tool_registry import MAIN_AGENT_PERMISSIONS, MAIN_AGENT_TOOLS, tool_registry
from agent.runtime.agent_runtime import AgentRuntime
from agent.runtime.profiles import get_runtime_profile, normalize_runtime_profile


_AGENT_CACHE = {}
_SPEC_CACHE = {}


def build_main_agent(profile: str = "deep"):
    """
    按 profile 构建主 DeepAgents 图。

    主 Agent 的工具不直接 import 裸函数，而是通过 ToolRegistry 取 guarded tools。
    这样 ToolSpec.permissions/risk 会在运行时真正生效，后续权限治理也只需要扩展 registry/security 层。
    """
    from deepagents import create_deep_agent
    from agent.llm import get_deep_model, get_standard_model

    runtime_profile = get_runtime_profile(profile)
    if runtime_profile.agent_mode == "none":
        raise ValueError("realtime profile 不构建 DeepAgent，请使用 ChatAgentRuntime。")
    subagents_list, subagent_specs = _subagents_for_profile(runtime_profile.name)
    checkpointer = _checkpointer_for_profile(runtime_profile.name)
    # deep profile 使用 deep 模型；standard profile 使用 standard 模型，避免普通后台任务误走深度模型。
    model = get_deep_model() if runtime_profile.name == "deep" else get_standard_model()
    return create_deep_agent(
        model=model,
        tools=tool_registry.tools(MAIN_AGENT_TOOLS, MAIN_AGENT_PERMISSIONS, "main_agent"),
        subagents=subagents_list,
        system_prompt=prompts.main_agent_content["system_prompt"],
        checkpointer=checkpointer,
    )


def get_deep_agent(profile: str = "deep"):
    """懒加载 DeepAgent；导入 main_agent.py 不再构建模型、subagents、tools 或 checkpointer。"""
    normalized = normalize_runtime_profile(profile)
    if normalized == "realtime":
        raise ValueError("realtime profile 不允许构建 DeepAgent")
    if normalized not in _AGENT_CACHE:
        _AGENT_CACHE[normalized] = build_main_agent(normalized)
    return _AGENT_CACHE[normalized], _SPEC_CACHE.get(normalized, [])


def reload_agent_policy():
    """热重载 prompt/policy 后重建主图，保持 API 层入口不变。"""
    global _AGENT_CACHE, _SPEC_CACHE
    prompts.reload_prompts()
    _AGENT_CACHE = {}
    _SPEC_CACHE = {}


def _subagents_for_profile(profile: str):
    """按 profile 裁剪 subagent。

    standard 默认只保留可预算约束的数据库助手；deep 才允许通过环境变量开启知识库/网络搜索等较重能力。
    这样可以把商业化热路径控制在可预测成本内，同时保留显式深度任务的扩展空间。
    """
    from agent.sub_agents.database_query_agent import database_query_agent, database_query_agent_spec

    subagents = [database_query_agent]
    specs = [database_query_agent_spec]
    if profile == "deep":
        import os

        # 可选深度子 Agent 延迟 import，避免 FastAPI 启动和 standard/realtime 热路径加载重依赖。
        if os.getenv("DEEP_AGENT_ENABLE_KNOWLEDGE_BASE", "false").lower() in {"1", "true", "yes", "on"}:
            from agent_extensions.deep_subagents.knowledge_base_agent import knowledge_base_agent, knowledge_base_agent_spec

            subagents.append(knowledge_base_agent)
            specs.append(knowledge_base_agent_spec)
        if os.getenv("DEEP_AGENT_ENABLE_NETWORK_SEARCH", "false").lower() in {"1", "true", "yes", "on"}:
            from agent_extensions.deep_subagents.network_search_agent import network_search_agent, network_search_agent_spec

            subagents.append(network_search_agent)
            specs.append(network_search_agent_spec)
    _SPEC_CACHE[profile] = specs
    return subagents, specs


def _checkpointer_for_profile(profile: str):
    """只给 deep profile 启用 checkpointer，standard/realtime 不在热路径初始化。"""
    if profile != "deep":
        return None
    from agent_extensions.checkpointing.checkpoint import build_checkpointer

    return build_checkpointer()


async def run_deep_agent(
    task_query,
    conversation_id,
    task_id=None,
    tenant_id="default_tenant",
    user_id="local_user",
    shop_id="default_shop",
    runtime_profile="full",
    task_plan_override=None,
):
    """
    FastAPI 后台任务调用的稳定入口。

    这里刻意只保留兼容入口：API、任务队列和旧调用方仍然调用 run_deep_agent，但阶段拆分、异常处理、
    Critic retry、记忆写入和 trace 收尾都由 AgentRuntime 统一编排。
    """
    print(f"当前会话的main_agent开始执行了！ conversation_id:{conversation_id} task_id:{task_id}")
    # 这里仍然按 runtime_profile 获取 Agent 图，但真正的阶段编排已经下沉到 AgentRuntime。
    # 保持该兼容入口可以让 API 层、任务队列和旧测试脚本不需要同步改调用方式。
    agent, subagent_specs = get_deep_agent(runtime_profile)
    runtime = AgentRuntime(agent, subagent_specs)
    return await runtime.run(
        task_query,
        conversation_id=conversation_id,
        task_id=task_id,
        tenant_id=tenant_id,
        user_id=user_id,
        shop_id=shop_id,
        runtime_profile=runtime_profile,
        task_plan_override=task_plan_override,
    )
