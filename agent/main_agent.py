"""Agent 稳定入口。

FastAPI、任务队列和脚本都通过这里启动 Agent 任务。本文件只负责按 profile 获取缓存后的
deepagents-native main agent，并把阶段化执行交给 `AgentRuntime`，避免 API 层直接依赖图实现细节。
"""

from agent.subagent.config import deepagents_enabled
from agent.subagent.runtime import clear_native_agent_cache, get_native_main_agent
from agent.runtime.agent_runtime import AgentRuntime
from agent.runtime.profiles import normalize_runtime_profile


def get_agent_app(profile: str = "standard"):
    """返回指定 profile 的 deepagents-native main agent 与 subagent 规格。"""
    normalized = normalize_runtime_profile(profile)
    if not deepagents_enabled(normalized):
        raise RuntimeError(f"deepagents-native runtime is disabled for profile {normalized}")
    return get_native_main_agent(normalized)


def reload_agent_policy():
    """清理 native agent 缓存，让批准后的策略和记忆配置在下次任务生效。"""
    clear_native_agent_cache()


async def run_agent_task(
    task_query,
    conversation_id,
    task_id=None,
    tenant_id="default_tenant",
    user_id="local_user",
    shop_id="default_shop",
    runtime_profile="full",
    task_plan_override=None,
):
    """FastAPI/后台任务调用的稳定入口，兼容 realtime、standard 和 deep 三档 profile。"""
    normalized_profile = normalize_runtime_profile(runtime_profile)
    try:
        agent, subagent_specs = get_agent_app(normalized_profile)
    except Exception as error:
        raise RuntimeError(f"deepagents-native initialization failed; check model, store, MCP, and checkpoint config: {error}") from error

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
