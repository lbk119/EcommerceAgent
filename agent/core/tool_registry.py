"""
统一工具注册表。

这个模块不重新实现工具执行逻辑，也不替代 LangChain/DeepAgents 的 tool 对象。
它只在现有工具外面补一层“平台元数据”，解决三个问题：

1. Agent 只通过稳定的工具名引用工具，避免到处直接 import 具体工具函数。
2. 每个工具都有风险等级、权限要求和审批要求，后续权限治理可以直接复用。
3. 前端、Critic、审计日志可以读取同一份工具目录，不需要重复维护一份说明。

注意：ToolSpec.name 应当和 LangChain tool 的名称保持一致，便于追踪和权限拦截。
"""

from dataclasses import dataclass, field
from importlib import import_module
from typing import Any, Callable, Dict, List, Optional

from agent.security.permissions import assert_tool_allowed


@dataclass(frozen=True)
class ToolSpec:
    """
    单个工具的平台级描述。

    字段说明：
    - name: 工具稳定标识。AgentSpec、权限策略、审计事件都用这个名字引用工具。
        - tool_factory: 懒加载实际 LangChain tool 对象的工厂。catalog、权限和 Critic policy 只读元数据，
            不应该因为导入 ToolRegistry 就初始化 LLM、RAGFlow 或数据库 workflow。
    - category: 工具类别，用于前端分组、审计筛选和 Critic 校验策略选择。
    - risk: 风险等级。建议使用 low / medium / high，后续权限引擎可按等级加严。
    - permissions: 调用工具所需的权限点，例如 db:read、file:write_output。
    - requires_human_approval: 是否天然需要人工审核。这里是工具元数据，不代表所有调用都会立刻阻塞。
    - description: 给前端、审计日志和运维人员看的短说明，不直接喂给模型做安全决策。
    """

    name: str
    category: str
    tool_factory: Callable[[], Any]
    risk: str = "low"
    permissions: List[str] = field(default_factory=list)
    requires_human_approval: bool = False
    description: str = ""


class ToolRegistry:
    """
    进程内工具目录。

    当前阶段保持轻量：启动时注册，运行时只读。
    后续如果要做租户级权限、动态启停工具或工具版本治理，可以在这里扩展，
    而不需要改 main_agent / sub_agent 的工具挂载代码。
    """

    def __init__(self):
        # key 是 ToolSpec.name，value 是完整工具元数据。
        self._specs: Dict[str, ToolSpec] = {}
        self._tools: Dict[str, Any] = {}
        self._guarded_tools: Dict[tuple[str, str, tuple[str, ...]], Any] = {}

    def register(self, spec: ToolSpec) -> ToolSpec:
        """注册一个工具，并返回原 spec，方便链式/测试断言。"""
        self._specs[spec.name] = spec
        return spec

    def get(self, name: str):
        """
        按稳定工具名获取实际 tool 对象，供 DeepAgents 挂载。

        这是唯一会触发工具模块 import 的入口。这样 `/api/tools/catalog`、Critic policy、任务分类和
        轻量测试导入 ToolRegistry 时，只会加载元数据，不会初始化 LLM 或外部客户端。
        """
        if name not in self._tools:
            self._tools[name] = self._specs[name].tool_factory()
        return self._tools[name]

    def get_spec(self, name: str) -> ToolSpec:
        """按稳定工具名获取工具元数据，供权限、审计、Critic 使用。"""
        return self._specs[name]

    def tools(self, names: List[str], granted_permissions: Optional[List[str]] = None, actor: str = "unknown_agent") -> List[Any]:
        """
        把 AgentSpec 中的工具名列表转换为 DeepAgents 需要的 tool 对象列表。

        返回的是 guarded tool：它保留原工具 schema，但在真正执行前会检查 ToolSpec.permissions。
        """
        return [self.guarded_tool(name, granted_permissions or [], actor) for name in names]

    def guarded_tool(self, name: str, granted_permissions: List[str], actor: str):
        """返回带权限拦截的工具对象，并按 actor/权限组合做缓存。"""
        cache_key = (name, actor, tuple(sorted(granted_permissions)))
        if cache_key not in self._guarded_tools:
            self._guarded_tools[cache_key] = self._build_guarded_tool(self.get_spec(name), granted_permissions, actor)
        return self._guarded_tools[cache_key]

    def _build_guarded_tool(self, spec: ToolSpec, granted_permissions: List[str], actor: str):
        from langchain_core.tools import StructuredTool

        original_tool = self.get(spec.name)

        def check_permission():
            assert_tool_allowed(spec.name, spec.permissions, granted_permissions, spec.risk, actor)

        sync_func = getattr(original_tool, "func", None)
        async_func = getattr(original_tool, "coroutine", None)

        def guarded_func(*args, **kwargs):
            check_permission()
            return sync_func(*args, **kwargs)

        async def guarded_coroutine(*args, **kwargs):
            check_permission()
            return await async_func(*args, **kwargs)

        return StructuredTool.from_function(
            func=guarded_func if sync_func else None,
            coroutine=guarded_coroutine if async_func else None,
            name=getattr(original_tool, "name", spec.name),
            description=getattr(original_tool, "description", spec.description),
            args_schema=getattr(original_tool, "args_schema", None),
            return_direct=getattr(original_tool, "return_direct", False),
        )

    def catalog(self) -> List[Dict[str, Any]]:
        """
        返回可序列化的工具目录。

        这里刻意不返回 tool 对象本身，因为它不能稳定 JSON 序列化，
        也不应该暴露给前端或外部系统。
        """
        return [
            {
                "name": spec.name,
                "category": spec.category,
                "risk": spec.risk,
                "permissions": spec.permissions,
                "requires_human_approval": spec.requires_human_approval,
                "description": spec.description,
            }
            for spec in self._specs.values()
        ]


