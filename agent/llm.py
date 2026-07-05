from agent.core.llm_router import llm_router


def get_reasoning_model():
	"""懒加载主推理模型；只在主 Agent 构图或业务综合真正需要时初始化。"""
	return llm_router.get("reasoning_model")


def get_fast_model():
	"""懒加载快速模型；保留给轻量分类、摘要或未来低成本任务使用。"""
	return llm_router.get("fast_model")


def get_critic_model():
	"""懒加载 Critic/监督模型；普通 reasoning 任务不会顺带初始化它。"""
	return llm_router.get("critic_model")