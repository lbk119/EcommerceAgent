from agent.prompts import sub_agents_content
from agent.core.agent_spec import AgentSpec
from agent.core.tool_registry import DATABASE_AGENT_PERMISSIONS, DATABASE_AGENT_TOOLS

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

database_query_agent = database_query_agent_spec.to_deepagents_subagent()
