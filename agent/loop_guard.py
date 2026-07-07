"""Agent 循环检测与轻量监督器。

DeepAgent / LangGraph 执行过程中可能出现“反复调用同一工具、没有新信息”的无进展循环。
本模块提供两层保护：
1. 本地 fingerprint：用工具名 + 压缩参数生成哈希，快速发现近期重复调用；
2. Supervisor LLM：在调用次数达到间隔或接近递归上限时，低频判断是否继续、反思或中止。

这些保护用于减少无意义成本消耗，并给用户返回可解释的阶段性失败原因。
"""

import hashlib
import json
import os


class AgentLoopGuard:
    """主 Agent 调用和 LangGraph 节点共享的循环保护器。"""

    def __init__(self, env_prefix="AGENT_LOOP", events=None, fingerprints=None, last_supervised_count=0):
        """初始化循环保护器，并从环境变量读取阈值。

        Args:
            env_prefix: 环境变量前缀，允许不同运行场景使用不同阈值。
            events: 已发生调用摘要，恢复执行时用于延续判断。
            fingerprints: 已发生调用指纹，恢复执行时用于延续重复检测。
            last_supervised_count: 上一次触发 LLM supervisor 时的调用数量。
        """
        self.env_prefix = env_prefix
        self.events = list(events or [])
        self.fingerprints = list(fingerprints or [])
        self.max_calls_before_reflection = int(os.getenv(f"{env_prefix}_MAX_CALLS_BEFORE_REFLECTION", "10"))
        self.repeat_threshold = int(os.getenv(f"{env_prefix}_REPEAT_THRESHOLD", "3"))
        self.recent_window = int(os.getenv(f"{env_prefix}_RECENT_WINDOW", "5"))
        self.supervisor_enabled = os.getenv(f"{env_prefix}_SUPERVISOR_ENABLED", os.getenv("AGENT_SUPERVISOR_ENABLED", "true")).lower() == "true"
        self.supervisor_interval = int(os.getenv(f"{env_prefix}_SUPERVISOR_INTERVAL", os.getenv("AGENT_SUPERVISOR_INTERVAL", "10")))
        self.supervisor_near_limit_margin = int(os.getenv(f"{env_prefix}_SUPERVISOR_NEAR_LIMIT_MARGIN", os.getenv("AGENT_SUPERVISOR_NEAR_LIMIT_MARGIN", "5")))
        self.last_supervised_count = last_supervised_count

    def record_tool_call(self, tool_call):
        """记录 LangChain/DeepAgents 工具调用，并返回可能的循环摘要。"""
        name = tool_call.get("name", "unknown")
        args = tool_call.get("args", {})
        event = self._format_event(name, args)
        return self.record_event(name, args, event)

    def record_event(self, name, args=None, event=None):
        """记录任意工具/节点事件。

        返回 None 表示暂未发现循环；返回字符串表示已触发本地循环规则，上层应进入反思或中止处理。
        """
        args = args or {}
        event = event or self._format_event(name, args)
        fingerprint = self._fingerprint(name, args)
        self.events.append(event)
        self.fingerprints.append(fingerprint)

        recent_fingerprints = self.fingerprints[-self.recent_window:]
        if recent_fingerprints.count(fingerprint) >= self.repeat_threshold:
            return self.summary(f"同一调用在最近 {self.recent_window} 次内重复达到 {self.repeat_threshold} 次")

        if len(self.events) >= self.max_calls_before_reflection:
            return self.summary(f"已连续发生 {len(self.events)} 次工具/节点调用但还没有最终结果")

        return None

    def should_supervise(self, recursion_limit):
        """判断当前是否应该调用 LLM supervisor 做语义层循环判断。"""
        if not self.supervisor_enabled:
            return False
        if len(self.events) == 0 or self.last_supervised_count == len(self.events):
            return False
        if len(self.events) % self.supervisor_interval == 0:
            self.last_supervised_count = len(self.events)
            return True
        if recursion_limit and len(self.events) >= max(1, recursion_limit - self.supervisor_near_limit_margin):
            self.last_supervised_count = len(self.events)
            return True
        return False

    def summary(self, reason):
        """构造给用户/监督器看的近期调用摘要。"""
        recent_events = self.events[-self.recent_window:]
        return reason + "。最近调用：\n" + "\n".join(f"- {event}" for event in recent_events)

    def snapshot(self):
        """导出可持久化快照，便于中断恢复后继续检测循环。"""
        return {
            "loop_events": self.events,
            "loop_fingerprints": self.fingerprints,
            "loop_last_supervised_count": self.last_supervised_count,
        }

    def _fingerprint(self, name, args):
        """把工具名和压缩后的参数变成稳定哈希，用于检测近似重复调用。"""
        compact_args = self._compact_args(args)
        raw = json.dumps({"name": name, "args": compact_args}, ensure_ascii=False, sort_keys=True, default=str)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _format_event(self, name, args):
        """把工具/子助手调用格式化为简短中文摘要。"""
        if name == "task" and isinstance(args, dict):
            assistant = args.get("subagent_type", "unknown")
            description = str(args.get("description", ""))[:160]
            return f"调用子助手 {assistant}: {description}"
        compact_args = self._compact_args(args)
        return f"调用工具/节点 {name}: {json.dumps(compact_args, ensure_ascii=False, default=str)[:220]}"

    def _compact_args(self, args):
        """压缩参数体，避免循环检测缓存保存过大的上下文或敏感原文。"""
        if not isinstance(args, dict):
            return str(args)[:240]

        compact = {}
        for key, value in args.items():
            if isinstance(value, str):
                compact[key] = value[:240]
            elif isinstance(value, (int, float, bool)) or value is None:
                compact[key] = value
            else:
                compact[key] = str(value)[:240]
        return compact


