"""任务反思摘要生成。

这里不是模型反思，而是轻量、确定性的任务结果摘要：成功任务记录输出摘要和经验；失败任务记录异常
和排查方向。result_pipeline 会把这些反思写入本地 memory/evolution 文件，供后续策略建议使用。
"""

from typing import Optional


def build_task_reflection(task_query: str, result: Optional[str] = None, error: Optional[str] = None) -> dict:
    """根据任务结果或错误生成反思结构。"""
    if error:
        # 失败反思优先保留错误和排查方向，不尝试猜业务结论。
        return {
            "status": "failed",
            "summary": f"任务执行失败: {error}",
            "lessons": [
                "保留失败输入和异常信息，后续用于归因和重试策略优化。",
                "优先检查工具、子智能体或外部服务是否不可用。",
            ],
        }

    result_text = result or ""
    # 成功反思只保存前 500 字摘要，避免把完整报告重复写入长期运行数据。
    return {
        "status": "succeeded",
        "summary": result_text[:500],
        "lessons": [
            "记录成功任务的输入和输出摘要，后续可作为相似任务的经验上下文。",
            "若用户继续追问，可优先检索同一 session 的历史产物和反思记录。",
        ],
    }