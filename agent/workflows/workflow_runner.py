"""
业务 Workflow 路由与执行器。

这里不是通用 Planner，而是把 task_classifier 已经识别出的电商任务接到固定业务 DAG。这样高价值、
结构稳定的任务先走确定性流程，普通闲聊或未覆盖任务仍回落 DeepAgent。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Awaitable, Callable, Dict, Optional

from agent.observability.tracer import tracer
from agent.planning.task_classifier import TaskClassification
from agent.runtime.execution_result import ExecutionResult


DeepAgentExecutor = Callable[[str], Awaitable[str]]


@dataclass(frozen=True)
class WorkflowRoute:
    """WorkflowRouter 的路由结果。"""

    workflow_name: str
    reason: str


@dataclass(frozen=True)
class MetricStepResult:
    """固定 SQL 节点的轻量结构化结果，兼容底层 CSV 文本返回。"""

    text: str
    columns: list[str]
    rows: list[list[str]]
    empty: bool


class WorkflowRouter:
    """
    根据任务分类选择固定业务 workflow。

    路由只看 classification，不重新理解用户意图。这样分类层和执行层职责清晰，后续如果分类器从
    规则升级到模型，WorkflowRouter 不需要跟着改。
    """

    _TASK_TO_WORKFLOW = {
        "daily_report": "daily_report",
        "inventory_analysis": "inventory_warning",
        "campaign_review": "campaign_review",
        "hot_product_analysis": "hot_product_analysis",
    }

    def route(self, classification: TaskClassification) -> Optional[WorkflowRoute]:
        """返回可执行 workflow；没有覆盖的 deterministic_dag 会交给 DeepAgent 兜底。"""
        if classification.preferred_workflow != "deterministic_dag":
            return None
        workflow_name = self._TASK_TO_WORKFLOW.get(classification.task_type)
        if not workflow_name:
            return None
        return WorkflowRoute(workflow_name=workflow_name, reason=f"task_type:{classification.task_type}")


class WorkflowRunner:
    """
    固定业务 DAG 执行器。

    当前版本把日报、库存和活动复盘的指标查询固定为确定性函数；LLM 只负责表达层综合。Critic 仍由
    AgentRuntime 后续阶段统一运行，避免 workflow 内部和外部重复校验。
    """

    def __init__(self):
        self.router = WorkflowRouter()

    async def run_or_fallback(
        self,
        *,
        query: str,
        classification: TaskClassification,
        trace_id: str,
        task_id: str,
        conversation_id: str,
        fallback: DeepAgentExecutor,
    ) -> ExecutionResult:
        """
        先尝试 deterministic workflow，未覆盖或核心节点失败时回落 DeepAgent。

        这里的 fallback 是一个函数而不是直接 import DeepAgent runner，是为了保持 WorkflowRunner
        与具体 Agent 框架解耦；未来切到其他 executor 也只需要换这个回调。
        """
        route = self.router.route(classification)
        if not route:
            self._trace_route(trace_id, task_id, conversation_id, classification, selected=False, reason="no_matching_workflow")
            return ExecutionResult.from_deepagent(await fallback(query), fallback_reason="no_matching_workflow")

        self._trace_route(trace_id, task_id, conversation_id, classification, selected=True, reason=route.reason, workflow_name=route.workflow_name)
        try:
            result = await self._run_workflow(route.workflow_name, query, trace_id, task_id, conversation_id)
            tracer.emit(
                "workflow_finished",
                trace_id=trace_id,
                task_id=task_id,
                conversation_id=conversation_id,
                agent_name="workflow_runner",
                metadata={"workflow_name": route.workflow_name, "result_preview": result.content[:500]},
            )
            return result
        except Exception as error:
            tracer.emit(
                "workflow_failed",
                trace_id=trace_id,
                task_id=task_id,
                conversation_id=conversation_id,
                agent_name="workflow_runner",
                error=str(error)[:1000],
                metadata={"workflow_name": route.workflow_name, "fallback": "deepagent"},
            )
            return ExecutionResult.from_deepagent(
                await fallback(query),
                fallback_reason="workflow_failed",
                attempted_workflow=route.workflow_name,
                workflow_failed=True,
            )

    async def _run_workflow(self, workflow_name: str, query: str, trace_id: str, task_id: str, conversation_id: str) -> ExecutionResult:
        if workflow_name == "daily_report":
            return await self._run_daily_report(query, trace_id, task_id, conversation_id)
        if workflow_name == "inventory_warning":
            return await self._run_inventory_warning(query, trace_id, task_id, conversation_id)
        if workflow_name == "campaign_review":
            return await self._run_campaign_review(query, trace_id, task_id, conversation_id)
        if workflow_name == "hot_product_analysis":
            return await self._run_hot_product_analysis(query, trace_id, task_id, conversation_id)
        raise ValueError(f"未知 workflow: {workflow_name}")

    async def _run_daily_report(self, query: str, trace_id: str, task_id: str, conversation_id: str) -> ExecutionResult:
        from agent.workflows.daily_report import describe_daily_report_workflow

        from agent.workflows.business_metrics import parse_business_time_range, query_daily_metrics, query_daily_risks

        time_range = parse_business_time_range(query)
        metrics = await self._run_metric_step(trace_id, task_id, conversation_id, "日报指标查询", lambda: query_daily_metrics(time_range), required=True, time_range_label=time_range.label)
        risks = await self._run_metric_step(trace_id, task_id, conversation_id, "日报风险查询", lambda: query_daily_risks(time_range), required=False, time_range_label=time_range.label)
        sections = {"指标查询": metrics, "风险查询": risks}
        workflow = describe_daily_report_workflow()
        output_requirements = "输出经营日报，必须包含核心指标、风险点、原因判断和下一步运营动作；如节点结果包含 section_error，必须明确说明该部分缺失，不能编造。"
        content = await self._synthesize(workflow=workflow, query=query, sections=sections, output_requirements=output_requirements)
        return ExecutionResult(content=content, source="workflow", workflow_name="daily_report", sections=sections, time_range=time_range.to_metadata(), workflow_definition=workflow, output_requirements=output_requirements)

    async def _run_inventory_warning(self, query: str, trace_id: str, task_id: str, conversation_id: str) -> ExecutionResult:
        from agent.workflows.inventory_warning import describe_inventory_warning_workflow

        from agent.workflows.business_metrics import parse_business_time_range, query_inventory_risks, query_inventory_velocity

        time_range = parse_business_time_range(query)
        risk_skus = await self._run_metric_step(trace_id, task_id, conversation_id, "风险商品查询", lambda: query_inventory_risks(time_range), required=True, time_range_label=time_range.label)
        velocity = await self._run_metric_step(trace_id, task_id, conversation_id, "库存周转查询", lambda: query_inventory_velocity(time_range), required=False, time_range_label=time_range.label)
        sections = {"风险商品": risk_skus, "库存周转": velocity}
        workflow = describe_inventory_warning_workflow()
        output_requirements = "输出库存预警报告，必须包含风险 SKU、库存/安全库存、优先级和明确动作建议；如节点结果包含 section_error，必须明确说明该部分缺失，不能编造。"
        content = await self._synthesize(workflow=workflow, query=query, sections=sections, output_requirements=output_requirements)
        return ExecutionResult(content=content, source="workflow", workflow_name="inventory_warning", sections=sections, time_range=time_range.to_metadata(), workflow_definition=workflow, output_requirements=output_requirements)

    async def _run_campaign_review(self, query: str, trace_id: str, task_id: str, conversation_id: str) -> ExecutionResult:
        from agent.workflows.campaign_review import describe_campaign_review_workflow

        from agent.workflows.business_metrics import parse_business_time_range, query_campaign_risks, query_campaign_roi, query_campaign_traffic

        time_range = parse_business_time_range(query)
        traffic = await self._run_metric_step(trace_id, task_id, conversation_id, "活动流量查询", lambda: query_campaign_traffic(time_range), required=True, time_range_label=time_range.label)
        conversion = await self._run_metric_step(trace_id, task_id, conversation_id, "活动转化查询", lambda: query_campaign_roi(time_range), required=True, time_range_label=time_range.label)
        risks = await self._run_metric_step(trace_id, task_id, conversation_id, "活动风险查询", lambda: query_campaign_risks(time_range), required=False, time_range_label=time_range.label)
        sections = {"流量表现": traffic, "转化成交": conversion, "活动风险": risks}
        workflow = describe_campaign_review_workflow()
        output_requirements = "输出活动复盘，必须覆盖曝光、点击、转化、GMV、退款或 ROI，并给出下一轮优化动作；如节点结果包含 section_error，必须明确说明该部分缺失，不能编造。"
        content = await self._synthesize(workflow=workflow, query=query, sections=sections, output_requirements=output_requirements)
        return ExecutionResult(content=content, source="workflow", workflow_name="campaign_review", sections=sections, time_range=time_range.to_metadata(), workflow_definition=workflow, output_requirements=output_requirements)

    async def _run_hot_product_analysis(self, query: str, trace_id: str, task_id: str, conversation_id: str) -> ExecutionResult:
        from agent.workflows.hot_product_analysis import describe_hot_product_workflow

        from agent.workflows.business_metrics import parse_business_time_range, query_hot_products

        time_range = parse_business_time_range(query)
        hot_products = await self._run_metric_step(trace_id, task_id, conversation_id, "爆品综合指标查询", lambda: query_hot_products(time_range, limit=5), required=True, time_range_label=time_range.label)
        sections = {"爆品候选指标": hot_products}
        workflow = describe_hot_product_workflow()
        output_requirements = "输出爆品分析结论，必须覆盖销售增长、流量、转化、价格、库存、活动 ROI、退款风险和下一步放量建议；只能基于节点结果给出 TOP 候选，不要再次调用数据库助手。"
        content = await self._synthesize(workflow=workflow, query=query, sections=sections, output_requirements=output_requirements)
        return ExecutionResult(content=content, source="workflow", workflow_name="hot_product_analysis", sections=sections, time_range=time_range.to_metadata(), workflow_definition=workflow, output_requirements=output_requirements)

    async def resynthesize_from_result(self, query: str, execution_result: ExecutionResult, fix_instruction: str) -> str:
        """基于 workflow 原始节点结果重新综合，用于 Critic 修正，避免回到自由 DeepAgent。"""
        if execution_result.source != "workflow" or not execution_result.sections:
            raise ValueError("只有 workflow 执行结果才能基于 sections 重新综合")
        return await self._synthesize(
            workflow=execution_result.workflow_definition,
            query=query,
            sections=execution_result.sections,
            output_requirements=execution_result.output_requirements,
            fix_instruction=fix_instruction,
        )

    async def _run_metric_step(
        self,
        trace_id: str,
        task_id: str,
        conversation_id: str,
        step_name: str,
        query_func: Callable[[], str],
        *,
        required: bool,
        time_range_label: str,
    ) -> str:
        """
        执行一个确定性业务指标节点。

        query_func 内部是固定 SQL，不经过 LLM 生成；这里用 to_thread 是为了避免同步 MySQL 查询阻塞
        FastAPI 事件循环。核心节点失败时抛异常交给 run_or_fallback；补充节点失败时返回 section_error，
        让 deterministic workflow 带着缺失说明继续综合。
        """
        tracer.emit(
            "workflow_step_started",
            trace_id=trace_id,
            task_id=task_id,
            conversation_id=conversation_id,
            agent_name="workflow_runner",
            metadata={"step_name": step_name, "required": required, "time_range": time_range_label},
        )
        try:
            result = await asyncio.to_thread(query_func)
            if _is_query_error(result):
                raise RuntimeError(f"{step_name}失败：{result[:500]}")
            metric_result = _parse_metric_step_result(result)
            if metric_result.empty:
                raise RuntimeError(f"{step_name}返回空结果：empty_result")
        except Exception as error:
            tracer.emit(
                "workflow_step_failed",
                trace_id=trace_id,
                task_id=task_id,
                conversation_id=conversation_id,
                agent_name="workflow_runner",
                error=str(error)[:1000],
                metadata={"step_name": step_name, "required": required, "time_range": time_range_label},
            )
            if required:
                raise
            return f"section_error: {step_name}暂时缺失，原因：{str(error)[:500]}"
        tracer.emit(
            "workflow_step_finished",
            trace_id=trace_id,
            task_id=task_id,
            conversation_id=conversation_id,
            agent_name="workflow_runner",
            metadata={
                "step_name": step_name,
                "required": required,
                "time_range": time_range_label,
                "columns": metric_result.columns,
                "row_count": len(metric_result.rows),
                "empty": metric_result.empty,
                "result_preview": metric_result.text[:500],
            },
        )
        return metric_result.text

    async def _synthesize(self, *, workflow: Dict[str, object], query: str, sections: Dict[str, str], output_requirements: str, fix_instruction: str = "") -> str:
        """
        汇总固定 DAG 的节点结果。

        这里允许使用 LLM 做表达层综合，但输入来自固定节点，任务边界和必备指标由 workflow 模板锁定，
        比让 LLM 自由规划整条链路更可控。
        """
        from agent.llm import get_reasoning_model

        section_text = "\n\n".join([f"## {name}\n{content[:6000]}" for name, content in sections.items()])
        response = await get_reasoning_model().ainvoke(f"""