def build_supervisor_prompt(summary):
    """构造 Supervisor LLM 的严格 JSON 判断提示词。"""
    return f"""
你是多智能体执行监督器，只判断任务执行是否陷入无进展循环。

请阅读最近工具/节点/子助手调用摘要，不要补做业务分析。

最近调用摘要：
{summary}

请只返回 JSON，不要返回 Markdown。格式：
{{"decision":"continue|reflect|abort","reason":"一句中文原因"}}

判断标准：
- continue: 最近调用有明显新信息、新表、新工具或新方向。
- reflect: 最近调用虽然形式不同，但语义上围绕同一件事打转，应换策略或给阶段性结论。
- abort: 已经明显无进展，继续执行只会消耗资源，应停止并向用户说明。
"""


def parse_supervisor_response(response):
    """解析 supervisor 响应，兼容返回 JSON fenced block 的情况。"""
    content = response.content if hasattr(response, "content") else str(response)
    content = content.strip()
    if content.startswith("```"):
        content = content.strip("`")
        if content.lower().startswith("json"):
            content = content[4:].strip()
    data = json.loads(content)
    decision = str(data.get("decision", "continue")).lower()
    if decision not in {"continue", "reflect", "abort"}:
        decision = "continue"
    reason = str(data.get("reason", "监督器未给出原因"))[:240]
    return decision, reason


async def evaluate_loop_with_supervisor(summary):
    """异步调用 critic 模型判断是否陷入语义循环；失败时默认继续执行。"""
    try:
        from agent.llm import get_critic_model

        response = await get_critic_model().ainvoke(build_supervisor_prompt(summary))
        return parse_supervisor_response(response)
    except Exception as error:
        return "continue", f"监督器判断失败，继续执行：{str(error)[:160]}"


def evaluate_loop_with_supervisor_sync(summary):
    """同步版本的 supervisor 判断，用于非 async 执行路径。"""
    try:
        from agent.llm import get_critic_model

        response = get_critic_model().invoke(build_supervisor_prompt(summary))
        return parse_supervisor_response(response)
    except Exception as error:
        return "continue", f"监督器判断失败，继续执行：{str(error)[:160]}"