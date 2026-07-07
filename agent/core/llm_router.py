"""统一的大模型后端 / 模型路由器。

这个模块负责把业务代码里的“语义模型档位”（例如 fast、standard、deep、critic）转换成
真正可调用的 LangChain ChatModel。这样上层 Agent 不需要关心具体模型名称、供应商、
超时时间、重试次数等部署细节。

运行时刻意只暴露两类真实模型档位：

- fast tier：用于 AI Chat、标准工作流、Reducer、Critic / Supervisor 等高频路径。
- deep tier：只用于明确需要深度推理的 Deep Agent 任务。

部署时通常只需要配置 LLM_FAST_MODEL 和 LLM_DEEP_MODEL；本文件会再按使用场景派生出
fast_model、standard_model、critic_model、deep_model 这几个内部 profile。
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

from dotenv import find_dotenv, load_dotenv
from langchain.chat_models import init_chat_model
from langchain_core.callbacks import BaseCallbackHandler

from agent.core.runtime_context import current_runtime_context
from agent.observability.tracer import tracer


load_dotenv(find_dotenv())


@dataclass(frozen=True)
class ModelProfile:
    """单个语义模型档位解析后的不可变配置。

    这里使用 frozen dataclass，表示 profile 一旦从环境变量解析完成，就不应在运行中被
    其它代码修改。这样可以避免长任务执行期间模型配置被意外变更，导致 trace 和真实调用不一致。
    """

    # 业务语义名称，例如 fast / standard / deep / critic，用于 trace 和诊断展示。
    name: str
    # 实际传给 LangChain / 模型供应商的模型名称。
    model: str
    # LangChain init_chat_model 使用的 provider，默认按 OpenAI 兼容接口处理。
    provider: str = "openai"
    # 单次模型调用超时时间，单位秒。不同 profile 有不同默认值。
    timeout: float = 180.0
    # 模型调用失败后的重试次数。热路径默认更保守，避免阻塞前端实时体验。
    max_retries: int = 3
    # 温度参数允许为空；为空时不显式传入，交给 provider 默认值。
    temperature: Optional[float] = None


class LLMTracingCallback(BaseCallbackHandler):
    """把 LangChain 的模型调用事件写入项目统一 trace 流。

    LangChain 会在模型开始、成功结束、失败时调用这些 callback。这里把这些事件转成
    tracer.emit(...)，使 AI Chat、标准 Agent、诊断接口和性能 smoke 都能看到同一套 LLM
    调用 telemetry，包括耗时、token 使用、模型档位、超时和重试配置。
    """

    def __init__(self, profile: ModelProfile):
        # 保存完整 profile，后续 trace 里需要同时记录语义档位和实际模型名。
        self.profile = profile
        self.profile_name = profile.name
        # 一个 callback 实例可能处理多个 LangChain run，因此按 run_id 记录开始时间。
        self._starts: Dict[str, float] = {}

    def on_chat_model_start(self, serialized: Dict[str, Any], messages: Any, *, run_id: Any, **kwargs: Any) -> None:
        """ChatModel 开始调用时触发。

        ChatModel 的输入通常是 messages，所以直接复用 _start 统一记录开始时间和 prompt 大小估算。
        """
        self._start(str(run_id), serialized, messages)

    def on_llm_start(self, serialized: Dict[str, Any], prompts: Any, *, run_id: Any, **kwargs: Any) -> None:
        """普通 LLM 开始调用时触发。

        兼容非 ChatModel 的 LangChain 回调形态；当前项目主要使用 ChatModel，但保留这条路径
        可以让后续替换 provider 或模型封装时继续拿到 trace。
        """
        self._start(str(run_id), serialized, prompts)

    def on_llm_end(self, response: Any, *, run_id: Any, **kwargs: Any) -> None:
        """模型调用成功结束时记录耗时、token 和模型配置。"""
        run_key = str(run_id)
        latency_ms = self._latency(run_key)
        token_usage = _extract_token_usage(response)
        context = current_runtime_context()
        tracer.emit(
            "llm_call_finished",
            trace_id=context.trace_id,
            task_id=context.task_id,
            conversation_id=context.conversation_id,
            agent_name=self.profile_name,
            latency_ms=latency_ms,
            token_input=token_usage.get("input"),
            token_output=token_usage.get("output"),
            metadata={
                # profile/model_profile 两个字段都保留，兼容不同诊断视图的字段命名。
                "profile": self.profile_name,
                "model_profile": self.profile_name,
                "model_name": self.profile.model,
                "timeout_seconds": self.profile.timeout,
                "retry_count": self.profile.max_retries,
                "streaming_enabled": False,
            },
        )

    def on_llm_error(self, error: BaseException, *, run_id: Any, **kwargs: Any) -> None:
        """模型调用失败时记录失败事件，供诊断页和 slow-task 分析使用。"""
        run_key = str(run_id)
        latency_ms = self._latency(run_key)
        context = current_runtime_context()
        tracer.emit(
            "llm_call_failed",
            trace_id=context.trace_id,
            task_id=context.task_id,
            conversation_id=context.conversation_id,
            agent_name=self.profile_name,
            latency_ms=latency_ms,
            # 错误信息截断，避免把过长 provider 响应或敏感上下文写入 trace。
            error=str(error)[:1000],
            metadata={
                "profile": self.profile_name,
                "model_profile": self.profile_name,
                "model_name": self.profile.model,
                "timeout_seconds": self.profile.timeout,
                "retry_count": self.profile.max_retries,
                "streaming_enabled": False,
            },
        )

    def _start(self, run_key: str, serialized: Dict[str, Any], prompt_payload: Any) -> None:
        """记录一次模型调用开始，并写入 prompt 规模估算等基础元数据。

        run_key 来自 LangChain run_id。后续 on_llm_end/on_llm_error 会用同一个 key 计算耗时。
        """
        self._starts[run_key] = time.perf_counter()
        context = current_runtime_context()
        tracer.emit(
            "llm_call_started",
            trace_id=context.trace_id,
            task_id=context.task_id,
            conversation_id=context.conversation_id,
            agent_name=self.profile_name,
            metadata={
                "profile": self.profile_name,
                "model_profile": self.profile_name,
                "model_name": self.profile.model,
                # serialized 是 LangChain 对当前 runnable/model 的描述；不同版本可能给 name 或 id。
                "serialized": serialized.get("name") or serialized.get("id"),
                # 这里不保存完整 prompt，只保存字符数估算，减少 trace 体积和敏感信息暴露。
                "prompt_chars_estimate": _estimate_prompt_chars(prompt_payload),
                "timeout_seconds": self.profile.timeout,
                "retry_count": self.profile.max_retries,
                "streaming_enabled": False,
            },
        )

    def _latency(self, run_key: str) -> float:
        """返回单次模型调用的耗时，单位毫秒。

        读取后会从 _starts 中删除该 run_key，避免长时间运行后缓存无意义增长。
        如果没有对应开始时间，说明 callback 顺序异常或进程中途恢复，返回 0.0 作为兜底。
        """
        started_at = self._starts.pop(run_key, None)
        if started_at is None:
            return 0.0
        return round((time.perf_counter() - started_at) * 1000, 2)


class LLMRouter:
    """进程级模型路由器，负责按 profile 懒加载 ChatModel。

    路由器在模块底部以单例 llm_router 的方式创建。这样每个 Python 进程内，同一个 profile
    只初始化一次模型对象，既减少热路径开销，也让 callback 配置集中在这里。
    """

    def __init__(self):
        # fast/deep 是部署层面真正需要配置的两个模型名称；其它 profile 都基于它们派生。
        fast_model = os.getenv("LLM_FAST_MODEL") or "gpt-5.4-mini"
        deep_model = os.getenv("LLM_DEEP_MODEL") or "gpt-5.5"
        self.profiles = {
            # fast_model 面向 AI Chat 实时路径：超时短、重试少，优先保证“快速受理/快速反馈”。
            "fast_model": self._profile(
                name="fast",
                canonical_prefix="LLM_FAST",
                default_model=fast_model,
                default_timeout=8,
                default_retries=1,
                default_temperature=0.2,
                timeout_aliases=("AI_CHAT_LLM_TIMEOUT_SECONDS",),
                retry_aliases=("AI_CHAT_LLM_MAX_RETRIES",),
            ),
            # standard_model 仍使用 fast tier，但给标准后台工作流更宽松的超时预算。
            "standard_model": self._profile(
                name="standard",
                canonical_prefix="LLM_FAST",
                default_model=fast_model,
                default_timeout=20,
                default_retries=1,
                default_temperature=0.2,
            ),
            # deep_model 只给明确深度推理任务使用，避免普通聊天误走昂贵慢路径。
            "deep_model": self._profile(
                name="deep",
                canonical_prefix="LLM_DEEP",
                default_model=deep_model,
                default_timeout=60,
                default_retries=1,
                default_temperature=0.2,
            ),
            # critic_model 用 fast tier 且 temperature=0，保证审查/监督类判断更稳定可复现。
            "critic_model": self._profile(
                name="critic",
                canonical_prefix="LLM_FAST",
                default_model=fast_model,
                default_timeout=15,
                default_retries=0,
                default_temperature=0.0,
            ),
        }
        # profile_name -> LangChain ChatModel。第一次 get 时才创建，避免 FastAPI 启动期过重。
        self._models: Dict[str, Any] = {}

    def get(self, profile_name: str = "standard_model"):
        """按语义 profile 返回一个懒加载的 ChatModel。

        Args:
            profile_name: 内部 profile key，例如 standard_model、fast_model、deep_model、critic_model。

        Raises:
            KeyError: 调用了不存在的 profile，通常意味着上层代码写错了路由名称。
        """
        if profile_name not in self.profiles:
            raise KeyError(f"Unknown LLM profile: {profile_name}")
        if profile_name not in self._models:
            self._models[profile_name] = self._build_model(self.profiles[profile_name])
        return self._models[profile_name]

    def _profile(
        self,
        *,
        name: str,
        canonical_prefix: str,
        default_model: str,
        default_timeout: float,
        default_retries: int,
        default_temperature: float,
        timeout_aliases: tuple[str, ...] = (),
        retry_aliases: tuple[str, ...] = (),
    ) -> ModelProfile:
        """从环境变量和默认值解析一个 ModelProfile。

        配置优先级：
        1. canonical_prefix 对应的标准环境变量，例如 LLM_FAST_MODEL、LLM_FAST_TIMEOUT_SECONDS。
        2. 调用方传入的兼容别名，例如 AI_CHAT_LLM_TIMEOUT_SECONDS。
        3. 代码里的默认值。

        这样既能保留历史环境变量兼容性，又能把推荐配置收敛到 LLM_FAST_* / LLM_DEEP_*。
        """
        model = os.getenv(f"{canonical_prefix}_MODEL") or default_model
        provider = os.getenv("LLM_PROVIDER") or "openai"
        # timeout/retries 支持 profile 专属别名，主要用于兼容历史 AI Chat 配置。
        timeout = _first_env(
            *timeout_aliases,
            f"{canonical_prefix}_TIMEOUT_SECONDS",
        )
        retries = _first_env(
            *retry_aliases,
            f"{canonical_prefix}_MAX_RETRIES",
        )
        temperature = _first_env(
            f"{canonical_prefix}_TEMPERATURE",
            "LLM_TEMPERATURE",
        )

        # 这里统一做类型转换，保证下游 init_chat_model 收到的是确定类型。
        return ModelProfile(
            name=name,
            model=model,
            provider=provider,
            timeout=float(timeout or default_timeout),
            max_retries=int(retries or default_retries),
            temperature=float(temperature or default_temperature),
        )

    def _build_model(self, profile: ModelProfile):
        """根据 ModelProfile 创建 LangChain ChatModel。

        返回原始 ChatModel，而不是再包一层自定义对象，是为了兼容 DeepAgents / LangChain
        生态中对 Runnable、callbacks、stream 等能力的预期。
        """
        kwargs: Dict[str, Any] = {
            "model": profile.model,
            "model_provider": profile.provider,
            "timeout": profile.timeout,
            "max_retries": profile.max_retries,
            # 每个模型对象绑定自己的 tracing callback，trace 中能区分调用来自哪个 profile。
            "callbacks": [LLMTracingCallback(profile)],
        }
        # temperature 允许为空；为空时不传，避免某些 provider 不接受该参数。
        if profile.temperature is not None:
            kwargs["temperature"] = profile.temperature
        return init_chat_model(**kwargs)


def _extract_token_usage(response: Any) -> Dict[str, Optional[int]]:
    """尽力从不同 OpenAI 兼容 provider 的响应中提取 token 使用量。

    不同 LangChain/provider 版本可能把 token 信息放在 llm_output.token_usage 或 llm_output.usage。
    这个函数只做宽容解析：拿不到就返回 None，不影响主流程。
    """
    usage = getattr(response, "llm_output", None) or {}
    token_usage = usage.get("token_usage") or usage.get("usage") or {}
    input_tokens = token_usage.get("prompt_tokens") or token_usage.get("input_tokens")
    output_tokens = token_usage.get("completion_tokens") or token_usage.get("output_tokens")
    # 有些 provider 只返回 total_tokens 和 output_tokens，此时可以反推出 input_tokens。
    if input_tokens is None and "total_tokens" in token_usage and output_tokens is not None:
        input_tokens = token_usage["total_tokens"] - output_tokens
    return {"input": input_tokens, "output": output_tokens}


def _estimate_prompt_chars(prompt_payload: Any) -> int:
    """估算 prompt 字符数，用于 trace 元数据。

    这里刻意不保存完整 prompt，只记录规模，原因是：
    - trace 体积更小；
    - 避免把用户输入、商业数据或系统提示词写入可长期保存的日志。
    """
    try:
        if isinstance(prompt_payload, str):
            return len(prompt_payload)
        if isinstance(prompt_payload, list):
            # ChatModel 的 messages 通常是列表，递归累加每条消息的估算长度。
            return sum(_estimate_prompt_chars(item) for item in prompt_payload)
        content = getattr(prompt_payload, "content", None)
        if content is not None:
            return len(str(content))
        return len(str(prompt_payload))
    except Exception:
        return 0


def _first_env(*names: str) -> Optional[str]:
    """按顺序返回第一个存在且非空的环境变量值。"""
    for name in names:
        value = os.getenv(name)
        if value:
            return value
    return None


# 全局单例：业务代码统一从这里获取模型，避免每个模块自行初始化模型和 callback。
llm_router = LLMRouter()