你是电商经营分析助手。请基于固定业务 workflow 的节点结果生成最终答案，禁止编造节点结果中不存在的数据。

用户任务：
{query}

Workflow 定义：
{workflow}

节点结果：
{section_text}

输出要求：
{output_requirements}

Critic 修正要求：
{fix_instruction or "无"}
""")
        return response.content if hasattr(response, "content") else str(response)

    def _trace_route(
        self,
        trace_id: str,
        task_id: str,
        conversation_id: str,
        classification: TaskClassification,
        *,
        selected: bool,
        reason: str,
        workflow_name: str = "",
    ) -> None:
        tracer.emit(
            "workflow_route_decided",
            trace_id=trace_id,
            task_id=task_id,
            conversation_id=conversation_id,
            agent_name="workflow_router",
            metadata={
                "selected": selected,
                "reason": reason,
                "workflow_name": workflow_name,
                "task_classification": classification.to_dict(),
            },
        )


def _is_query_error(result: str) -> bool:
    """识别固定指标查询的失败文本，便于 workflow 自动 fallback。"""
    return any(marker in result for marker in ("查询出现异常", "查询被拒绝", "缺失数据库核心配置"))


def _is_empty_result(result: str) -> bool:
    """识别 CSV 风格 SQL 结果只有表头、没有数据行的情况。"""
    return _parse_metric_step_result(result).empty


def _parse_metric_step_result(result: str) -> MetricStepResult:
    """把底层 SQL 文本结果解析成 workflow 可用的结构化形态。"""
    if not result or result.startswith("section_error:"):
        return MetricStepResult(text=result, columns=[], rows=[], empty=False)
    lines = [line.strip() for line in result.splitlines() if line.strip()]
    columns = lines[0].split(",") if lines and "," in lines[0] else []
    rows = [line.split(",") for line in lines[1:]] if columns else []
    explicit_empty = any(marker in result for marker in ("查询没有结果", "为空没有数据", "没有可用的表"))
    header_only_empty = bool(columns) and not rows
    return MetricStepResult(text=result, columns=columns, rows=rows, empty=explicit_empty or header_only_empty)