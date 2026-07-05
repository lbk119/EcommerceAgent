import csv
import io
import json
import os
import re
from typing import Literal, TypedDict

from langchain_core.tools import tool
from langgraph.graph import END, START, StateGraph

from agent.loop_guard import AgentLoopGuard, evaluate_loop_with_supervisor_sync
from agent.core.db import execute_write_sql_raw
from api.context import get_thread_context
from api.monitor import monitor
from api.task_runtime import task_runtime
from tools.db_tools import (
    analyze_top_products,
    execute_sql_query,
    get_table_schema,
    reset_db_guard_state,
)


MAX_GRAPH_ROUNDS = int(os.getenv("DB_GRAPH_MAX_ROUNDS", "8"))
MAX_SQL_FAILURES = int(os.getenv("DB_GRAPH_MAX_SQL_FAILURES", "2"))
MAX_SCHEMA_TABLES = int(os.getenv("DB_GRAPH_MAX_SCHEMA_TABLES", "5"))

HOT_PRODUCT_KEYWORDS = ("爆品", "热销", "表现最好", "增长商品", "放量", "转化", "活动因素")
WRITE_KEYWORDS = ("新增", "插入", "修改", "更新", "删除", "建表", "改表", "drop", "truncate", "alter", "insert", "update", "delete")

TABLE_HINTS = {
    "orders": ("订单", "销售", "成交", "gmv", "客单", "日期"),
    "order_items": ("商品", "明细", "销量", "价格", "seller", "sku"),
    "products": ("商品", "类目", "品类", "category"),
    "payments": ("支付", "付款", "gmv", "金额"),
    "inventory": ("库存", "缺货", "安全库存"),
    "traffic_stats": ("流量", "访客", "浏览", "转化", "加购", "收藏"),
    "campaigns": ("活动", "投放", "渠道", "预算"),
    "campaign_product_stats": ("活动", "投放", "roi", "点击", "曝光"),
    "refunds": ("退款", "退货", "售后"),
    "reviews": ("评价", "评分", "review"),
    "customers": ("客户", "用户", "地区", "城市"),
}


class DatabaseGraphState(TypedDict, total=False):
    question: str
    route: str
    analysis_type: str
    rounds: int
    schema_text: str
    sql: str
    write_sql: str
    sandbox_result: dict
    approval_decision: str
    approval_instruction: str
    merge_result: dict
    sql_history: list[str]
    sql_fail_count: int
    query_result: str
    errors: list[str]
    final_answer: str
    loop_events: list[str]
    loop_fingerprints: list[str]
    loop_last_supervised_count: int


def _emit_node(node_name, state):
    # 节点事件会推给前端，方便确认数据库任务是否按状态机路径推进，而不是自由循环。
    monitor._emit("db_graph_node", f"数据库状态机进入节点: {node_name}", {
        "node": node_name,
        "rounds": state.get("rounds", 0),
        "sql_fail_count": state.get("sql_fail_count", 0),
    })


def _bump_round(state, node_name):
    # 这是状态机的硬轮次上限；它比 prompt 约束可靠，确保图不会无限执行。
    rounds = state.get("rounds", 0) + 1
    state["rounds"] = rounds
    _emit_node(node_name, state)
    if rounds > MAX_GRAPH_ROUNDS:
        return {
            **state,
            "route": "end",
            "final_answer": f"数据库状态机超过最大轮次 {MAX_GRAPH_ROUNDS}，已强制结束，避免循环。",
        }
    return state


