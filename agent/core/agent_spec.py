"""
AgentSpec：DeepAgents 子 Agent 的轻量配置层。

这个模块只把“Agent 是谁、目标是什么、能用哪些工具、需要哪些权限、是否需要 Critic”这些平台信息结构化。
这样做的好处是：
- 主 Agent / 子 Agent 的定义更可读；
- 工具通过名字引用，统一走 ToolRegistry；
- 后续权限治理和 Critic 可以读取同一份 AgentSpec，而不是解析 prompt 文本。
"""

from dataclasses import dataclass, field
from typing import List

from agent.core.tool_registry import tool_registry


@dataclass(frozen=True)
class AgentSpec:
    """
    单个 Agent 的平台级描述。
    name/description/system_prompt 是 DeepAgents 当前真正需要的字段；
    role/goal/permissions/critic_required 是平台治理字段，先沉淀结构，后续可用于权限检查、
    前端展示、质量校验和审计分析。
    """

    name: str
    role: str
    goal: str
    tools: List[str]
    permissions: List[str] = field(default_factory=list)
    critic_required: bool = False
    system_prompt: str = ""
    description: str = ""

    def to_deepagents_subagent(self) -> dict:
        """
        转换为 create_deep_agent 接受的 subagent dict。

        这里是 AgentSpec 和 DeepAgents 的边界：业务层维护结构化 spec，运行时再转换成
        DeepAgents 需要的简单字典，避免把 DeepAgents 的数据结构散落到各个模块。
        """
        return {
            "name": self.name,
            "description": self.description or self.goal,
            "tools": tool_registry.tools(self.tools, self.permissions, self.name),
            "system_prompt": self.system_prompt,
        }