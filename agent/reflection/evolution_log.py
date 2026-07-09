"""轻量治理事件与反思 JSONL 日志。

AgentRuntime 的后处理阶段会把任务事件、Critic 状态和反思摘要写到这里，供后续策略评审、
非功能报告和人工审计使用。当前实现是本地 JSONL，接口保持稳定，便于未来替换为数据库。
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict


PROJECT_ROOT = Path(__file__).parents[2].resolve()
GOVERNANCE_DIR = PROJECT_ROOT / "data" / "governance"
EVENTS_PATH = GOVERNANCE_DIR / "task_events.jsonl"
REFLECTIONS_PATH = GOVERNANCE_DIR / "reflections.jsonl"
_WRITE_LOCK = threading.Lock()


def append_task_event(event_type: str, session_id: str, payload: Dict[str, Any]) -> None:
    """追加一条任务治理事件。"""
    append_jsonl(EVENTS_PATH, {"type": event_type, "session_id": session_id, "payload": payload})


def append_reflection(session_id: str, task_query: str, status: str, summary: str, lessons: list[str]) -> None:
    """追加一次任务反思记录。"""
    append_jsonl(
        REFLECTIONS_PATH,
        {
            "type": "task_reflection",
            "session_id": session_id,
            "task_query": task_query,
            "status": status,
            "summary": summary,
            "lessons": lessons,
        },
    )


def append_jsonl(path: Path, record: Dict[str, Any]) -> None:
    """线程安全地向治理 JSONL 文件追加带时间戳的记录。"""
    GOVERNANCE_DIR.mkdir(parents=True, exist_ok=True)
    payload = {"timestamp": datetime.now(timezone.utc).isoformat(), **record}
    with _WRITE_LOCK:
        with path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(payload, ensure_ascii=False) + "\n")
