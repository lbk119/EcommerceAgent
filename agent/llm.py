"""模型获取门面。

业务代码不要直接 import LangChain 或读取 LLM_* 环境变量，而是通过这里按语义档位获取模型：
- fast：聊天受理、路由、轻量摘要等高频实时路径；
- standard：标准后台 workflow 和数据库助手；
- deep：显式深度推理任务；
- critic：审查、监督、循环判断等需要稳定输出的判断任务。

真正的模型名称、超时、重试和 tracing callback 都集中在 agent.core.llm_router 中。
这个文件只保留简短函数，是为了让旧代码和新运行时都能稳定复用同一套模型路由规则。
"""

from agent.core.llm_router import llm_router


def get_fast_model():
    """获取 fast 档模型。

    fast 档面向用户可感知的热路径，因此默认超时更短、重试更少，优先保证前端及时收到反馈。
    """
    return llm_router.get("fast_model")


def get_standard_model():
    """获取 standard 档模型。

    standard 仍复用 fast tier 的真实模型，但给后台经营分析、SQL 助手等任务更宽松的执行预算。
    """
    return llm_router.get("standard_model")


def get_deep_model():
    """获取 deep 档模型。

    只有明确需要复杂推理的 DeepAgent 任务才应该使用该档位，避免普通聊天误走慢且昂贵的模型路径。
    """
    return llm_router.get("deep_model")


def get_critic_model():
    """获取 critic 档模型。

    critic 档通常 temperature 更低，用于 Critic、Supervisor、循环检测等需要可复现判断的场景。
    """
    return llm_router.get("critic_model")
