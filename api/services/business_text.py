"""业务文本与策略候选生成工具。

本模块只做确定性文本整理，不调用大模型。这样数据导入后的报告和策略沉淀可以稳定、便宜、
可重复地生成；真正的 Agent 输出也会先经过这里清洗成业务表需要的 summary 和策略候选。
"""

from __future__ import annotations

import re
from typing import Any


def markdown_summary(content: str, limit: int = 180) -> str:
    """从 Markdown 内容中抽取适合列表展示的短摘要。

    规则尽量保守：优先取第一段正文，去掉 Markdown 标题、列表符号和多余空白；空内容给出明确
    提醒；最终长度不超过 220 字，避免前端卡片被异常长文本撑开。
    """
    if not content or not content.strip():
        return "报告已生成，但内容为空，请重新运行"
    paragraphs = [item.strip() for item in re.split(r"\n\s*\n", content) if item.strip()]
    first = next((item for item in paragraphs if not item.lstrip().startswith("#")), paragraphs[0] if paragraphs else content.strip())
    first = re.sub(r"^#{1,6}\s*", "", first)
    first = re.sub(r"^[>\-\*\d\.、\s]+", "", first)
    first = re.sub(r"[`*_#]", "", first)
    first = re.sub(r"\s+", " ", first).strip()
    max_length = min(max(limit, 1), 220)
    if len(first) <= max_length:
        return first
    return first[: max_length - 1].rstrip() + "…"


def build_import_overview_markdown(metrics: dict[str, Any], products: list[dict[str, Any]], inventory_risks: list[dict[str, Any]], campaigns: list[dict[str, Any]]) -> str:
    """根据导入后的聚合数据生成经营概览报告。

    这是导入完成后的确定性报告，不消耗模型资源。即使数据为空，也返回一份可解释的空状态报告，
    帮用户知道下一步应该补哪些字段。
    """
    orders = int(metrics.get("orders") or 0)
    gmv = float(metrics.get("gmv") or 0)
    visitors = int(metrics.get("visitors") or 0)
    product_count = len(products)
    if not any([orders, gmv, visitors, product_count, inventory_risks, campaigns]):
        return """# 导入后经营概览\n\n本次导入已经完成，但当前店铺还没有形成可分析的经营数据。建议检查上传文件是否包含订单号、商品 ID、成交金额、库存、安全库存、访客和转化等字段。\n\n## 下一步\n\n- 补充订单与商品字段后重新导入。\n- 确认当前店铺已选择正确的数据文件。\n- 如果只导入了活动或流量数据，建议同时补充订单明细以形成 GMV 和转化分析。\n"""

    top_products = products[:5]
    risk_lines = [f"- {item.get('productName') or item.get('name') or item.get('sku')}：库存 {item.get('stock')}，安全库存 {item.get('safetyStock') or item.get('safety_stock')}，建议 {item.get('suggestedAction') or item.get('aiSuggestion')}" for item in inventory_risks[:5]]
    product_lines = [f"- {item.get('name')}：销量 {item.get('sales')}，转化率 {item.get('conversionRate')}%，分层 {item.get('layer')}" for item in top_products]
    campaign_lines = [f"- {item.get('name')}：ROI {item.get('roi')}，GMV {item.get('gmv')}，结论：{item.get('conclusion')}" for item in campaigns[:5]]
    return f"""# 导入后经营概览\n\n本次导入后，系统已完成经营数据聚合。当前 GMV 为 {gmv:.2f}，订单数 {orders}，访客数 {visitors}，转化率 {float(metrics.get('conversionRate') or 0):.2f}%，客单价 {float(metrics.get('averageOrderValue') or 0):.2f}，退款率 {float(metrics.get('refundRate') or 0):.2f}%。\n\n## 商品表现\n\n{chr(10).join(product_lines) if product_lines else '- 暂无可分析商品。'}\n\n## 库存风险\n\n{chr(10).join(risk_lines) if risk_lines else '- 暂未发现高优先级库存风险。'}\n\n## 活动复盘\n\n{chr(10).join(campaign_lines) if campaign_lines else '- 暂无活动数据。'}\n\n## 建议动作\n\n- 优先处理库存风险 SKU，避免导入后首轮活动承接不足。\n- 对高转化商品保留预算和库存，继续观察退款率与投放 ROI。\n- 对有访问但成交不足的商品，优先检查价格、主图和活动利益点。\n"""


