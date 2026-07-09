"""数据质量 toolbox 入口。

当前数据质量能力复用确定性 business tool 目录，对 deepagents subagent 暴露稳定导入路径。
"""

from agent.tools.toolbox.business_tools import get_business_tool

__all__ = ["get_business_tool"]
