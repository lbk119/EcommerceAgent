"""LLM 后端与语义 profile 路由。

运行时代码只按 fast、standard、deep、critic 这些语义档位请求模型，不直接关心具体 provider、模型名、
超时和重试策略。本文件负责懒加载 LangChain chat model，并把模型调用耗时、token 和错误写入 trace。
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

from dotenv import find_dotenv, load_dotenv
from langchain.chat_models import init_chat_model
from langchain_core.callbacks import BaseCallbackHandler

from agent.runtime.runtime_context import current_runtime_context
from agent.trace.tracer import tracer


load_dotenv(find_dotenv())


@dataclass(frozen=True)
class ModelProfile:
    """单个语义模型档位解析后的不可变配置。"""

    name: str
    model: str
    provider: str = "openai"
    timeout: float = 180.0
    max_retries: int = 3
    temperature: Optional[float] = None


class LLMTracingCallback(BaseCallbackHandler):
    """把 LangChain 模型回调转换为项目统一 trace 事件。"""

    def __init__(self, profile: ModelProfile):
        self.profile = profile
        self.profile_name = profile.name
        self._starts: Dict[str, float] = {}

    def on_chat_model_start(self, serialized: Dict[str, Any], messages: Any, *, run_id: Any, **kwargs: Any) -> None:
        self._start(str(run_id), serialized, messages)

    def on_llm_start(self, serialized: Dict[str, Any], prompts: Any, *, run_id: Any, **kwargs: Any) -> None:
        self._start(str(run_id), serialized, prompts)

    def on_llm_end(self, response: Any, *, run_id: Any, **kwargs: Any) -> None:
        run_key = str(run_id)
        token_usage = _extract_token_usage(response)
        context = current_runtime_context()
        tracer.emit(
            "llm_call_finished",
            trace_id=context.trace_id,
            task_id=context.task_id,
            conversation_id=context.conversation_id,
            agent_name=self.profile_name,
            latency_ms=self._latency(run_key),
            token_input=token_usage.get("input"),
            token_output=token_usage.get("output"),
            metadata=self._trace_metadata(),
        )

    def on_llm_error(self, error: BaseException, *, run_id: Any, **kwargs: Any) -> None:
        run_key = str(run_id)
        context = current_runtime_context()
        tracer.emit(
            "llm_call_failed",
            trace_id=context.trace_id,
            task_id=context.task_id,
            conversation_id=context.conversation_id,
            agent_name=self.profile_name,
            latency_ms=self._latency(run_key),
            error=str(error)[:1000],
            metadata=self._trace_metadata(),
        )

    def _start(self, run_key: str, serialized: Dict[str, Any], prompt_payload: Any) -> None:
        self._starts[run_key] = time.perf_counter()
        context = current_runtime_context()
        metadata = self._trace_metadata()
        metadata.update(
            {
                "serialized": serialized.get("name") or serialized.get("id"),
                "prompt_chars_estimate": _estimate_prompt_chars(prompt_payload),
            }
        )
        tracer.emit(
            "llm_call_started",
            trace_id=context.trace_id,
            task_id=context.task_id,
            conversation_id=context.conversation_id,
            agent_name=self.profile_name,
            metadata=metadata,
        )

    def _latency(self, run_key: str) -> float:
        started_at = self._starts.pop(run_key, None)
        if started_at is None:
            return 0.0
        return round((time.perf_counter() - started_at) * 1000, 2)

    def _trace_metadata(self) -> Dict[str, Any]:
        return {
            "profile": self.profile_name,
            "model_profile": self.profile_name,
            "model_name": self.profile.model,
            "timeout_seconds": self.profile.timeout,
            "retry_count": self.profile.max_retries,
            "streaming_enabled": False,
        }


class LLMRouter:
    """进程内懒加载模型路由器。"""

    def __init__(self):
        fast_model = os.getenv("LLM_FAST_MODEL") or "gpt-5.4-mini"
        deep_model = os.getenv("LLM_DEEP_MODEL") or "gpt-5.5"
        self.profiles: Dict[str, ModelProfile] = {
            "fast": self._profile(
                name="fast",
                canonical_prefix="LLM_FAST",
                default_model=fast_model,
                default_timeout=8,
                default_retries=1,
                default_temperature=0.2,
                timeout_aliases=("AI_CHAT_LLM_TIMEOUT_SECONDS",),
                retry_aliases=("AI_CHAT_LLM_MAX_RETRIES",),
            ),
            "standard": self._profile(
                name="standard",
                canonical_prefix="LLM_FAST",
                default_model=fast_model,
                default_timeout=20,
                default_retries=1,
                default_temperature=0.2,
            ),
            "deep": self._profile(
                name="deep",
                canonical_prefix="LLM_DEEP",
                default_model=deep_model,
                default_timeout=60,
                default_retries=1,
                default_temperature=0.2,
            ),
            "critic": self._profile(
                name="critic",
                canonical_prefix="LLM_FAST",
                default_model=fast_model,
                default_timeout=15,
                default_retries=0,
                default_temperature=0.0,
            ),
        }
        self._models: Dict[str, Any] = {}

    def model(self, profile_name: str = "standard"):
        """Return a lazy LangChain ChatModel for the semantic profile."""
        normalized = profile_name.removesuffix("_model")
        if normalized not in self.profiles:
            raise KeyError(f"Unknown LLM profile: {profile_name}")
        if normalized not in self._models:
            self._models[normalized] = self._build_model(self.profiles[normalized])
        return self._models[normalized]

    def get(self, profile_name: str = "standard_model"):
        """Backward-compatible alias for callers that still pass *_model names."""
        return self.model(profile_name)

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
        model = os.getenv(f"{canonical_prefix}_MODEL") or default_model
        provider = os.getenv("LLM_PROVIDER") or "openai"
        timeout = _first_env(*timeout_aliases, f"{canonical_prefix}_TIMEOUT_SECONDS")
        retries = _first_env(*retry_aliases, f"{canonical_prefix}_MAX_RETRIES")
        temperature = _first_env(f"{canonical_prefix}_TEMPERATURE", "LLM_TEMPERATURE")
        return ModelProfile(
            name=name,
            model=model,
            provider=provider,
            timeout=float(timeout or default_timeout),
            max_retries=int(retries or default_retries),
            temperature=float(temperature or default_temperature),
        )

    def _build_model(self, profile: ModelProfile):
        kwargs: Dict[str, Any] = {
            "model": profile.model,
            "model_provider": profile.provider,
            "timeout": profile.timeout,
            "max_retries": profile.max_retries,
            "callbacks": [LLMTracingCallback(profile)],
        }
        if profile.temperature is not None:
            kwargs["temperature"] = profile.temperature
        return init_chat_model(**kwargs)


def _extract_token_usage(response: Any) -> Dict[str, Optional[int]]:
    usage = getattr(response, "llm_output", None) or {}
    token_usage = usage.get("token_usage") or usage.get("usage") or {}
    input_tokens = token_usage.get("prompt_tokens") or token_usage.get("input_tokens")
    output_tokens = token_usage.get("completion_tokens") or token_usage.get("output_tokens")
    if input_tokens is None and "total_tokens" in token_usage and output_tokens is not None:
        input_tokens = token_usage["total_tokens"] - output_tokens
    return {"input": input_tokens, "output": output_tokens}


def _estimate_prompt_chars(prompt_payload: Any) -> int:
    try:
        if isinstance(prompt_payload, str):
            return len(prompt_payload)
        if isinstance(prompt_payload, list):
            return sum(_estimate_prompt_chars(item) for item in prompt_payload)
        content = getattr(prompt_payload, "content", None)
        if content is not None:
            return len(str(content))
        return len(str(prompt_payload))
    except Exception:
        return 0


def _first_env(*names: str) -> Optional[str]:
    for name in names:
        value = os.getenv(name)
        if value:
            return value
    return None


llm_router = LLMRouter()