def _record_node_guard(state, node_name, args=None):
    """Apply the shared loop guard inside the database graph.

    The guard here is the primary workflow-level loop detector. The lower-level
    guard in db_tools.py remains only as a defensive fuse if atomic tools are
    accidentally exposed or a future node loops outside this graph state.
    """
    guard = AgentLoopGuard(
        env_prefix="DB_GRAPH_LOOP",
        events=state.get("loop_events", []),
        fingerprints=state.get("loop_fingerprints", []),
        last_supervised_count=state.get("loop_last_supervised_count", 0),
    )
    summary = guard.record_event(f"db_node:{node_name}", args or {}, f"数据库状态机节点 {node_name}: {args or {}}")
    state.update(guard.snapshot())
    if summary:
        monitor._emit("db_loop_guard", summary)
        return {
            **state,
            "route": "end",
            "final_answer": f"数据库状态机检测到重复或过多节点调用，已停止。\n{summary}",
        }

    if _should_run_workflow_supervisor(state, node_name, guard):
        supervisor_summary = guard.summary("数据库状态机监督器按节点步数触发检查")
        decision, reason = evaluate_loop_with_supervisor_sync(supervisor_summary)
        state.update(guard.snapshot())
        monitor._emit("db_loop_supervisor", f"数据库状态机监督器判断: {decision} - {reason}", {
            "decision": decision,
            "reason": reason,
            "summary": supervisor_summary,
        })
        if decision in {"reflect", "abort"}:
            return {
                **state,
                "route": "end",
                "final_answer": f"数据库状态机监督器建议停止：{reason}\n{supervisor_summary}",
            }

    return state


def _should_run_workflow_supervisor(state, node_name, guard):
    # 已经拿到查询结果并进入总结阶段时，继续监督容易把正常收尾误判成循环。
    if node_name == "summarize" and state.get("query_result"):
        return False
    # 写操作审核链路的 approval/merge 是确定性安全节点，由人工决策和发布开关控制，不交给监督 LLM 误判。
    if node_name in {"approval", "merge"}:
        return False
    return guard.should_supervise(MAX_GRAPH_ROUNDS)


def _route_node(state: DatabaseGraphState) -> DatabaseGraphState:
    state = _bump_round(dict(state), "route")
    if state.get("final_answer"):
        return state
    state = _record_node_guard(state, "route", {"question": state.get("question", "")})
    if state.get("final_answer"):
        return state

    question = state["question"].lower()
    compact_question = re.sub(r"\s+", "", state["question"])
    if any(keyword in question for keyword in WRITE_KEYWORDS):
        return {
            **state,
            "route": "sandbox",
            "analysis_type": "write_change",
        }
    if _is_hot_product_question(compact_question):
        return {**state, "route": "specialized", "analysis_type": "hot_products"}
    return {**state, "route": "schema"}


def _sandbox_node(state: DatabaseGraphState) -> DatabaseGraphState:
    state = _bump_round(dict(state), "sandbox")
    if state.get("final_answer"):
        return state
    state = _record_node_guard(state, "sandbox", {"question": state.get("question", "")})
    if state.get("final_answer"):
        return state

    write_sql = _generate_write_sql(state["question"])
    validation = _validate_write_sql(write_sql)
    if not validation["ok"]:
        return {
            **state,
            "write_sql": write_sql,
            "sandbox_result": validation,
            "route": "end",
            "final_answer": f"数据库变更请求未通过安全校验，已终止。原因：{validation['error']}\n\n候选 SQL：\n{write_sql}",
        }

    sandbox_result = _run_sandbox(write_sql)
    return {**state, "write_sql": write_sql, "sandbox_result": sandbox_result, "route": "approval"}


async def _approval_node(state: DatabaseGraphState) -> DatabaseGraphState:
    state = _bump_round(dict(state), "approval")
    if state.get("final_answer"):
        return state
    state = _record_node_guard(state, "approval", {"write_sql": state.get("write_sql", "")})
    if state.get("final_answer"):
        return state

    decision_payload = await _request_write_approval(state)
    decision = decision_payload.get("decision", "abort")
    instruction = decision_payload.get("instruction", "")
    if decision == "continue":
        return {**state, "approval_decision": decision, "approval_instruction": instruction, "route": "merge"}
    if decision == "revise":
        return {
            **state,
            "approval_decision": decision,
            "approval_instruction": instruction,
            "route": "end",
            "final_answer": "数据库变更已暂停。人工要求按新策略修订后再提交审核。\n\n人工意见：" + (instruction or "未填写"),
        }
    return {
        **state,
        "approval_decision": decision,
        "approval_instruction": instruction,
        "route": "end",
        "final_answer": "数据库变更已被人工审核终止，未执行 Merge 发布。",
    }