def build_strategy_candidates_from_metrics(metrics: dict[str, Any], products: list[dict[str, Any]], inventory_risks: list[dict[str, Any]], campaigns: list[dict[str, Any]]) -> list[dict[str, str]]:
    """根据确定性指标生成待审核策略候选。"""
    candidates: list[dict[str, str]] = []
    if inventory_risks:
        first = inventory_risks[0]
        candidates.append({
            "title": f"优先处理 {first.get('productName') or first.get('sku')} 的库存风险",
            "source": "数据导入巡检",
            "expected_impact": "预计降低缺货风险并提升活动承接效率",
            "risk_level": "high" if first.get("riskLevel") == "high" else "medium",
        })
    potential = next((item for item in products if item.get("layer") == "潜力品"), None)
    if potential:
        candidates.append({
            "title": f"放大 {potential.get('name')} 的高转化流量",
            "source": "数据导入巡检",
            "expected_impact": "预计提升潜力商品成交转化和 GMV 贡献",
            "risk_level": "medium",
        })
    weak_campaign = next((item for item in campaigns if float(item.get("roi") or 0) < 2 and float(item.get("gmv") or 0) > 0), None)
    if weak_campaign:
        candidates.append({
            "title": f"复盘 {weak_campaign.get('name')} 的投放 ROI",
            "source": "数据导入巡检",
            "expected_impact": "预计减少低效投放并提升活动预算使用效率",
            "risk_level": "medium",
        })
    if not candidates and int(metrics.get("orders") or 0) > 0:
        candidates.append({
            "title": "建立导入后每日经营巡检节奏",
            "source": "数据导入巡检",
            "expected_impact": "预计更早发现 GMV、库存和退款异常",
            "risk_level": "low",
        })
    return candidates[:3]


def build_strategy_candidates_from_agent_result(final_result: str, job: dict[str, Any]) -> list[dict[str, str]]:
    """从 Agent 输出中抽取 1-3 条策略候选。

    这里不做复杂 NLP，只按标题、列表项和“建议/策略/动作”关键词抽取；抽不到时根据任务类型生成
    一条兜底策略，保证 Agent 完成后前端能看到可审核沉淀。
    """
    agent_name = str(job.get("agent_name") or job.get("agentName") or "数字员工")
    lines = []
    for raw_line in final_result.splitlines():
        line = re.sub(r"^[#>\-\*\d\.、\s]+", "", raw_line).strip()
        if not line:
            continue
        if any(keyword in line for keyword in ("建议", "策略", "动作", "优化", "补货", "复盘", "提升", "降低")):
            lines.append(line)
    if not lines:
        job_type = str(job.get("job_type") or job.get("jobType") or "daily_report")
        fallback_title = {
            "inventory_risk_scan": "根据库存巡检结果调整风险 SKU 备货",
            "replenishment_plan": "按补货计划更新安全库存和采购节奏",
            "campaign_review": "根据活动复盘优化下一轮投放结构",
            "product_optimization": "根据商品分析优化潜力品承接链路",
            "hot_product_analysis": "围绕爆品候选建立放量观察清单",
            "daily_report": "根据经营日报执行次日重点巡检动作",
        }.get(job_type, "根据数字员工结论沉淀运营动作")
        lines = [fallback_title]
    candidates = []
    for line in lines[:3]:
        risk_level = "high" if any(word in line for word in ("缺货", "失败", "异常", "高风险")) else "medium"
        candidates.append({
            "title": line[:80],
            "source": agent_name,
            "expected_impact": _impact_from_text(line),
            "risk_level": risk_level,
        })
    return candidates


def _impact_from_text(text: str) -> str:
    """根据策略文本生成具体预期影响。"""
    if "库存" in text or "补货" in text or "缺货" in text:
        return "预计降低缺货风险并提升库存周转效率"
    if "活动" in text or "投放" in text or "ROI" in text:
        return "预计提升活动承接效率和投放 ROI"
    if "商品" in text or "转化" in text or "价格" in text:
        return "预计提升商品转化率和成交效率"
    if "退款" in text or "售后" in text:
        return "预计降低退款与售后风险"
    return "预计提升经营巡检效率和策略执行一致性"
