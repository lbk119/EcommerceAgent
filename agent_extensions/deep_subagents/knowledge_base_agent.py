from agent.prompts import sub_agents_content
from agent.core.agent_spec import AgentSpec
from agent.core.tool_registry import KNOWLEDGE_BASE_AGENT_PERMISSIONS, KNOWLEDGE_BASE_AGENT_TOOLS

knowledge_base_agent_spec = AgentSpec(
    name=sub_agents_content["ragflow"]["name"],
    role="知识库检索专家",
    goal=sub_agents_content["ragflow"]["description"],
    tools=KNOWLEDGE_BASE_AGENT_TOOLS,
    permissions=KNOWLEDGE_BASE_AGENT_PERMISSIONS,
    system_prompt=sub_agents_content["ragflow"]["system_prompt"],
    description=sub_agents_content["ragflow"]["description"],
)

knowledge_base_agent = knowledge_base_agent_spec.to_deepagents_subagent()