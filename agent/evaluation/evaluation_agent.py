"""Evaluation Agent 的窄口复核入口。

Evaluation 不替代主 Agent，也不会审查所有输出。它只针对高价值或高风险经营任务做结构化质量校验，
例如经营日报、SQL/写库、活动复盘、库存补货、退款异常分析等。

本阶段 Evaluation 的职责是发现明显缺项、识别证据不足和给出修复指令；它不会自动无限重试，
也不会绕过主运行时直接改写工具结果。
"""

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List

from agent.evaluation.evaluation_policy import evaluate_evaluation_policy
from agent.trace.tracer import tracer


@dataclass(frozen=True)
class EvaluationIssue:
    """Evaluation 发现的单个质量问题。"""

    type: str
    message: str


@dataclass(frozen=True)
class EvaluationResult:
    """Evaluation 的结构化复核结果。"""

    passed: bool
    issues: List[EvaluationIssue] = field(default_factory=list)
    fix_instruction: str = ""
    raw: Dict[str, Any] = field(default_factory=dict)


def evaluation_required_for_task(query: str) -> bool:
    """返回当前任务是否需要 Evaluation 质量复核。"""
    return evaluate_evaluation_policy(query).required


async def run_evaluation(task_query: str, final_result: str, *, trace_id: str, task_id: str, conversation_id: str) -> EvaluationResult:
    """调用 Evaluation 模型并返回结构化质量反馈。"""
    tracer.emit("evaluation_started", trace_id=trace_id, task_id=task_id, conversation_id=conversation_id, agent_name="evaluation_agent")
    try:
        from agent.llm import get_evaluation_model

        response = await get_evaluation_model().ainvoke(_build_evaluation_prompt(task_query, final_result))
        result = _parse_evaluation_response(response)
        tracer.emit(
            "evaluation_finished",
            trace_id=trace_id,
            task_id=task_id,
            conversation_id=conversation_id,
            agent_name="evaluation_agent",
            metadata={"passed": result.passed, "issue_count": len(result.issues)},
        )
        if not result.passed:
            tracer.emit(
                "evaluation_failed",
                trace_id=trace_id,
                task_id=task_id,
                conversation_id=conversation_id,
                agent_name="evaluation_agent",
                metadata={"issues": [issue.__dict__ for issue in result.issues], "fix_instruction": result.fix_instruction},
            )
        return result
    except Exception as error:
        tracer.emit(
            "evaluation_failed",
            trace_id=trace_id,
            task_id=task_id,
            conversation_id=conversation_id,
            agent_name="evaluation_agent",
            error=str(error)[:1000],
            metadata={"reason": "evaluation_runtime_error"},
        )
        return EvaluationResult(True, raw={"error": str(error)})


def _build_evaluation_prompt(task_query: str, final_result: str) -> str:
    """构造有长度边界的 Evaluation prompt。"""
    return f"""
你是电商运营 Agent Evaluation，只负责质量校验，不要重写答案。

请判断最终输出是否满足用户任务，重点检查：
1. 经营日报是否包含 GMV、订单数、客单价、评分、退款、客服风险等必要指标。
2. SQL/写入类任务是否说明数据来源、影响范围和审核/候选状态。
3. 活动复盘是否包含曝光、点击、转化、GMV、退款或 ROI。
4. 库存补货建议是否包含风险商品、库存、安全库存、建议动作。
5. 退款异常分析是否包含退款率、异常原因或后续排查建议。

用户任务：
{task_query}

最终输出：
{final_result[:8000]}

只返回 JSON，不输出 Markdown。格式：
{{
  "passed": true,
  "issues": [{{"type": "data_missing", "message": "缺少退款率数据"}}],
  "fix_instruction": "重新查询 refunds 表并补充退款率"
}}
"""


def _parse_evaluation_response(response: Any) -> EvaluationResult:
    """解析 Evaluation JSON 响应，兼容 Markdown fenced code block。"""
    content = response.content if hasattr(response, "content") else str(response)
    content = content.strip()
    if content.startswith("```"):
        content = content.strip("`")
        if content.lower().startswith("json"):
            content = content[4:].strip()
    data = json.loads(content)
    issues = [EvaluationIssue(type=str(item.get("type", "unknown")), message=str(item.get("message", ""))) for item in data.get("issues", [])]
    return EvaluationResult(
        passed=bool(data.get("passed", not issues)),
        issues=issues,
        fix_instruction=str(data.get("fix_instruction", "")),
        raw=data,
    )