def _merge_node(state: DatabaseGraphState) -> DatabaseGraphState:
    state = _bump_round(dict(state), "merge")
    if state.get("final_answer"):
        return state
    state = _record_node_guard(state, "merge", {"production_merge_enabled": _is_production_merge_enabled()})
    if state.get("final_answer"):
        return state

    write_sql = state.get("write_sql", "")
    if not _is_production_merge_enabled():
        merge_result = {
            "ok": False,
            "published": False,
            "reason": "DB_ENABLE_PRODUCTION_MERGE 未开启，已完成审核但未发布到生产库。",
        }
        return {
            **state,
            "merge_result": merge_result,
            "route": "end",
            "final_answer": _format_merge_summary(state, merge_result),
        }

    merge_result = execute_write_sql_raw(write_sql)
    return {
        **state,
        "merge_result": merge_result,
        "route": "end",
        "final_answer": _format_merge_summary(state, merge_result),
    }


def _specialized_tool_node(state: DatabaseGraphState) -> DatabaseGraphState:
    state = _bump_round(dict(state), "specialized_tool")
    if state.get("final_answer"):
        return state
    state = _record_node_guard(state, "specialized_tool", {"analysis_type": state.get("analysis_type", "")})
    if state.get("final_answer"):
        return state
    try:
        result = analyze_top_products.invoke({"days": 365, "limit": 5})
        return {**state, "query_result": result, "route": "summarize"}
    except Exception as error:
        errors = state.get("errors", []) + [str(error)]
        return {**state, "errors": errors, "route": "schema"}


def _schema_node(state: DatabaseGraphState) -> DatabaseGraphState:
    state = _bump_round(dict(state), "schema")
    if state.get("final_answer"):
        return state

    tables = _select_candidate_tables(state["question"])
    state = _record_node_guard(state, "schema", {"tables": tables[:MAX_SCHEMA_TABLES]})
    if state.get("final_answer"):
        return state
    schema_blocks = []
    errors = state.get("errors", [])
    for table_name in tables[:MAX_SCHEMA_TABLES]:
        try:
            schema_blocks.append(f"[{table_name}]\n{get_table_schema.invoke({'table_name': table_name})}")
        except Exception as error:
            errors.append(f"{table_name}: {error}")
    if not schema_blocks:
        return {**state, "errors": errors, "route": "end", "final_answer": "没有拿到可用表结构，数据库状态机已结束。"}
    return {**state, "schema_text": "\n\n".join(schema_blocks), "errors": errors, "route": "sql"}


def _sql_node(state: DatabaseGraphState) -> DatabaseGraphState:
    state = _bump_round(dict(state), "sql")
    if state.get("final_answer"):
        return state

    sql = _generate_sql(state)
    state = _record_node_guard(state, "sql", {"sql": sql})
    if state.get("final_answer"):
        return state
    sql_history = state.get("sql_history", [])
    errors = state.get("errors", [])

    try:
        result = execute_sql_query.invoke({"query": sql})
    except Exception as error:
        result = f"查询出现异常：{error}"

    sql_history = sql_history + [sql]
    failed = _is_failed_result(result)
    if failed:
        fail_count = state.get("sql_fail_count", 0) + 1
        errors = errors + [result[:600]]
        if fail_count >= MAX_SQL_FAILURES:
            return {
                **state,
                "sql": sql,
                "query_result": result,
                "sql_history": sql_history,
                "sql_fail_count": fail_count,
                "errors": errors,
                "route": "end",
                "final_answer": f"SQL 连续失败 {fail_count} 次，数据库状态机已终止。最后错误：{result}",
            }
        return {
            **state,
            "sql": sql,
            "query_result": result,
            "sql_history": sql_history,
            "sql_fail_count": fail_count,
            "errors": errors,
            "route": "sql",
        }

    return {
        **state,
        "sql": sql,
        "query_result": result,
        "sql_history": sql_history,
        "route": "summarize",
    }


