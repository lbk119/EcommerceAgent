"""
窄口径 CriticAgent。

Critic 不替代主 Agent，也不审查所有输出。它只针对高价值经营任务做结构化质量校验，
例如经营日报、SQL/写库、活动复盘、库存补货、退款异常分析。

第一阶段 Critic 的职责是“发现明显缺项并给修复指令”，不是自动无限重试。
"""

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List

from agent.critic.policy import evaluate_critic_policy
from agent.observability.tracer import tracer


@dataclass(frozen=True)
class CriticIssue:
    """Critic 发现的单个问题。"""

    type: str
    message: str


@dataclass(frozen=True)
class CriticResult:
    """Critic 的结构化输出。"""

    passed: bool
    issues: List[CriticIssue] = field(default_factory=list)
    fix_instruction: str = ""
    raw: Dict[str, Any] = field(default_factory=dict)


def critic_required_for_task(query: str) -> bool:
    """兼容旧调用方：实际策略已迁移到 agent.critic.policy。"""
    return evaluate_critic_policy(query).required


async def run_critic(task_query: str, final_result: str, *, trace_id: str, task_id: str, conversation_id: str) -> CriticResult:
    """调用 critic_model，对最终结果做结构化校验。

    Critic 失败时不会让主任务直接失败，而是返回 passed=True 并把错误写入 raw/trace。
    这样可以避免“审查模型异常”掩盖主 Agent 已经完成的业务结果。
    """
    tracer.emit("critic_started", trace_id=trace_id, task_id=task_id, conversation_id=conversation_id, agent_name="critic_agent")
    try:
        from agent.llm import get_critic_model

        response = await get_critic_model().ainvoke(_build_critic_prompt(task_query, final_result))
        result = _parse_critic_response(response)
        tracer.emit(
            "critic_finished",
            trace_id=trace_id,
            task_id=task_id,
            conversation_id=conversation_id,
            agent_name="critic_agent",
            metadata={"passed": result.passed, "issue_count": len(result.issues)},
        )
        if not result.passed:
            # 未通过时额外发 critic_failed，AgentRuntime 可据此生成修复指令或触发一次 retry。
            tracer.emit(
                "critic_failed",
                trace_id=trace_id,
                task_id=task_id,
                conversation_id=conversation_id,
                agent_name="critic_agent",
                metadata={"issues": [issue.__dict__ for issue in result.issues], "fix_instruction": result.fix_instruction},
            )
        return result
    except Exception as error:
        tracer.emit(
            "critic_failed",
            trace_id=trace_id,
            task_id=task_id,
            conversation_id=conversation_id,
            agent_name="critic_agent",
            error=str(error)[:1000],
            metadata={"reason": "critic_runtime_error"},
        )
        return CriticResult(True, raw={"error": str(error)})


def _build_critic_prompt(task_query: str, final_result: str) -> str:
    """构造 Critic 提示词。

    final_result 截断到 8000 字符，避免 Critic 因主答案过长导致上下文成本过高或超限。
    """
    return f"""
你是电商运营 Agent 的 Critic，只负责质量校验，不要重写答案。

请判断最终输出是否满足用户任务，重点检查：
1. 经营日报是否包含 GMV、订单数、客单价、评分、退款、客服风险等必要指标。
2. SQL/写入类任务是否说明数据来源、影响范围和审核/候选状态。
3. 活动复盘是否包含曝光、点击、转化、GMV、退款或 ROI。
4. 库存补货建议是否包含风险商品、库存/安全库存、建议动作。
5. 退款异常分析是否包含退款率、异常原因或后续排查建议。

用户任务：
{task_query}

最终输出：
{final_result[:8000]}

只返回 JSON，不要 Markdown。格式：
{{
  "passed": true,
  "issues": [{{"type": "data_missing", "message": "缺少退款率数据"}}],
  "fix_instruction": "重新查询 refunds 表并补充退款率"
}}
"""


def _parse_critic_response(response: Any) -> CriticResult:
    """解析 Critic 的 JSON 响应，兼容 Markdown fenced code block。"""
    content = response.content if hasattr(response, "content") else str(response)
    content = content.strip()
    if content.startswith("```"):
        content = content.strip("`")
        if content.lower().startswith("json"):
            content = content[4:].strip()
    data = json.loads(content)
    issues = [CriticIssue(type=str(item.get("type", "unknown")), message=str(item.get("message", ""))) for item in data.get("issues", [])]
    return CriticResult(
        passed=bool(data.get("passed", not issues)),
        issues=issues,
        fix_instruction=str(data.get("fix_instruction", "")),
        raw=data,
    )