# 全局单例：和 LLMRouter 一样，作为进程级平台基础设施使用。
tool_registry = ToolRegistry()


def _lazy_tool(module_path: str, attr_name: str) -> Callable[[], Any]:
    """
    创建工具懒加载工厂。

    module_path/attr_name 使用字符串而不是直接 import，目的就是打断 import 阶段的重依赖链：
    tool_registry -> database_workflow_tool -> agent.llm -> 模型初始化。
    """
    def load_tool() -> Any:
        module = import_module(module_path)
        return getattr(module, attr_name)

    return load_tool

# 主 Agent 的产物工具：只处理文件/文档，不直接碰业务库或外部网络。
tool_registry.register(ToolSpec(
    name="generate_markdown",
    category="document",
    tool_factory=_lazy_tool("tools.markdown_tools", "generate_markdown"),
    risk="low",
    permissions=["file:write_output"],
    description="生成 Markdown 结果文件。",
))
tool_registry.register(ToolSpec(
    name="convert_md_to_pdf",
    category="document",
    tool_factory=_lazy_tool("tools.pdf_tools", "convert_md_to_pdf"),
    risk="low",
    permissions=["file:read_output", "file:write_output"],
    description="将 Markdown 结果转换为 PDF。",
))
tool_registry.register(ToolSpec(
    name="read_file_content",
    category="file",
    tool_factory=_lazy_tool("tools.upload_file_read_tool", "read_file_content"),
    risk="medium",
    permissions=["file:read_uploaded"],
    description="读取用户上传文件内容。",
))

# 数据库工具风险最高：读查询允许自动执行，写入必须走 sandbox / approval / merge 工作流。
tool_registry.register(ToolSpec(
    name="run_database_workflow",
    category="database",
    tool_factory=_lazy_tool("tools.database_workflow_tool", "run_database_workflow"),
    risk="high",
    permissions=["db:read", "db:write_candidate"],
    requires_human_approval=True,
    description="执行数据库经营分析工作流，写操作必须走候选/审核链路。",
))

# 知识库工具属于外部系统访问，风险主要来自信息泄露、无效知识库或错误引用。
tool_registry.register(ToolSpec(
    name="get_assistant_list",
    category="knowledge_base",
    tool_factory=_lazy_tool("tools.ragflow_tools", "get_assistant_list"),
    risk="low",
    permissions=["kb:read"],
    description="查询 RAGFlow 知识库助手列表。",
))
tool_registry.register(ToolSpec(
    name="create_ask_delete",
    category="knowledge_base",
    tool_factory=_lazy_tool("tools.ragflow_tools", "create_ask_delete"),
    risk="medium",
    permissions=["kb:ask"],
    description="向 RAGFlow 助手提问并清理临时会话。",
))

# 网络搜索会访问外部互联网，结果需要由 Agent/Critic 判断可信度，不能直接当作业务事实。
tool_registry.register(ToolSpec(
    name="internet_search",
    category="network",
    tool_factory=_lazy_tool("tools.tavily_tool", "internet_search"),
    risk="medium",
    permissions=["network:search"],
    description="执行外部网络搜索。",
))


# AgentSpec 只引用这些名字，而不是直接 import 工具对象。
# 这样后续要做权限裁剪、工具替换、mock 测试或前端工具目录展示时，都有统一入口。
MAIN_AGENT_TOOLS = ["generate_markdown", "convert_md_to_pdf", "read_file_content"]
MAIN_AGENT_PERMISSIONS = ["file:read_output", "file:write_output", "file:read_uploaded"]
DATABASE_AGENT_TOOLS = ["run_database_workflow"]
DATABASE_AGENT_PERMISSIONS = ["db:read", "db:write_candidate"]
KNOWLEDGE_BASE_AGENT_TOOLS = ["get_assistant_list", "create_ask_delete"]
KNOWLEDGE_BASE_AGENT_PERMISSIONS = ["kb:read", "kb:ask"]
NETWORK_SEARCH_AGENT_TOOLS = ["internet_search"]
NETWORK_SEARCH_AGENT_PERMISSIONS = ["network:search"]