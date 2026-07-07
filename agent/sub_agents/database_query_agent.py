"""数据库查询子 Agent 定义。

该文件只把 prompt 配置、工具权限和 AgentSpec 组装成 DeepAgents 可识别的 subagent dict。
实际数据库访问由 tools.database_workflow_tool 和 agent.core.db 负责，权限由 ToolRegistry guarded tool
在执行前拦截。
"""

from agent.prompts import sub_agents_content
from agent.core.agent_spec import AgentSpec
from agent.core.tool_registry import DATABASE_AGENT_PERMISSIONS, DATABASE_AGENT_TOOLS

# 平台级结构化描述：供主 Agent 构建、权限治理、Critic 策略和前端/审计目录复用。
database_query_agent_spec = AgentSpec(
    name=sub_agents_content["db"]["name"],
    role="数据库经营分析专家",
    goal=sub_agents_content["db"]["description"],
    tools=DATABASE_AGENT_TOOLS,
    permissions=DATABASE_AGENT_PERMISSIONS,
    critic_required=True,
    system_prompt=sub_agents_content["db"]["system_prompt"],
    description=sub_agents_content["db"]["description"],
)

# DeepAgents 需要的是 dict 格式，所以在模块底部做一次边界转换。
database_query_agent = database_query_agent_spec.to_deepagents_subagent()
