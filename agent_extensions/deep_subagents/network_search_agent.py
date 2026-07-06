from agent.prompts import sub_agents_content
from agent.core.agent_spec import AgentSpec
from agent.core.tool_registry import NETWORK_SEARCH_AGENT_PERMISSIONS, NETWORK_SEARCH_AGENT_TOOLS

network_search_agent_spec = AgentSpec(
    name=sub_agents_content["tavily"]["name"],
    role="网络检索专家",
    goal=sub_agents_content["tavily"]["description"],
    tools=NETWORK_SEARCH_AGENT_TOOLS,
    permissions=NETWORK_SEARCH_AGENT_PERMISSIONS,
    system_prompt=sub_agents_content["tavily"]["system_prompt"],
    description=sub_agents_content["tavily"]["description"],
)

network_search_agent = network_search_agent_spec.to_deepagents_subagent()
