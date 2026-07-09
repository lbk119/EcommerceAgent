"""PlannerAgent 顶层规划器。

PlannerAgent 只负责理解用户需求、选择业务 capability、生成 subagent assignment 和依赖关系。
它不执行工具、不写 SQL、不产出最终经营结论；deepagents-native main agent 会把这些规划信息作为
调度策略参考，真正的业务执行由 subagents 和受控 tools 完成。
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

from agent.plan.models import AgentAssignment, AgentDependency, AgentTaskPlan
from agent.trace.tracer import tracer
from agent.runtime.profiles import normalize_runtime_profile


PLANNER_PROMPT = """
你是电商运营 Agent Planner。你只做规划，不执行工具，不写 SQL，不输出 Markdown。
你只能从 CapabilityRegistry 中选择 capability，并把它分派给存在的 deepagents business subagent。
不能编造系统不存在的 subagent、AgentAssignment 或 intent。
如果需求不清楚，requires_clarification=true，并给出 clarification_questions。
如果超出电商经营范围，boundary=true，并给出 boundary_reason。
如果 profile=realtime，禁止分派只支持 standard/deep 的业务 subagent。
数据导入、数据质量、平台授权和同步问题分派给 data_quality subagent。
每个 AgentAssignment.task 必须是面向该业务 subagent 的清晰任务改写，不要简单复制原始用户输入。
每个 AgentAssignment 必须包含 original_query、assignment_scope、objective、expected_contribution、upstream_requirements。
Planner 可以输出自然语言约束，但不要输出 time_range、limit、category 等具体工具参数，也不要生成工具调用步骤或工具名。
如果下游需要上游结果，输出 dependencies，并把同样的上游 assignment_id 写入下游 depends_on。
输出必须是严格 JSON，符合 AgentTaskPlan schema，不要包含注释或额外文本。
""".strip()


@dataclass(frozen=True)
class Capability:
    """系统当前可规划能力。"""

    capability_id: str
    target_agent_id: str
    intent: str
    aliases: tuple[str, ...]
    required_context: tuple[str, ...]
    supported_profiles: tuple[str, ...] = ("realtime", "standard", "deep")
    clarification_policy: str = "none"
    output_requirement: str = "输出结构化经营分析结论。"

    def to_prompt_dict(self) -> dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "target_agent_id": self.target_agent_id,
            "intent": self.intent,
            "aliases": list(self.aliases),
            "required_context": list(self.required_context),
            "supported_profiles": list(self.supported_profiles),
            "clarification_policy": self.clarification_policy,
            "output_requirement": self.output_requirement,
        }


@dataclass(frozen=True)
class CapabilityMatch:
    capability: Capability
    score: int


@dataclass
class CapabilityRegistry:
    """Central business capability registry for planner fallback matching."""

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
            for text in capability.aliases:
                token = _normalize(text)
                if token and token in normalized:
                    score += 3
                else:
                    score += len(set(_tokens(token)) & set(_tokens(normalized)))
            if score > 0:
                matches.append(CapabilityMatch(capability, score))
        return sorted(matches, key=lambda item: item.score, reverse=True)[:limit]


CAPABILITIES: tuple[Capability, ...] = (
    Capability("hot_product_analysis", "product_analysis", "hot_product_analysis", ("推荐我最近爆品", "哪些商品适合加大投放", "top product", "爆品", "热销", "畅销", "加大投放", "放量", "bestseller", "topproduct", "商品分析", "商品分", "商品情况", "product analysis"), ("orders", "traffic_stats", "inventory", "refunds"), output_requirement="输出爆品候选、放量原因、库存承接、活动承接和风险。"),
    Capability("product_optimization", "product_analysis", "product_optimization", ("哪个商品最值得优化", "帮我优化商品转化", "低转化商品怎么改", "商品优化", "值得优化", "低转化", "标题", "主图", "价格", "转化", "which product should i optimize", "product optimization", "optimize product", "optimize products", "conversion optimization", "low conversion product"), ("orders", "traffic_stats", "inventory"), output_requirement="输出优先优化商品、原因、标题/价格/库存/活动动作。"),
    Capability("inventory_analysis", "inventory", "inventory_analysis", ("库存风险优先级", "哪些 SKU 要补货", "安全库存够不够", "哪些商品低于安全库存", "库存", "补货", "安全库存", "缺货", "滞销", "低于安全库存", "inventory"), ("inventory", "orders"), output_requirement="输出风险 SKU、补货优先级、缺货/滞销原因和动作。"),
    Capability("inventory_warning", "inventory", "inventory_analysis", ("库存预警", "库存告警"), ("inventory",), output_requirement="输出库存告警、风险等级和处理动作。"),
    Capability("replenishment_plan", "inventory", "replenishment_plan", ("给我补货计划", "哪些商品要补多少", "补货计划", "补多少", "采购", "备货"), ("inventory", "orders"), output_requirement="输出补货 SKU、建议数量、优先级和依据。"),
    Capability("campaign_review", "campaign", "campaign_review", ("复盘这个月活动", "活动 ROI 怎么样", "投放效果分析", "活动", "活动复盘", "投放", "roi", "转化", "campaign"), ("campaigns", "campaign_product_stats", "inventory", "refunds"), clarification_policy="campaign_identifier_when_vague", output_requirement="输出活动流量、成交、ROI、风险和下一轮优化。"),
    Capability("daily_report", "report", "daily_report", ("帮我看看店铺最近怎么样", "生成经营日报", "最近经营情况", "日报", "经营分析", "店铺最新情况", "最近怎么样", "经营情况", "诊断", "dailyreport"), ("orders", "reviews", "refunds", "inventory"), output_requirement="输出核心指标、风险、重点商品和运营动作。"),
    Capability("weekly_report", "report", "weekly_report", ("生成周报", "本周经营情况", "周报", "本周", "weekly"), ("orders", "reviews", "refunds"), output_requirement="输出周度核心指标、变化和下周动作。"),
    Capability("seasonal_selection", "product_analysis", "seasonal_selection", ("这个季节适合卖什么", "夏天卖什么", "应季选品", "选品", "应季", "季节", "上新", "适合卖什么", "趋势", "seasonal product selection", "seasonal products", "product selection", "seasonal assortment", "summer products", "products for this season"), ("orders", "inventory", "campaigns"), output_requirement="输出应季选品方向、候选商品、库存承接和活动建议。"),
    Capability("general_business_chat", "report", "general_business_chat", ("帮我看看业务", "给我经营建议", "经营", "店铺", "业务", "运营"), ("orders", "inventory"), output_requirement="输出经营诊断、主要风险和下一步动作。"),
    Capability("data_quality_check", "data_quality", "data_quality_check", ("数据导入失败怎么办", "检查数据质量", "为什么没有数据", "数据导入", "导入", "数据质量", "没有数据", "平台授权", "授权", "同步"), ("platform_integration", "import_jobs"), supported_profiles=("standard", "deep"), output_requirement="输出数据质量、导入状态、授权状态和修复动作。"),
    Capability("platform_integration_help", "data_quality", "platform_integration_help", ("怎么授权店铺", "平台同步失败", "授权", "集成", "同步失败", "店铺连接", "平台"), ("platform_integration",), supported_profiles=("standard", "deep"), output_requirement="输出平台授权/同步状态和排障动作。"),
)


class PlannerAgent:
    """顶层规划 Agent。"""

    def __init__(self, registry: CapabilityRegistry | None = None):
        self.registry = registry or CapabilityRegistry()

    async def plan_async(self, query: str, *, profile: str = "standard", context: dict[str, Any] | None = None, trace_id: str = "", task_id: str = "", conversation_id: str = "") -> AgentTaskPlan:
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
            tracer.emit("planner_finished", trace_id=trace_id or task_id, task_id=task_id, conversation_id=conversation_id, agent_name="planner_agent", latency_ms=round((time.perf_counter() - started_at) * 1000, 2), metadata={"profile": normalized_profile, "plan": fallback_plan.to_dict(), "source": "planner_fallback", "error": str(error)[:500]})
            return fallback_plan

    def plan(self, query: str, *, profile: str = "standard", context: dict[str, Any] | None = None, fallback_reason: str = "rule_based_agent_fallback") -> AgentTaskPlan:
        """同步确定性规划入口，供 AI Chat 受理和兼容分类器使用。"""
        normalized_profile = normalize_runtime_profile(profile)
        return self._fallback_plan(query, profile=normalized_profile, context=context or {}, fallback_reason=fallback_reason)

    async def _plan_with_model(self, query: str, *, profile: str, context: dict[str, Any]) -> AgentTaskPlan:
        if os.getenv("PLANNER_AGENT_DISABLE_LLM", "false").lower() in {"1", "true", "yes", "on"}:
            raise RuntimeError("planner llm disabled")
        from agent.llm import get_fast_model

        prompt = self._build_prompt(query, profile=profile, context=context)
        timeout_seconds = float(os.getenv("PLANNER_AGENT_TIMEOUT_SECONDS", "3" if profile == "realtime" else "5"))
        response = await asyncio.wait_for(get_fast_model().ainvoke(prompt), timeout=timeout_seconds)
        content = response.content if hasattr(response, "content") else str(response)
        return AgentTaskPlan.from_dict(_loads_json_object(content))

    def _build_prompt(self, query: str, *, profile: str, context: dict[str, Any]) -> str:
        return "\n\n".join([
            PLANNER_PROMPT,
            "AgentTaskPlan schema keys: plan_id, raw_query, primary_intent, profile, assignments, dependencies, merge_strategy, requires_clarification, clarification_questions, boundary, boundary_reason, confidence, expected_output, metadata.",
            "AgentAssignment keys: assignment_id, agent_id, task, intent, original_query, assignment_scope, objective, expected_contribution, upstream_requirements, priority, reason, input_context, constraints, required_output, depends_on.",
            "AgentDependency.type allowed values: blocking_data_dependency, optional_context, ordering_only, conflict_check.",
            "Allowed business subagent ids: product_analysis, inventory, campaign, report, data_quality, knowledge_base, network_search, database_query.",
            f"Runtime profile: {profile}",
            f"CapabilityRegistry: {json.dumps(self.registry.summary(), ensure_ascii=False)}",
            f"Runtime context: {json.dumps(context, ensure_ascii=False, default=str)}",
            f"User query: {query}",
        ])

    def _fallback_plan(self, query: str, *, profile: str, context: dict[str, Any], fallback_reason: str) -> AgentTaskPlan:
        normalized = _normalize(query)
        if _is_out_of_scope(normalized):
            return _boundary_plan(query, profile=profile, reason="out_of_scope")
        matches = self.registry.match(query)
        if _needs_campaign_clarification(normalized, matches):
            return _clarification_plan(query, profile=profile, question="请告诉我要分析哪个活动，例如活动名称、活动 ID，或说明要复盘最近一次/本月活动。")
        selected = _select_capabilities(matches, query)
        if not selected:
            if profile == "realtime":
                return _realtime_chat_plan(query, profile=profile, fallback_reason=fallback_reason)
            selected = [self.registry.get("daily_report") or self.registry.capabilities[0]]
        if profile == "realtime":
            selected = [capability for capability in selected if "realtime" in capability.supported_profiles]
            if not selected:
                return _boundary_plan(query, profile=profile, reason="realtime_profile_not_supported")
        selected = _dedupe_capabilities_by_agent(selected)
        constraints = _extract_constraints(query)
        assignments = [_assignment_for_capability(capability, index=index, query=query, context=context, constraints=constraints) for index, capability in enumerate(selected, start=1)]
        dependencies = _assignment_dependencies(assignments, query=query)
        assignments = _sync_assignment_dependencies(assignments, dependencies)
        primary_intent = _primary_intent(selected)
        confidence = 0.72 if fallback_reason == "rule_based_agent_fallback" else 0.62
        return AgentTaskPlan(
            plan_id=_plan_id(query, profile),
            raw_query=query,
            primary_intent=primary_intent,
            profile=profile,
            assignments=assignments,
            dependencies=dependencies,
            merge_strategy=_merge_strategy(assignments),
            expected_output="；".join(dict.fromkeys(cap.output_requirement for cap in selected)),
            confidence=confidence,
            metadata={
                "normalized_query": normalized,
                "constraints": constraints,
                "output_format": _extract_output_format(query),
                "fallback_reason": fallback_reason,
                "risk_level": "medium" if any(cap.intent in {"campaign_review", "inventory_analysis", "replenishment_plan"} for cap in selected) else "low",
            },
        )

    def _sanitize_plan(self, plan: AgentTaskPlan, *, query: str, profile: str, fallback_reason: str) -> AgentTaskPlan:
        if profile == "realtime" and any(assignment.agent_id in {"network_search", "database_query", "data_quality", "knowledge_base"} for assignment in plan.assignments):
            return _boundary_plan(query, profile=profile, reason="realtime_profile_not_supported")
        known_agents = {"product_analysis", "inventory", "campaign", "report", "data_quality", "knowledge_base", "network_search", "database_query"}
        if any(assignment.agent_id not in known_agents for assignment in plan.assignments):
            return self._fallback_plan(query, profile=profile, context={}, fallback_reason="invalid_execution_mode")
        if not plan.assignments and not plan.boundary and not plan.requires_clarification:
            return self._fallback_plan(query, profile=profile, context={}, fallback_reason="model_plan_without_executable_steps")
        enriched_assignments = [_enrich_assignment(assignment, query=query) for assignment in plan.assignments]
        dependencies = _sanitize_dependencies(plan.dependencies, enriched_assignments)
        assignments = _sync_assignment_dependencies(enriched_assignments, dependencies)
        return AgentTaskPlan.from_dict({
            **plan.to_dict(),
            "plan_id": plan.plan_id or _plan_id(query, profile),
            "raw_query": query,
            "primary_intent": plan.primary_intent or _primary_intent_from_assignments(plan.assignments),
            "profile": profile,
            "assignments": [assignment.to_dict() for assignment in assignments],
            "dependencies": [dependency.to_dict() for dependency in dependencies],
            "metadata": {**plan.metadata, "fallback_reason": fallback_reason},
        })


def _select_capabilities(matches: list[CapabilityMatch], query: str) -> list[Capability]:
    if not matches:
        return []
    normalized = _normalize(query)
    selected: list[Capability] = []
    for match in matches:
        if match.score >= 2 and match.capability.capability_id not in {cap.capability_id for cap in selected}:
            selected.append(match.capability)
    if any(word in normalized for word in ("商品", "商品表现", "product")) and not any(cap.target_agent_id == "product_analysis" for cap in selected):
        selected.insert(0, _capability_by_id("hot_product_analysis"))
    if any(word in normalized for word in ("库存", "补货", "缺货", "inventory")) and not any(cap.target_agent_id == "inventory" for cap in selected):
        selected.append(_capability_by_id("inventory_analysis"))
    if any(word in normalized for word in ("活动", "投放", "roi", "campaign")) and not any(cap.target_agent_id == "campaign" for cap in selected):
        selected.append(_capability_by_id("campaign_review"))
    if any(word in normalized for word in ("同时", "一起", "并且", "以及", "加大投放", "活动风险", "投放风险", "活动投放风险")):
        for capability_id in ("hot_product_analysis", "inventory_analysis", "campaign_review"):
            capability = _capability_by_id(capability_id)
            if capability.capability_id not in {cap.capability_id for cap in selected}:
                selected.append(capability)
        selected = _sort_multi_agent_capabilities(selected)
    return selected[:4]


def _capability_by_id(capability_id: str) -> Capability:
    return next(capability for capability in CAPABILITIES if capability.capability_id == capability_id)


def _sort_multi_agent_capabilities(capabilities: list[Capability]) -> list[Capability]:
    preferred_agents = {"product_analysis": 0, "inventory": 1, "campaign": 2, "report": 3, "data_quality": 4, "knowledge_base": 5, "network_search": 6, "database_query": 7}
    return sorted(capabilities, key=lambda capability: preferred_agents.get(capability.target_agent_id, 99))


def _assignment_for_capability(capability: Capability, *, index: int, query: str, context: dict[str, Any], constraints: dict[str, Any]) -> AgentAssignment:
    scope = _assignment_scope(capability.target_agent_id)
    objective = _assignment_objective(capability)
    expected_contribution = _expected_contribution(capability)
    return AgentAssignment(
        assignment_id=f"a{index}_{capability.target_agent_id}",
        agent_id=capability.target_agent_id,
        task=_assignment_task(capability, query, constraints),
        intent=capability.intent,
        original_query=query,
        assignment_scope=scope,
        objective=objective,
        expected_contribution=expected_contribution,
        upstream_requirements=[],
        priority=index,
        reason=f"capability_match:{capability.capability_id}",
        input_context=dict(context or {}),
        constraints=constraints,
        required_output=capability.output_requirement,
        depends_on=[],
    )


def _enrich_assignment(assignment: AgentAssignment, *, query: str) -> AgentAssignment:
    capability = _capability_for_assignment(assignment)
    constraints = _extract_constraints(query)
    data = assignment.to_dict()
    if not data.get("task") or data.get("task") == query:
        data["task"] = _assignment_task(capability, query, constraints) if capability else f"围绕自身业务范围完成该子任务，输出证据、风险和建议。原始问题：{query}"
    data["original_query"] = data.get("original_query") or query
    data["assignment_scope"] = data.get("assignment_scope") or _assignment_scope(assignment.agent_id)
    data["objective"] = data.get("objective") or (_assignment_objective(capability) if capability else assignment.required_output)
    data["expected_contribution"] = data.get("expected_contribution") or (_expected_contribution(capability) if capability else assignment.required_output)
    data["constraints"] = {key: value for key, value in dict(data.get("constraints") or {}).items() if key not in {"time_range", "limit", "category"}}
    return AgentAssignment.from_dict(data)


def _capability_for_assignment(assignment: AgentAssignment) -> Capability | None:
    return next((capability for capability in CAPABILITIES if capability.target_agent_id == assignment.agent_id and capability.intent == assignment.intent), None) or next((capability for capability in CAPABILITIES if capability.target_agent_id == assignment.agent_id), None)


def _assignment_task(capability: Capability, query: str, constraints: dict[str, Any]) -> str:
    hint = str(constraints.get("scope_hint") or "用户问题中的业务范围")
    if capability.target_agent_id == "product_analysis":
        return f"围绕{hint}分析商品表现，识别热销、低转化和可优化商品，输出商品层面的证据、风险和建议。原始问题：{query}"
    if capability.target_agent_id == "inventory":
        return f"结合用户问题和可用上游商品候选，分析库存承接、缺货、滞销和补货风险，输出库存风险和补货建议。原始问题：{query}"
    if capability.target_agent_id == "campaign":
        return f"结合商品表现和库存承接情况，评估活动投放 ROI、流量承接和投放风险，输出投放建议和风险。原始问题：{query}"
    if capability.target_agent_id == "report":
        return f"围绕{hint}汇总经营指标、风险、重点商品和运营动作，形成面向最终回答的经营报告。原始问题：{query}"
    if capability.target_agent_id == "data_quality":
        return f"检查数据导入、同步、授权和数据新鲜度是否支撑后续经营分析，输出数据质量风险和修复动作。原始问题：{query}"
    if capability.target_agent_id == "database_query":
        return f"在租户和店铺范围内提供只读事实查询基础，输出可供下游业务 Agent 使用的结构化事实。原始问题：{query}"
    return f"围绕自身业务范围完成该子任务，输出可被最终回答引用的证据、风险和建议。原始问题：{query}"


def _assignment_scope(agent_id: str) -> str:
    return {
        "product_analysis": "商品表现、商品候选、低转化和商品优化",
        "inventory": "库存承接、缺货、滞销和补货风险",
        "campaign": "活动投放、ROI、流量承接和投放风险",
        "report": "经营报告汇总和管理层摘要",
        "data_quality": "数据导入、授权、同步和 schema 健康",
        "knowledge_base": "历史记忆、历史报告和策略候选",
        "network_search": "外部趋势和竞品上下文",
        "database_query": "受控只读数据库事实查询",
    }.get(agent_id, agent_id)


def _assignment_objective(capability: Capability) -> str:
    return {
        "product_analysis": "产出商品候选和商品风险",
        "inventory": "判断候选商品是否具备库存承接能力",
        "campaign": "判断是否适合继续投放或调整预算",
        "report": "形成可直接进入最终答案的经营汇总",
        "data_quality": "判断数据是否足以支撑后续经营分析",
        "database_query": "提供下游分析可引用的结构化事实基础",
    }.get(capability.target_agent_id, capability.output_requirement)


def _expected_contribution(capability: Capability) -> str:
    return {
        "product_analysis": "给库存和活动分析提供商品候选、商品表现摘要和商品层面证据",
        "inventory": "输出库存风险、补货建议和承接限制",
        "campaign": "输出活动投放建议、ROI 风险和预算调整依据",
        "report": "汇总多个领域 Agent 的结论并输出报告结构",
        "data_quality": "输出数据可用性、缺失环节和修复优先级",
        "database_query": "输出可被其他 Agent 引用的事实数据",
    }.get(capability.target_agent_id, capability.output_requirement)


def _dedupe_capabilities_by_agent(capabilities: list[Capability]) -> list[Capability]:
    selected: list[Capability] = []
    seen_agents: set[str] = set()
    for capability in capabilities:
        agent_id = capability.target_agent_id
        if agent_id in seen_agents:
            continue
        seen_agents.add(agent_id)
        selected.append(capability)
    return selected


def _assignment_dependencies(assignments: list[AgentAssignment], *, query: str) -> list[AgentDependency]:
    if _asks_for_independent_views(query):
        return []
    product = next((assignment.assignment_id for assignment in assignments if assignment.agent_id == "product_analysis"), "")
    inventory = next((assignment.assignment_id for assignment in assignments if assignment.agent_id == "inventory"), "")
    data_quality = next((assignment.assignment_id for assignment in assignments if assignment.agent_id == "data_quality"), "")
    database = next((assignment.assignment_id for assignment in assignments if assignment.agent_id == "database_query"), "")
    dependencies: list[AgentDependency] = []
    if product:
        for assignment in assignments:
            if assignment.agent_id == "inventory":
                dependencies.append(AgentDependency(from_assignment=product, to_assignment=assignment.assignment_id, type="blocking_data_dependency"))
            if assignment.agent_id == "campaign":
                dependencies.append(AgentDependency(from_assignment=product, to_assignment=assignment.assignment_id, type="blocking_data_dependency"))
    if inventory:
        for assignment in assignments:
            if assignment.agent_id == "campaign":
                dependencies.append(AgentDependency(from_assignment=inventory, to_assignment=assignment.assignment_id, type="optional_context"))
    for upstream in (data_quality, database):
        if upstream:
            for assignment in assignments:
                if assignment.assignment_id != upstream:
                    dependencies.append(AgentDependency(from_assignment=upstream, to_assignment=assignment.assignment_id, type="blocking_data_dependency"))
    if any(assignment.agent_id == "report" for assignment in assignments) and len(assignments) > 1:
        report_id = next(assignment.assignment_id for assignment in assignments if assignment.agent_id == "report")
        for assignment in assignments:
            if assignment.assignment_id != report_id:
                dependencies.append(AgentDependency(from_assignment=assignment.assignment_id, to_assignment=report_id, type="optional_context"))
    return _dedupe_dependencies(dependencies)


def _sync_assignment_dependencies(assignments: list[AgentAssignment], dependencies: list[AgentDependency]) -> list[AgentAssignment]:
    by_target: dict[str, list[str]] = {}
    requirements: dict[str, list[str]] = {}
    for dependency in dependencies:
        if dependency.type == "conflict_check":
            continue
        by_target.setdefault(dependency.to_assignment, []).append(dependency.from_assignment)
        requirements.setdefault(dependency.to_assignment, []).append(f"{dependency.type}:{dependency.from_assignment}")
    synced: list[AgentAssignment] = []
    for assignment in assignments:
        deps = list(dict.fromkeys([*assignment.depends_on, *by_target.get(assignment.assignment_id, [])]))
        upstream_requirements = list(dict.fromkeys([*assignment.upstream_requirements, *requirements.get(assignment.assignment_id, [])]))
        synced.append(AgentAssignment.from_dict({**assignment.to_dict(), "depends_on": deps, "upstream_requirements": upstream_requirements}))
    return synced


def _sanitize_dependencies(dependencies: list[AgentDependency], assignments: list[AgentAssignment]) -> list[AgentDependency]:
    assignment_ids = {assignment.assignment_id for assignment in assignments}
    allowed_types = {"blocking_data_dependency", "optional_context", "ordering_only", "conflict_check"}
    sanitized = [dependency for dependency in dependencies if dependency.from_assignment in assignment_ids and dependency.to_assignment in assignment_ids and dependency.from_assignment != dependency.to_assignment and dependency.type in allowed_types]
    return _dedupe_dependencies(sanitized)


def _dedupe_dependencies(dependencies: list[AgentDependency]) -> list[AgentDependency]:
    seen: set[tuple[str, str, str]] = set()
    unique: list[AgentDependency] = []
    for dependency in dependencies:
        key = (dependency.from_assignment, dependency.to_assignment, dependency.type)
        if key in seen:
            continue
        seen.add(key)
        unique.append(dependency)
    return unique


def _asks_for_independent_views(query: str) -> bool:
    normalized = _normalize(query)
    return any(token in normalized for token in ("分别", "各自", "独立", "分别看", "separately", "independently"))


def _merge_strategy(assignments: list[AgentAssignment]) -> str:
    agent_ids = {assignment.agent_id for assignment in assignments}
    if {"product_analysis", "inventory", "campaign"}.issubset(agent_ids):
        return "business_recommendation"
    if "report" in agent_ids:
        return "report_summary"
    return "single_agent_summary" if len(assignments) == 1 else "multi_agent_summary"


def _primary_intent(capabilities: list[Capability]) -> str:
    return capabilities[0].intent if capabilities else "general_business_chat"


def _primary_intent_from_assignments(assignments: list[AgentAssignment]) -> str:
    ordered = sorted(assignments, key=lambda item: item.priority)
    return ordered[0].intent if ordered else "general_business_chat"


def _boundary_plan(query: str, *, profile: str, reason: str) -> AgentTaskPlan:
    return AgentTaskPlan(
        plan_id=_plan_id(query, profile),
        raw_query=query,
        primary_intent="boundary",
        profile=profile,
        boundary=True,
        boundary_reason=reason,
        expected_output="返回能力边界说明和可继续的电商经营问题入口。",
        confidence=0.9,
        metadata={"normalized_query": _normalize(query), "fallback_reason": reason, "risk_level": "low", "business_domain": "outside_ecommerce_operations"},
    )


def _clarification_plan(query: str, *, profile: str, question: str) -> AgentTaskPlan:
    return AgentTaskPlan(
        plan_id=_plan_id(query, profile),
        raw_query=query,
        primary_intent="campaign_review",
        profile=profile,
        requires_clarification=True,
        clarification_questions=[question],
        expected_output="先向用户澄清活动范围。",
        confidence=0.8,
        metadata={"normalized_query": _normalize(query), "constraints": _extract_constraints(query), "risk_level": "medium", "missing_context": ["campaign_id_or_name"], "fallback_reason": "missing_campaign_context", "intent": "campaign_review"},
    )


def _realtime_chat_plan(query: str, *, profile: str, fallback_reason: str) -> AgentTaskPlan:
    return AgentTaskPlan(
        plan_id=_plan_id(query, profile),
        raw_query=query,
        primary_intent="realtime_chat",
        profile=profile,
        assignments=[],
        dependencies=[],
        merge_strategy="direct_chat",
        expected_output="直接回答普通聊天或轻量说明，不调用业务工具、数据库、文件系统、记忆写入或 subagent。",
        confidence=0.86,
        metadata={
            "normalized_query": _normalize(query),
            "fallback_reason": fallback_reason,
            "risk_level": "low",
            "chat_path": "realtime",
        },
    )


def _extract_constraints(query: str) -> dict[str, Any]:
    constraints: dict[str, Any] = {}
    if any(token in query for token in ("今天", "今日", "today")):
        constraints["scope_hint"] = "用户提到今天，业务 Agent 应在自己的工具参数中体现该范围。"
    elif any(token in query for token in ("昨天", "昨日", "yesterday")):
        constraints["scope_hint"] = "用户提到昨天，业务 Agent 应在自己的工具参数中体现该范围。"
    elif any(token in query for token in ("本周", "周报")):
        constraints["scope_hint"] = "用户提到本周，业务 Agent 应在自己的工具参数中体现该范围。"
    elif any(token in query for token in ("这个月", "本月", "月报")):
        constraints["scope_hint"] = "用户提到本月，业务 Agent 应在自己的工具参数中体现该范围。"
    match = re.search(r"(?:最近|近|last)(\d+)(?:天|d|day|days)?", query.lower())
    if match:
        constraints["scope_hint"] = f"用户提到最近 {match.group(1)} 天，业务 Agent 应在自己的工具参数中体现该范围。"
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
    vague = any(
        token in normalized
        for token in (
            "那个活动",
            "这个活动",
            "本次活动",
            "这次活动",
            "活动看看",
            "分析一下活动",
            "活动怎么样",
            "这个活动怎么样",
            "那个活动怎么样",
        )
    )
    if re.search(r"(那个|这个|本次|这次)活动(怎么样|看看|分析一下)?$", normalized) or re.search(r"(分析一下|看看)活动$", normalized):
        vague = True
    has_identifier = bool(re.search(r"(618|双十一|campaign[_-]?[a-z0-9]+|活动(id|ID)?[a-z0-9_-]+)", normalized))
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


