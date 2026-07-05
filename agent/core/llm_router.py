"""
统一 LLM Backend / Model Router。

当前项目里 Agent、循环监督器、未来 Critic 都需要调用模型。如果每个模块都直接
`init_chat_model(...)`，后续会很难统一 timeout、重试、温度、日志、token 统计和 provider 切换。

这个模块先做一个轻量模型路由层，不做负载均衡和复杂调度，只负责：
- 定义 fast_model / reasoning_model / critic_model 三类 profile；
- 从环境变量读取每类 profile 的模型参数；
- 构造 LangChain ChatModel；
- 给每次 LLM 调用挂上 tracing callback，写入 JSONL 观测日志。

后续要支持 Qwen / OpenAI / Ollama / vLLM 时，优先扩展 ModelProfile 和 _build_model，
不要让业务代码直接感知具体供应商差异。
"""

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
    """
    单个模型 profile 的稳定配置。

    name 是平台内部名字，例如 fast / reasoning / critic；
    model/provider/timeout/max_retries/temperature 是传给 LangChain init_chat_model 的核心参数。

    使用 dataclass(frozen=True) 是为了让 profile 在进程启动后保持只读，避免运行中被某个任务
    意外修改，导致不同会话的模型行为不一致。
    """

    name: str
    model: str
    provider: str = "openai"
    timeout: float = 180.0
    max_retries: int = 3
    temperature: Optional[float] = None


class LLMTracingCallback(BaseCallbackHandler):
    """
    LangChain callback：把每次模型调用写入统一 trace。

    这里不直接包一层 invoke/ainvoke，是因为 DeepAgents 内部会自己调用模型。
    callback 能挂在 ChatModel 上，让 DeepAgents、LoopGuard、未来 Critic 的调用都自动被记录。
    """

    def __init__(self, profile_name: str):
        self.profile_name = profile_name
        # run_id -> perf_counter 开始时间。LangChain 会给每次模型调用分配 run_id。
        self._starts: Dict[str, float] = {}

    def on_chat_model_start(self, serialized: Dict[str, Any], messages: Any, *, run_id: Any, **kwargs: Any) -> None:
        # ChatModel 会触发这个回调；只记录 started，不记录 prompt 明文，避免 trace 泄露敏感内容。
        self._start(str(run_id), serialized)

    def on_llm_start(self, serialized: Dict[str, Any], prompts: Any, *, run_id: Any, **kwargs: Any) -> None:
        # 兼容非 chat LLM 的回调形态，保留同一套观测逻辑。
        self._start(str(run_id), serialized)

    def on_llm_end(self, response: Any, *, run_id: Any, **kwargs: Any) -> None:
        run_key = str(run_id)
        latency_ms = self._latency(run_key)
        token_usage = _extract_token_usage(response)
        context = current_runtime_context()
        # token 字段在不同 provider 上名字不完全一致，_extract_token_usage 会尽量归一化。
        tracer.emit(
            "llm_call_finished",
            trace_id=context.trace_id,
            task_id=context.task_id,
            conversation_id=context.conversation_id,
            agent_name=self.profile_name,
            latency_ms=latency_ms,
            token_input=token_usage.get("input"),
            token_output=token_usage.get("output"),
            metadata={"profile": self.profile_name},
        )

    def on_llm_error(self, error: BaseException, *, run_id: Any, **kwargs: Any) -> None:
        run_key = str(run_id)
        latency_ms = self._latency(run_key)
        context = current_runtime_context()
        # 错误信息截断，避免第三方 SDK 把过长请求上下文或敏感内容带进日志。
        tracer.emit(
            "llm_call_failed",
            trace_id=context.trace_id,
            task_id=context.task_id,
            conversation_id=context.conversation_id,
            agent_name=self.profile_name,
            latency_ms=latency_ms,
            error=str(error)[:1000],
            metadata={"profile": self.profile_name},
        )

    def _start(self, run_key: str, serialized: Dict[str, Any]) -> None:
        self._starts[run_key] = time.perf_counter()
        context = current_runtime_context()
        tracer.emit(
            "llm_call_started",
            trace_id=context.trace_id,
            task_id=context.task_id,
            conversation_id=context.conversation_id,
            agent_name=self.profile_name,
            metadata={"profile": self.profile_name, "serialized": serialized.get("name") or serialized.get("id")},
        )

    def _latency(self, run_key: str) -> float:
        """返回毫秒耗时；如果没有 start 事件，则返回 0，避免 callback 异常影响主流程。"""
        started_at = self._starts.pop(run_key, None)
        if started_at is None:
            return 0.0
        return round((time.perf_counter() - started_at) * 1000, 2)


