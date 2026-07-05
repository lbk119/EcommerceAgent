from typing import Optional


def build_task_reflection(task_query: str, result: Optional[str] = None, error: Optional[str] = None) -> dict:
    if error:
        return {
            "status": "failed",
            "summary": f"任务执行失败: {error}",
            "lessons": [
                "保留失败输入和异常信息，后续用于归因和重试策略优化。",
                "优先检查工具、子智能体或外部服务是否不可用。",
            ],
        }

    result_text = result or ""
    return {
        "status": "succeeded",
        "summary": result_text[:500],
        "lessons": [
            "记录成功任务的输入和输出摘要，后续可作为相似任务的经验上下文。",
            "若用户继续追问，可优先检索同一 session 的历史产物和反思记录。",
        ],
    }