def _summarize_node(state: DatabaseGraphState) -> DatabaseGraphState:
    state = _bump_round(dict(state), "summarize")
    if state.get("final_answer"):
        return state
    state = _record_node_guard(state, "summarize", {"analysis_type": state.get("analysis_type", "generic")})
    if state.get("final_answer"):
        return state

    question = state["question"]
    result = state.get("query_result", "")
    if state.get("analysis_type") == "hot_products":
        return {**state, "final_answer": _summarize_top_products(result), "route": "end"}

    try:
        from agent.llm import get_reasoning_model

        response = get_reasoning_model().invoke(f"""
你是电商数据库分析助手。请基于数据库查询结果回答用户问题，禁止编造结果中不存在的指标。

用户问题：{question}

SQL/工具结果：
{result[:12000]}

输出要求：
1. 先给结论；
2. 说明关键数据证据；
3. 如果是商品/爆品分析，覆盖销售、流量、转化、价格、库存、活动和退款风险；
4. 给出下一步运营建议；
5. 不要再要求继续查询。
""")
        final_answer = response.content if hasattr(response, "content") else str(response)
    except Exception:
        final_answer = _fallback_summary(question, result)
    return {**state, "final_answer": final_answer, "route": "end"}


def _select_next_after_route(state: DatabaseGraphState) -> Literal["specialized", "schema", "sandbox", "summarize", "end"]:
    if state.get("final_answer"):
        return "end"
    route = state.get("route", "end")
    if route in {"specialized", "schema", "sandbox", "summarize"}:
        return route
    return "end"


def _select_next_after_approval(state: DatabaseGraphState) -> Literal["merge", "end"]:
    if state.get("final_answer"):
        return "end"
    return "merge" if state.get("route") == "merge" else "end"


def _select_next_after_sql(state: DatabaseGraphState) -> Literal["sql", "summarize", "end"]:
    if state.get("final_answer"):
        return "end"
    route = state.get("route", "end")
    if route in {"sql", "summarize"}:
        return route
    return "end"


def _select_candidate_tables(question):
    normalized_question = question.lower()
    selected = []
    for table_name, hints in TABLE_HINTS.items():
        if any(hint.lower() in normalized_question or hint in question for hint in hints):
            selected.append(table_name)
    if selected:
        return selected[:MAX_SCHEMA_TABLES]
    return ["orders", "order_items", "products", "payments", "inventory"][:MAX_SCHEMA_TABLES]


def _is_hot_product_question(compact_question):
    if any(keyword in compact_question for keyword in HOT_PRODUCT_KEYWORDS):
        return True
    product_terms = ("商品", "产品", "sku", "品类")
    metric_terms = ("销售", "销量", "流量", "转化", "库存", "活动", "投放", "退款", "价格")
    rank_terms = ("最好", "最高", "top", "增长", "放量")
    return (
        any(term in compact_question.lower() for term in product_terms)
        and any(term in compact_question.lower() for term in metric_terms)
        and any(term in compact_question.lower() for term in rank_terms)
    )