class LLMRouter:
    """
    进程级模型路由器。

    Router 维护 profile 配置和懒加载后的模型实例。业务代码只拿语义化 profile 名，
    不直接关心底层是 qwen、openai、ollama 还是 vLLM。

    目前只做“profile + 调用日志”，故意不做负载均衡、熔断和动态路由，避免第一阶段过度设计。
    """

    def __init__(self):
        # 环境变量命名约定：FAST_LLM_* / REASONING_LLM_* / CRITIC_LLM_*。
        # 如果没有单独配置，就回退到全局 LLM_* 或项目默认值。
        self.profiles = {
            "fast_model": self._profile("FAST", default_model=os.getenv("LLM_MODEL", "qwen-max"), default_temperature=0.2),
            "reasoning_model": self._profile("REASONING", default_model=os.getenv("LLM_MODEL", "qwen-max"), default_temperature=0.2),
            "critic_model": self._profile("CRITIC", default_model=os.getenv("LLM_MODEL", "qwen-max"), default_temperature=0.0),
        }
        # 懒加载缓存：只有某个 profile 第一次被使用时才创建实际 ChatModel。
        self._models: Dict[str, Any] = {}

    def get(self, profile_name: str = "reasoning_model"):
        """按 profile 名获取 ChatModel。返回值必须保持 DeepAgents 可接受的原始模型对象。"""
        if profile_name not in self.profiles:
            raise KeyError(f"Unknown LLM profile: {profile_name}")
        if profile_name not in self._models:
            self._models[profile_name] = self._build_model(self.profiles[profile_name])
        return self._models[profile_name]

    def _profile(self, prefix: str, default_model: str, default_temperature: float) -> ModelProfile:
        """按环境变量前缀构建 profile，集中处理默认值和类型转换。"""
        def profile_env(name: str, fallback: str) -> str:
            primary = os.getenv(f"{prefix}_LLM_{name}")
            if primary is not None:
                return primary
            return os.getenv(f"LLM_{name}", fallback)

        return ModelProfile(
            name=prefix.lower(),
            model=profile_env("MODEL", default_model),
            provider=profile_env("PROVIDER", "openai"),
            timeout=float(profile_env("TIMEOUT_SECONDS", "180")),
            max_retries=int(profile_env("MAX_RETRIES", "3")),
            temperature=float(profile_env("TEMPERATURE", str(default_temperature))),
        )

    def _build_model(self, profile: ModelProfile):
        """
        构造 LangChain ChatModel。

        注意：不要在这里返回 model.with_config(...)。
        DeepAgents 的 create_deep_agent 会识别原始 ChatModel；RunnableBinding 在当前版本会被
        DeepAgents 当成字符串模型 spec 解析，导致启动失败。
        """
        kwargs: Dict[str, Any] = {
            "model": profile.model,
            "model_provider": profile.provider,
            "timeout": profile.timeout,
            "max_retries": profile.max_retries,
            "callbacks": [LLMTracingCallback(profile.name)],
        }
        if profile.temperature is not None:
            kwargs["temperature"] = profile.temperature
        return init_chat_model(**kwargs)


def _extract_token_usage(response: Any) -> Dict[str, Optional[int]]:
    """
    从不同 provider 的响应里尽量提取 token 用量。

    OpenAI-compatible、LangChain provider profile 和不同 SDK 版本的字段名不完全相同，
    所以这里做宽松解析；拿不到就返回 None，不影响主流程。
    """
    usage = getattr(response, "llm_output", None) or {}
    token_usage = usage.get("token_usage") or usage.get("usage") or {}
    input_tokens = token_usage.get("prompt_tokens") or token_usage.get("input_tokens")
    output_tokens = token_usage.get("completion_tokens") or token_usage.get("output_tokens")
    if input_tokens is None and "total_tokens" in token_usage and output_tokens is not None:
        input_tokens = token_usage["total_tokens"] - output_tokens
    return {"input": input_tokens, "output": output_tokens}


# 全局单例：旧入口 agent.llm 会从这里拿模型，未来其他模块也优先通过它取模型。
llm_router = LLMRouter()