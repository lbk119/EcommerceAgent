"""PlannerAgent 顶层规划器。

PlannerAgent 是全局调度大脑：接收用户原始需求，输出 TaskPlan/DAG/ExpertRoute。它不执行工具、
不拼 SQL、不做经营计算。实现采用三层规划：能力注册表、fast model JSON 规划、确定性 fallback。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any

from agent.observability.tracer import tracer
from agent.planning.schemas import ExpertRoute, PlanEdge, PlanStep, TaskIntent, TaskPlan
from agent.runtime.profiles import normalize_runtime_profile


PLANNER_PROMPT = """
你是电商运营多 Agent Planner。你只做规划，不执行工具，不写 SQL，不输出 Markdown。
你只能从 CapabilityRegistry 中选择 capability、expert、runtime 和 deterministic step。
不能编造系统不存在的 expert/tool/step。
如果需求不清楚，requires_clarification=true，并给出 clarification_questions。
如果超出电商经营范围，execution_mode=boundary。
如果 profile=realtime，禁止 execution_mode=deepagent。
输出必须是严格 JSON，符合 TaskPlan schema，不要包含注释或额外文本。
""".strip()


@dataclass(frozen=True)
class Capability:
    """系统当前可规划能力。"""

    capability_id: str
    description: str
    examples: tuple[str, ...]
    aliases: tuple[str, ...]
    required_data: tuple[str, ...]
    expert: str
    runtime: str
    deterministic_steps: tuple[str, ...]
    default_timeout: float
    supported_profiles: tuple[str, ...] = ("realtime", "standard", "deep")
    output_requirement: str = "输出结构化经营分析结论。"

    def to_prompt_dict(self) -> dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "description": self.description,
            "examples": list(self.examples[:3]),
            "aliases": list(self.aliases),
            "required_data": list(self.required_data),
            "expert": self.expert,
            "runtime": self.runtime,
            "deterministic_steps": list(self.deterministic_steps),
            "default_timeout": self.default_timeout,
            "supported_profiles": list(self.supported_profiles),
        }


@dataclass(frozen=True)
class CapabilityMatch:
    capability: Capability
    score: int


@dataclass
class CapabilityRegistry:
    """集中式领域能力注册表。

    fallback 规则只基于这里的 aliases/examples 做匹配，避免关键词 if/else 散落在分类器和 runtime 中。
    """

    capabilities: tuple[Capability, ...] = field(default_factory=lambda: CAPABILITIES)

    def get(self, capability_id: str) -> Capability | None:
        return next((capability for capability in self.capabilities if capability.capability_id == capability_id), None)

    def summary(self) -> list[dict[str, Any]]:
        return [capability.to_prompt_dict() for capability in self.capabilities]

    def match(self, query: str, *, limit: int = 4) -> list[CapabilityMatch]:
        normalized = _normalize(query)
        matches: list[CapabilityMatch] = []
        for capability in self.capabilities:
            score = 0
            for text in (*capability.aliases, *capability.examples):
                token = _normalize(text)
                if token and token in normalized:
                    score += 3 if text in capability.aliases else 2
                else:
                    score += len(set(_tokens(token)) & set(_tokens(normalized)))
            if score > 0:
                matches.append(CapabilityMatch(capability, score))
        return sorted(matches, key=lambda item: item.score, reverse=True)[:limit]


CAPABILITIES: tuple[Capability, ...] = (
    Capability("hot_product_analysis", "分析最近爆品和可放量商品。", ("推荐我最近爆品", "哪些商品适合加大投放", "top product"), ("爆品", "热销", "畅销", "加大投放", "放量", "bestseller", "topproduct"), ("orders", "traffic_stats", "inventory", "refunds"), "product_expert", "deterministic_workflow", ("query_hot_products", "query_campaign_roi", "query_inventory_velocity"), 3, output_requirement="输出爆品候选、放量原因、库存承接、活动承接和风险。"),
    Capability("product_optimization", "识别值得优化的商品和优化动作。", ("哪个商品最值得优化", "帮我优化商品转化", "低转化商品怎么办"), ("商品优化", "值得优化", "低转化", "标题", "主图", "价格", "转化"), ("orders", "traffic_stats", "inventory"), "product_expert", "deterministic_workflow", ("query_hot_products", "query_low_conversion_products", "query_inventory_velocity"), 3, output_requirement="输出优先优化商品、原因、标题/价格/库存/活动动作。"),
    Capability("inventory_analysis", "库存风险、缺货、滞销和补货分析。", ("库存风险优先级", "哪些 SKU 要补货", "安全库存够不够"), ("库存", "补货", "安全库存", "缺货", "滞销", "inventory"), ("inventory", "orders"), "inventory_expert", "deterministic_workflow", ("query_inventory_risks", "query_inventory_velocity", "query_sales_trend"), 3, output_requirement="输出风险 SKU、补货优先级、缺货/滞销原因和动作。"),
    Capability("inventory_warning", "库存预警。", ("库存预警", "哪些商品低于安全库存"), ("库存预警", "低于安全库存", "库存告警"), ("inventory",), "inventory_expert", "deterministic_workflow", ("query_inventory_risks", "query_inventory_velocity"), 3),
    Capability("replenishment_plan", "补货计划。", ("给我补货计划", "哪些商品要补多少"), ("补货计划", "补多少", "采购", "备货"), ("inventory", "orders"), "inventory_expert", "deterministic_workflow", ("query_inventory_velocity", "query_inventory_risks"), 3),
    Capability("campaign_review", "活动流量、成交、ROI 和风险复盘。", ("复盘这个月活动", "活动 ROI 怎么样", "投放效果分析"), ("活动", "活动复盘", "投放", "roi", "转化率", "campaign"), ("campaigns", "campaign_product_stats", "inventory", "refunds"), "campaign_expert", "deterministic_workflow", ("query_campaign_traffic", "query_campaign_roi", "query_campaign_risks"), 3, output_requirement="输出活动流量、成交、ROI、风险和下一轮优化。"),
    Capability("daily_report", "经营日报或近期店铺诊断。", ("帮我看看店铺最近怎么样", "生成经营日报", "最近经营情况"), ("日报", "经营分析", "店铺最近", "最近怎么样", "经营情况", "诊断", "dailyreport"), ("orders", "reviews", "refunds", "inventory"), "report_expert", "deterministic_workflow", ("query_daily_metrics", "query_daily_risks", "query_hot_products", "query_campaign_roi"), 3, output_requirement="输出核心指标、风险、重点商品和运营动作。"),
    Capability("weekly_report", "经营周报。", ("生成周报", "本周经营情况"), ("周报", "本周", "weekly"), ("orders", "reviews", "refunds"), "report_expert", "deterministic_workflow", ("query_daily_metrics", "query_daily_risks", "query_hot_products"), 3),
    Capability("seasonal_selection", "季节性选品和上新建议。", ("这个季节适合卖什么", "夏天卖什么", "应季选品"), ("选品", "应季", "季节", "上新", "适合卖什么", "趋势"), ("orders", "inventory", "campaigns"), "product_expert", "deterministic_workflow", ("query_shop_profile", "query_hot_products", "query_inventory_velocity", "query_campaign_roi"), 3),
    Capability("general_business_chat", "电商经营泛化诊断。", ("帮我看看业务", "给我经营建议"), ("经营", "店铺", "业务", "运营"), ("orders", "inventory"), "report_expert", "deterministic_workflow", ("query_daily_metrics", "query_daily_risks", "query_hot_products"), 3),
    Capability("data_quality_check", "数据导入、数据质量和平台数据状态检查。", ("数据导入失败怎么办", "检查数据质量", "为什么没有数据"), ("数据导入", "导入", "数据质量", "没有数据", "平台授权", "授权", "同步"), ("platform_integration", "import_jobs"), "data_expert", "database_agent", tuple(), 5, supported_profiles=("standard", "deep")),
    Capability("platform_integration_help", "平台授权和集成帮助。", ("怎么授权店铺", "平台同步失败"), ("授权", "集成", "同步失败", "店铺连接", "平台"), ("platform_integration",), "data_expert", "database_agent", tuple(), 5, supported_profiles=("standard", "deep")),
)


class PlannerAgent:
    """顶层规划 Agent。"""

    def __init__(self, registry: CapabilityRegistry | None = None):
        self.registry = registry or CapabilityRegistry()

    async def plan_async(self, query: str, *, profile: str = "standard", context: dict[str, Any] | None = None, trace_id: str = "", task_id: str = "", conversation_id: str = "") -> TaskPlan:
        normalized_profile = normalize_runtime_profile(profile)
        started_at = time.perf_counter()
        tracer.emit("planner_started", trace_id=trace_id or task_id, task_id=task_id, conversation_id=conversation_id, agent_name="planner_agent", metadata={"profile": normalized_profile, "query_chars": len(query)})
        try:
            plan = await self._plan_with_model(query, profile=normalized_profile, context=context or {})
            plan = self._sanitize_plan(plan, query=query, profile=normalized_profile, fallback_reason=plan.fallback_reason)
            tracer.emit("planner_finished", trace_id=trace_id or task_id, task_id=task_id, conversation_id=conversation_id, agent_name="planner_agent", latency_ms=round((time.perf_counter() - started_at) * 1000, 2), metadata={"profile": normalized_profile, "plan": plan.to_dict(), "source": "fast_model"})
            return plan
        except Exception as error:
            fallback_plan = self.plan(query, profile=normalized_profile, context=context, fallback_reason=f"planner_model_failed:{str(error)[:120]}")
            tracer.emit("planner_finished", trace_id=trace_id or task_id, task_id=task_id, conversation_id=conversation_id, agent_name="planner_agent", latency_ms=round((time.perf_counter() - started_at) * 1000, 2), metadata={"profile": normalized_profile, "plan": fallback_plan.to_dict(), "source": "deterministic_fallback", "error": str(error)[:500]})
            return fallback_plan

    def plan(self, query: str, *, profile: str = "standard", context: dict[str, Any] | None = None, fallback_reason: str = "deterministic_fallback") -> TaskPlan:
        """同步确定性规划入口，供 AI Chat 受理和兼容分类器使用。"""
        normalized_profile = normalize_runtime_profile(profile)
        return self._fallback_plan(query, profile=normalized_profile, context=context or {}, fallback_reason=fallback_reason)

    def replan(self, previous_plan: TaskPlan, *, critic_feedback: str = "", failed_steps: list[str] | None = None, missing_data: list[str] | None = None) -> TaskPlan:
        """Critic 反馈后的重规划接口。

        第一版只把失败 step 标为非并行重试候选，并把缺失数据写入 missing_context；后续可接 fast model。
        """
        failed = set(failed_steps or [])
        steps = [PlanStep(**{**step.to_dict(), "can_parallel": False, "fallback_strategy": "retry_then_degrade"}) if step.step_id in failed or step.name in failed else step for step in previous_plan.steps]
        return TaskPlan.from_dict({
            **previous_plan.to_dict(),
            "steps": [step.to_dict() for step in steps],
            "missing_context": [*previous_plan.missing_context, *(missing_data or [])],
            "fallback_reason": critic_feedback or "critic_replan_requested",
            "confidence": min(previous_plan.confidence, 0.65),
        })

    async def _plan_with_model(self, query: str, *, profile: str, context: dict[str, Any]) -> TaskPlan:
        if os.getenv("PLANNER_AGENT_DISABLE_LLM", "false").lower() in {"1", "true", "yes", "on"}:
            raise RuntimeError("planner llm disabled")
        from agent.llm import get_fast_model

        prompt = self._build_prompt(query, profile=profile, context=context)
        timeout_seconds = float(os.getenv("PLANNER_AGENT_TIMEOUT_SECONDS", "3" if profile == "realtime" else "5"))
        response = await asyncio.wait_for(get_fast_model().ainvoke(prompt), timeout=timeout_seconds)
        content = response.content if hasattr(response, "content") else str(response)
        return TaskPlan.from_dict(_loads_json_object(content))

    def _build_prompt(self, query: str, *, profile: str, context: dict[str, Any]) -> str:
        return "\n\n".join([
            PLANNER_PROMPT,
            "TaskPlan schema keys: plan_id, raw_query, intent, profile, execution_mode, steps, dependencies, expected_output, missing_context, requires_clarification, clarification_questions, confidence, fallback_reason, expert_routes, output_format.",
            "PlanStep keys: step_id, name, description, task_type, expert, tool_group, can_parallel, depends_on, timeout_seconds, critical, input_schema, output_schema, success_criteria, fallback_strategy.",
            f"Runtime profile: {profile}",
            f"CapabilityRegistry: {json.dumps(self.registry.summary(), ensure_ascii=False)}",
            f"Runtime context: {json.dumps(context, ensure_ascii=False, default=str)}",
            f"User query: {query}",
        ])

    def _fallback_plan(self, query: str, *, profile: str, context: dict[str, Any], fallback_reason: str) -> TaskPlan:
        normalized = _normalize(query)
        if _is_out_of_scope(normalized):
            return _boundary_plan(query, profile=profile, reason="out_of_scope")
        matches = self.registry.match(query)
        if _needs_campaign_clarification(normalized, matches):
            return _clarification_plan(query, profile=profile, question="请告诉我要分析哪个活动，例如活动名称、活动 ID，或说明要复盘最近一次/本月活动。")
        selected = _select_capabilities(matches, query)
        if not selected:
            selected = [self.registry.get("daily_report") or self.registry.capabilities[0]]
        if profile == "realtime":
            selected = [capability for capability in selected if "realtime" in capability.supported_profiles]
            if not selected:
                return _boundary_plan(query, profile=profile, reason="realtime_deepagent_forbidden")
        steps, edges = _build_steps(selected, profile=profile)
        if not steps:
            mode = "deepagent" if profile == "deep" else "boundary"
            if mode == "boundary":
                return _boundary_plan(query, profile=profile, reason="no_deterministic_steps")
        else:
            mode = "hybrid_plan" if len(selected) > 1 else "deterministic_dag"
        primary = selected[0]
        intent = TaskIntent(
            raw_query=query,
            normalized_query=normalized,
            primary_goal=primary.capability_id,
            constraints=_extract_constraints(query),
            output_format=_extract_output_format(query),
            urgency="high" if profile == "realtime" else "normal",
            risk_level="medium" if any(cap.capability_id in {"campaign_review", "inventory_analysis", "replenishment_plan"} for cap in selected) else "low",
            confidence=0.72 if fallback_reason == "deterministic_fallback" else 0.62,
        )
        routes = [_expert_route(capability, profile=profile) for capability in selected]
        return TaskPlan(
            plan_id=_plan_id(query, profile),
            raw_query=query,
            intent=intent,
            profile=profile,
            execution_mode=mode,
            steps=steps,
            dependencies=edges,
            expected_output="；".join(dict.fromkeys(cap.output_requirement for cap in selected)),
            confidence=intent.confidence,
            fallback_reason=fallback_reason,
            expert_routes=routes,
            output_format=intent.output_format,
        )

    def _sanitize_plan(self, plan: TaskPlan, *, query: str, profile: str, fallback_reason: str) -> TaskPlan:
        if profile == "realtime" and plan.execution_mode == "deepagent":
            return _boundary_plan(query, profile=profile, reason="realtime_deepagent_forbidden")
        if plan.execution_mode not in {"deterministic_dag", "hybrid_plan", "deepagent", "boundary"}:
            return self._fallback_plan(query, profile=profile, context={}, fallback_reason="invalid_execution_mode")
        known_steps = {step for capability in self.registry.capabilities for step in capability.deterministic_steps}
        sanitized_steps = [_sanitize_step_timeout(step, profile=profile) for step in plan.steps if step.name in known_steps]
        if plan.execution_mode in {"deterministic_dag", "hybrid_plan"} and not sanitized_steps:
            return self._fallback_plan(query, profile=profile, context={}, fallback_reason="model_plan_without_executable_steps")
        return TaskPlan.from_dict({
            **plan.to_dict(),
            "plan_id": plan.plan_id or _plan_id(query, profile),
            "raw_query": query,
            "profile": profile,
            "steps": [step.to_dict() for step in sanitized_steps],
            "fallback_reason": fallback_reason,
        })


def _select_capabilities(matches: list[CapabilityMatch], query: str) -> list[Capability]:
    if not matches:
        return []
    normalized = _normalize(query)
    selected: list[Capability] = []
    for match in matches:
        if match.score >= 2 and match.capability.capability_id not in {cap.capability_id for cap in selected}:
            selected.append(match.capability)
    if any(word in normalized for word in ("同时", "一起", "并且", "以及", "加大投放", "活动风险")):
        for capability_id in ("hot_product_analysis", "inventory_analysis", "campaign_review"):
            capability = next((match.capability for match in matches if match.capability.capability_id == capability_id), None)
            if capability and capability.capability_id not in {cap.capability_id for cap in selected}:
                selected.append(capability)
    return selected[:4]


def _build_steps(capabilities: list[Capability], *, profile: str) -> tuple[list[PlanStep], list[PlanEdge]]:
    steps: list[PlanStep] = []
    seen: set[str] = set()
    timeout_cap = _profile_step_timeout_cap(profile)
    for capability in capabilities:
        for step_name in capability.deterministic_steps:
            if step_name in seen:
                continue
            seen.add(step_name)
            steps.append(PlanStep(
                step_id=f"step_{len(steps) + 1}_{step_name}",
                name=step_name,
                description=f"执行 {capability.description} 所需的数据节点：{step_name}",
                task_type=capability.capability_id,
                expert=capability.expert,
                tool_group="business_metrics",
                can_parallel=True,
                timeout_seconds=min(capability.default_timeout, timeout_cap),
                critical=len(steps) == 0 or step_name in {"query_hot_products", "query_daily_metrics", "query_inventory_risks", "query_campaign_roi"},
                success_criteria=["返回至少一行结构化数据", "结果必须受 tenant/shop scope 限制"],
                fallback_strategy="deepagent" if profile == "deep" else "degrade",
            ))
    # 第一版执行层支持依赖，但 deterministic 数据读取默认并行；综合动作由 Reducer 承担。
    return steps, []


def _expert_route(capability: Capability, *, profile: str) -> ExpertRoute:
    return ExpertRoute(
        expert_id=capability.expert,
        expert_name={
            "product_expert": "商品分析专家",
            "inventory_expert": "库存补货专家",
            "campaign_expert": "活动复盘专家",
            "report_expert": "经营报告专家",
            "data_expert": "数据质量专家",
            "strategy_expert": "策略沉淀专家",
        }.get(capability.expert, "通用深度 Agent"),
        capability=capability.capability_id,
        runtime=capability.runtime,
        allowed_tools=list(capability.deterministic_steps),
        model_profile="fast" if profile in {"realtime", "standard"} else "deep",
    )


def _sanitize_step_timeout(step: PlanStep, *, profile: str) -> PlanStep:
    timeout_cap = _profile_step_timeout_cap(profile)
    current_timeout = step.timeout_seconds if step.timeout_seconds is not None else timeout_cap
    return PlanStep.from_dict({**step.to_dict(), "timeout_seconds": min(float(current_timeout), timeout_cap)})


def _profile_step_timeout_cap(profile: str) -> float:
    if profile == "realtime":
        return 2.5
    if profile == "deep":
        return 3.0
    return 2.0


def _boundary_plan(query: str, *, profile: str, reason: str) -> TaskPlan:
    intent = TaskIntent(raw_query=query, normalized_query=_normalize(query), business_domain="outside_ecommerce_operations", primary_goal="boundary", confidence=0.9)
    return TaskPlan(plan_id=_plan_id(query, profile), raw_query=query, intent=intent, profile=profile, execution_mode="boundary", expected_output="返回能力边界说明和可继续的电商经营问题入口。", confidence=0.9, fallback_reason=reason)


def _clarification_plan(query: str, *, profile: str, question: str) -> TaskPlan:
    intent = TaskIntent(raw_query=query, normalized_query=_normalize(query), primary_goal="campaign_review", constraints=_extract_constraints(query), risk_level="medium", confidence=0.8)
    return TaskPlan(plan_id=_plan_id(query, profile), raw_query=query, intent=intent, profile=profile, execution_mode="boundary", missing_context=["campaign_id_or_name"], requires_clarification=True, clarification_questions=[question], expected_output="先向用户澄清活动范围。", confidence=0.8, fallback_reason="missing_campaign_context")


def _extract_constraints(query: str) -> dict[str, Any]:
    constraints: dict[str, Any] = {}
    if any(token in query for token in ("今天", "今日", "today")):
        constraints["time_range"] = "today"
    elif any(token in query for token in ("昨天", "昨日", "yesterday")):
        constraints["time_range"] = "yesterday"
    elif any(token in query for token in ("本周", "周报")):
        constraints["time_range"] = "last_7d"
    elif any(token in query for token in ("这个月", "本月", "月报")):
        constraints["time_range"] = "last_30d"
    match = re.search(r"(?:最近|近|last)(\d+)(?:天|d|day|days)?", query.lower())
    if match:
        constraints["time_range"] = f"last_{match.group(1)}d"
    if any(token in query for token in ("表格", "excel", "csv")):
        constraints["format"] = "table"
    return constraints


def _extract_output_format(query: str) -> str:
    if any(token in query for token in ("表格", "excel", "csv")):
        return "table_markdown"
    return "markdown"


def _is_out_of_scope(normalized: str) -> bool:
    return any(token in normalized for token in ("天气", "气温", "下雨", "空气质量", "股票", "新闻", "电影"))


def _needs_campaign_clarification(normalized: str, matches: list[CapabilityMatch]) -> bool:
    has_campaign = any(match.capability.capability_id == "campaign_review" for match in matches)
    vague = any(token in normalized for token in ("那个活动", "这个活动", "活动看看", "分析一下活动", "活动怎么样"))
    has_identifier = bool(re.search(r"(618|双11|双十一|campaign|活动[\w\u4e00-\u9fff]{2,})", normalized))
    return has_campaign and vague and not has_identifier


def _loads_json_object(content: str) -> dict[str, Any]:
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text).strip()
        text = re.sub(r"```$", "", text).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("planner model did not return json object")
    data = json.loads(text[start:end + 1])
    if not isinstance(data, dict):
        raise ValueError("planner json is not object")
    return data


def _normalize(text: str) -> str:
    return re.sub(r"\s+", "", text.lower().replace("_", ""))


def _tokens(text: str) -> list[str]:
    return [token for token in re.split(r"[^a-z0-9\u4e00-\u9fff]+", text.lower()) if token]


def _plan_id(query: str, profile: str) -> str:
    digest = hashlib.sha256(f"{profile}:{query}".encode("utf-8")).hexdigest()[:12]
    return f"plan_{digest}"


planner_agent = PlannerAgent()