def _summarize_top_products(result):
    rows = list(csv.DictReader(io.StringIO(result)))
    if not rows:
        return "爆品分析工具没有返回可用商品数据，数据库状态机已结束。"

    top = rows[0]
    top3_rows = rows[:3]
    lines = [
        f"## 爆品结论",
        "",
        f"当前最值得优先关注和放量的爆品是 **{top.get('product_id', '')}**，类目为 **{top.get('category_name_en', '')}**。",
        "该商品销售额最高、库存健康、活动 ROI 较好，适合作为下一阶段优先放量商品。",
        "",
        "## 核心指标",
        "",
        "| 指标 | 数值 |",
        "| --- | ---: |",
        f"| 销售额 | {top.get('sales_amount', '0')} |",
        f"| 销量 | {top.get('units_sold', '0')} |",
        f"| 订单数 | {top.get('orders_count', '0')} |",
        f"| 成交均价 | {top.get('avg_price', '0')} |",
        f"| 访客数 | {top.get('visitors', '0')} |",
        f"| 浏览量 | {top.get('views', '0')} |",
        f"| 转化率 | {_format_rate(top.get('conversion_rate'))} |",
        f"| 当前库存 | {top.get('stock', '0')} |",
        f"| 安全库存 | {top.get('safety_stock', '0')} |",
        f"| 活动收入 | {top.get('campaign_revenue', '0')} |",
        f"| 活动花费 | {top.get('campaign_spend', '0')} |",
        f"| 活动 ROI | {top.get('campaign_roi', '0')} |",
        f"| 退款单数 | {top.get('refunds_count', '0')} |",
        f"| 退款金额 | {top.get('refund_amount', '0')} |",
        "",
        "## TOP3 候选商品",
        "",
        "| 排名 | 商品ID | 类目 | 销售额 | 销量 | 订单数 | 均价 | 访客 | 转化率 | 库存 | 安全库存 | 活动ROI | 判断 |",
        "| ---: | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for index, row in enumerate(top3_rows, start=1):
        stock = _to_float(row.get("stock"))
        safety_stock = _to_float(row.get("safety_stock"))
        stock_note = "库存健康"
        if stock <= 0:
            stock_note = "无库存，不能直接放量"
        elif stock <= safety_stock:
            stock_note = "接近或低于安全库存，放量前需补货"

        lines.append(
            f"| {index} | {row.get('product_id', '')} | {row.get('category_name_en', '')} | "
            f"{row.get('sales_amount', '0')} | {row.get('units_sold', '0')} | {row.get('orders_count', '0')} | "
            f"{row.get('avg_price', '0')} | {row.get('visitors', '0')} | {_format_rate(row.get('conversion_rate'))} | "
            f"{row.get('stock', '0')} | {row.get('safety_stock', '0')} | {row.get('campaign_roi', '0')} | {stock_note} |"
        )

    lines.extend([
        "",
        "## 放量建议",
        "",
        "1. **主推第一名商品**：预算按 20%-30% 小步递增，观察活动 ROI 是否稳定。",
        "2. **先补货再投放缺货商品**：库存为 0 或接近安全库存的商品，不建议直接加大流量。",
        "3. **保留价格体系**：优先用限时券、满减、赠品或会员权益提升转化，不建议直接大幅降价。",
        "4. **同步监控退款原因**：退款集中在质量、描述、物流或售后时，先修正再放量。",
        "5. **建立第二增长点**：优先选择活动 ROI 高且库存健康的候选商品做加测。",
    ])
    return "\n".join(lines)


def _to_float(value):
    try:
        return float(value or 0)
    except ValueError:
        return 0.0


def _format_rate(value):
    return f"{_to_float(value) * 100:.2f}%"


def _generate_sql(state):
    # 通用查询仍由 LLM 生成 SQL，但状态机只允许有限次数失败和重复方案检测。
    previous_errors = "\n".join(state.get("errors", [])[-3:]) or "无"
    previous_sql = "\n".join(state.get("sql_history", [])[-3:]) or "无"
    from agent.llm import get_reasoning_model

    response = get_reasoning_model().invoke(f"""
你是 MySQL 只读 SQL 生成器。只返回一条 SQL，不要 Markdown，不要解释。

硬性规则：
- 只能生成 SELECT 或 WITH 开头的只读查询。
- 必须带 LIMIT，除非最外层是聚合单行结果。
- 禁止 INSERT、UPDATE、DELETE、DROP、TRUNCATE、ALTER、CREATE。
- 如果上一轮 SQL 失败，必须换一种查询方案，不能复用同一逻辑。

用户问题：{state['question']}

可用表结构：
{state.get('schema_text', '')[:8000]}

历史失败 SQL：
{previous_sql}

历史错误：
{previous_errors}
""")
    content = response.content if hasattr(response, "content") else str(response)
    return _extract_sql(content)


def _generate_write_sql(question):
    # 写操作只生成候选 SQL，不会在生成阶段执行；后续必须经过 sandbox 和人工审核。
    from agent.llm import get_reasoning_model

    response = get_reasoning_model().invoke(f"""
你是 MySQL 数据库变更 SQL 生成器。只返回一条 SQL，不要 Markdown，不要解释。

安全规则：
- 只能生成 INSERT、UPDATE、DELETE、CREATE、ALTER。
- UPDATE 和 DELETE 必须包含明确 WHERE 条件。
- 禁止 DROP、TRUNCATE。
- 不要生成多条语句。
- 如果用户意图不明确，返回一条以 SELECT 开头的说明性查询是不允许的；此时返回 INVALID_REQUEST。

用户变更需求：{question}
""")
    content = response.content if hasattr(response, "content") else str(response)
    return _extract_sql(content)


def _validate_write_sql(sql):
    # Merge 前的硬校验不依赖模型自觉；所有危险写入都在这里被拦截。
    normalized_sql = sql.strip().rstrip(";")
    statement_head = normalized_sql.split(None, 1)[0].lower() if normalized_sql else ""
    if statement_head == "invalid_request":
        return {"ok": False, "error": "模型认为用户写入需求不明确，未生成可审核 SQL。"}
    if statement_head not in {"insert", "update", "delete", "create", "alter"}:
        return {"ok": False, "error": "候选 SQL 不是允许的 INSERT/UPDATE/DELETE/CREATE/ALTER。"}
    if statement_head in {"update", "delete"} and not re.search(r"\bwhere\b", normalized_sql, re.IGNORECASE):
        return {"ok": False, "error": "UPDATE/DELETE 缺少 WHERE 条件。"}
    if re.search(r"\b(drop|truncate)\b", normalized_sql, re.IGNORECASE):
        return {"ok": False, "error": "候选 SQL 包含 DROP/TRUNCATE，高危 DDL 已拒绝。"}
    if ";" in normalized_sql:
        return {"ok": False, "error": "候选 SQL 包含多语句风险，已拒绝。"}
    return {"ok": True, "statement": statement_head, "sql": normalized_sql}


def _run_sandbox(write_sql):
    # 当前项目没有真实数据库 fork 能力；这里先实现安全的“沙箱校验报告”。
    # 如果配置 DB_SANDBOX_DATABASE，则会在该库执行事务；否则只返回待审核计划，不在 sandbox 阶段碰任何库。
    # 测试阶段允许“无沙箱直连生产”：当 DB_ENABLE_PRODUCTION_MERGE=true 时，人工审核 continue 后会在 merge 节点写入 MYSQL_DATABASE。
    sandbox_database = os.getenv("DB_SANDBOX_DATABASE")
    if not sandbox_database:
        if _is_production_merge_enabled():
            return {
                "ok": True,
                "executed": False,
                "database": None,
                "mode": "direct_production_after_approval",
                "message": "未配置 DB_SANDBOX_DATABASE；当前 DB_ENABLE_PRODUCTION_MERGE=true。sandbox 阶段只做安全校验，人工审核 continue 后将在 merge 节点直接写入 MYSQL_DATABASE 指向的生产库。",
            }
        return {
            "ok": True,
            "executed": False,
            "database": None,
            "message": "未配置 DB_SANDBOX_DATABASE，已完成 SQL 安全校验，未在任何数据库执行。",
        }
    result = execute_write_sql_raw(write_sql, target_database=sandbox_database)
    return {**result, "executed": bool(result.get("ok")), "sandbox_database": sandbox_database}


async def _request_write_approval(state):
    # 这里复用现有 task_runtime.interrupt/resume 链路：任务会暂停，前端展示审核摘要，人工点继续/修订/终止后恢复。
    thread_id = get_thread_context()
    approval_summary = _format_approval_summary(state)
    monitor._emit("human_interrupt", "数据库变更需要人工审核", {
        "summary": approval_summary,
        "suggested_decision": "abort",
        "options": ["continue", "revise", "abort"],
        "reason": "database_change_approval",
    })
    if not thread_id:
        return {"decision": "abort", "instruction": "当前不在任务上下文中，无法等待人工审核。"}
    return await task_runtime.interrupt(thread_id, "database_change_approval", approval_summary)


def _format_approval_summary(state):
    sandbox_result = state.get("sandbox_result", {})
    production_merge_enabled = _is_production_merge_enabled()
    production_warning = ""
    if production_merge_enabled and sandbox_result.get("mode") == "direct_production_after_approval":
        production_warning = (
            "\n【重要风险提示】当前未配置 DB_SANDBOX_DATABASE，且 DB_ENABLE_PRODUCTION_MERGE=true。"
            "如果选择 continue，Merge 节点会直接写入 MYSQL_DATABASE 指向的生产库。\n"
        )
    return (
        "【数据库变更人工审核】\n"
        "状态机已生成候选 SQL，并完成 sandbox/安全校验。请确认是否继续 Merge 发布。\n\n"
        f"用户需求：{state.get('question', '')}\n\n"
        f"候选 SQL：\n{state.get('write_sql', '')}\n\n"
        f"Sandbox 结果：\n{json.dumps(sandbox_result, ensure_ascii=False, indent=2)}\n\n"
        f"{production_warning}"
        "选择 continue：进入 Merge 发布节点；选择 revise：按人工意见修订后重新提交；选择 abort：终止变更。\n"
        f"生产发布开关 DB_ENABLE_PRODUCTION_MERGE 当前为：{production_merge_enabled}"
    )


def _is_production_merge_enabled():
    # 运行时读取开关，便于测试阶段调整 .env 后重启服务生效；默认关闭，避免误写生产库。
    return os.getenv("DB_ENABLE_PRODUCTION_MERGE", "false").lower() == "true"


def _format_merge_summary(state, merge_result):
    return (
        "数据库变更审核链路已结束。\n\n"
        f"候选 SQL：\n{state.get('write_sql', '')}\n\n"
        f"Sandbox 结果：\n{json.dumps(state.get('sandbox_result', {}), ensure_ascii=False, indent=2)}\n\n"
        f"人工决策：{state.get('approval_decision', '')}\n"
        f"人工意见：{state.get('approval_instruction', '') or '无'}\n\n"
        f"Merge 结果：\n{json.dumps(merge_result, ensure_ascii=False, indent=2)}"
    )


def _extract_sql(content):
    content = content.strip()
    fenced = re.search(r"```(?:sql)?\s*(.*?)```", content, re.IGNORECASE | re.DOTALL)
    if fenced:
        content = fenced.group(1).strip()
    statements = [part.strip() for part in content.split(";") if part.strip()]
    return statements[0] if statements else content


def _is_failed_result(result):
    return any(marker in result for marker in ("查询出现异常", "查询被拒绝", "SQL 连续失败"))


def _fallback_summary(question, result):
    rows = list(csv.reader(io.StringIO(result)))
    if len(rows) <= 1:
        return f"数据库状态机已完成查询，但没有可汇总的数据。问题：{question}\n\n原始结果：\n{result}"
    header = rows[0]
    preview_rows = rows[1:6]
    lines = ["数据库状态机已完成查询，以下是关键结果："]
    for row in preview_rows:
        item = dict(zip(header, row))
        lines.append("- " + ", ".join(f"{key}: {value}" for key, value in item.items()))
    return "\n".join(lines)


def _build_database_graph():
    graph = StateGraph(DatabaseGraphState)
    graph.add_node("route", _route_node)
    graph.add_node("specialized", _specialized_tool_node)
    graph.add_node("schema", _schema_node)
    graph.add_node("sql", _sql_node)
    graph.add_node("summarize", _summarize_node)
    graph.add_node("sandbox", _sandbox_node)
    graph.add_node("approval", _approval_node)
    graph.add_node("merge", _merge_node)

    graph.add_edge(START, "route")
    graph.add_conditional_edges("route", _select_next_after_route, {
        "specialized": "specialized",
        "schema": "schema",
        "sandbox": "sandbox",
        "summarize": "summarize",
        "end": END,
    })
    graph.add_edge("specialized", "summarize")
    graph.add_edge("sandbox", "approval")
    graph.add_conditional_edges("approval", _select_next_after_approval, {
        "merge": "merge",
        "end": END,
    })
    graph.add_edge("merge", END)
    graph.add_edge("schema", "sql")
    graph.add_conditional_edges("sql", _select_next_after_sql, {
        "sql": "sql",
        "summarize": "summarize",
        "end": END,
    })
    graph.add_edge("summarize", END)
    return graph.compile()


database_graph = _build_database_graph()


@tool
async def run_database_workflow(question: str) -> str:
    """
    使用小型 LangGraph 数据库状态机回答数据库问题。
    状态机节点：route -> specialized_tool/schema -> sql -> summarize/end。
    内置轮次上限、SQL失败计数和历史SQL查重，避免数据库子 Agent 自由循环。
    """
    reset_db_guard_state()
    monitor._emit("db_graph_start", "数据库状态机开始执行", {"question": question})
    result = await database_graph.ainvoke({
        "question": question,
        "rounds": 0,
        "sql_fail_count": 0,
        "sql_history": [],
        "errors": [],
        "loop_events": [],
        "loop_fingerprints": [],
        "loop_last_supervised_count": 0,
    }, {"recursion_limit": MAX_GRAPH_ROUNDS + 4})
    final_answer = result.get("final_answer") or "数据库状态机未生成最终答案，已结束。"
    monitor._emit("db_graph_end", "数据库状态机执行结束", {"answer_preview": final_answer[:500]})
    return final_answer