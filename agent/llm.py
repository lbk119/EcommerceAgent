from agent.core.llm_router import llm_router


def get_fast_model():
    """Fast-tier model for chat, routing, and lightweight summaries."""
    return llm_router.get("fast_model")


def get_standard_model():
    """Fast-tier model for standard workflows and SQL assistant tasks."""
    return llm_router.get("standard_model")


def get_deep_model():
    """Deep-tier model for explicitly deep Agent tasks."""
    return llm_router.get("deep_model")


def get_critic_model():
    """Fast-tier model for critic and supervisor checks."""
    return llm_router.get("critic_model")
