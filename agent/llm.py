"""LLM 获取门面。

业务代码不要直接读取模型环境变量或初始化 LangChain 模型，而是通过这里按语义档位获取模型。
fast、standard、deep、critic 的供应商、超时、重试和 trace 回调都集中在 `LLMRouter` 中治理。
"""

from agent.platform.llm_router import llm_router


def get_fast_model():
    """返回 realtime/路由/轻量摘要使用的 fast 模型。"""
    return llm_router.model("fast")


def get_standard_model():
    """返回 standard 后台任务使用的标准模型。"""
    return llm_router.model("standard")


def get_deep_model():
    """返回 deep profile 长任务和复杂推理使用的模型。"""
    return llm_router.model("deep")


def get_critic_model():
    """返回 Critic、监督和低温质量判断使用的模型。"""
    return llm_router.model("critic")


__all__ = ["get_fast_model", "get_standard_model", "get_deep_model", "get_critic_model"]
