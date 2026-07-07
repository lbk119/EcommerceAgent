"""Prompt 配置加载器。

主 Agent 和子 Agent 的系统提示词存放在 prompt/prompts.yml 中；运行时通过本模块加载到内存。
如果存在 prompt/policy_overrides.yml，还会把已经审核通过的进化策略追加到主 Agent system prompt。

设计重点：
- YAML 只使用 safe_load，避免配置文件解析时执行任意对象构造；
- reload_prompts() 支持运行中热重载策略，不需要重启服务；
- 对外暴露 main_agent_content/sub_agents_content，兼容旧的 Agent 构建代码。
"""

import yaml  # yaml 配置文件读取
from pathlib import Path

def load_yaml(file_path):
    """
    加载指定位置的 YAML 配置文件。

    Args:
        file_path: 要读取的 YAML 文件路径。

    Returns:
        dict/list/None: yaml.safe_load 的解析结果，通常是 dict。

    注意：必须使用 safe_load。普通 yaml.load 可能根据 YAML 内容构造 Python 对象，存在配置注入风险。
    """
    with open(file_path, 'r', encoding='utf-8') as f :
        return yaml.safe_load(f)

# 项目根目录。当前文件在 agent/prompts.py，因此 parents[1] 指向仓库根目录。
project_root_path  = Path(__file__).parents[1]
yaml_file_path = project_root_path / "prompt" / "prompts.yml"

# 进化策略审核通过后会写入该文件；不存在时说明没有额外策略需要合并。
policy_overrides_path = project_root_path / "prompt" / "policy_overrides.yml"


def load_prompt_content():
    """加载基础 prompt，并合并已审核的策略追加项。

    policy_overrides.yml 的 main_agent.system_prompt_append 是一个列表，每项包含一条 instruction。
    这里只把非空 instruction 追加到主 Agent system prompt 的末尾，避免覆盖原始 prompt 主体。
    """
    content = load_yaml(yaml_file_path)
    if policy_overrides_path.exists():
        policy_overrides = load_yaml(policy_overrides_path) or {}
        main_agent_overrides = policy_overrides.get("main_agent", {})
        prompt_appends = main_agent_overrides.get("system_prompt_append", [])
        if prompt_appends:
            extra_instructions = "\n".join([
                f"- {item.get('instruction', '')}" for item in prompt_appends if item.get("instruction")
            ])
            if extra_instructions:
                # 使用清晰标题区分“基础系统提示词”和“已审核进化策略”，便于排障 prompt 来源。
                content["main_agent"]["system_prompt"] += f"\n\n【已审核进化策略】\n{extra_instructions}"
    return content


def reload_prompts():
    """重新加载 prompt 全局缓存。

    main_agent.reload_agent_policy() 会调用该函数，随后清空 Agent 图缓存，使新策略在下一次构建时生效。
    """
    global prompt_yaml_content, main_agent_content, sub_agents_content
    prompt_yaml_content = load_prompt_content()
    main_agent_content = prompt_yaml_content["main_agent"]
    sub_agents_content = prompt_yaml_content["sub_agents"]


# 模块导入时立即加载一次，保证旧代码可以直接读取 main_agent_content/sub_agents_content。
reload_prompts()
