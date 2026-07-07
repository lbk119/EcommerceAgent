"""任务事件和反思 JSONL 追加写入。

这些文件是轻量运行记忆，不等同于长期语义记忆：
- task_events.jsonl 记录任务生命周期、失败、记忆审核等结构化事件；
- reflections.jsonl 记录任务成功/失败后的复盘摘要。

写入使用进程内锁，保证多线程后台任务同时追加时不交错写坏单行 JSON。
"""

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict


project_root_path = Path(__file__).parents[2].resolve()
memory_dir = project_root_path / "data" / "memory"
events_path = memory_dir / "task_events.jsonl"
reflections_path = memory_dir / "reflections.jsonl"
_write_lock = threading.Lock()


def append_task_event(event_type: str, session_id: str, payload: Dict[str, Any]) -> None:
    """追加一条任务事件。"""
    append_jsonl(events_path, {
        "type": event_type,
        "session_id": session_id,
        "payload": payload,
    })


def append_reflection(session_id: str, task_query: str, status: str, summary: str, lessons: list[str]) -> None:
    """追加一条任务反思记录。"""
    record = {
        "type": "task_reflection",
        "session_id": session_id,
        "task_query": task_query,
        "status": status,
        "summary": summary,
        "lessons": lessons,
    }
    append_jsonl(reflections_path, record)


def append_jsonl(path: Path, record: Dict[str, Any]) -> None:
    """线程安全地追加一行 JSONL。"""
    memory_dir.mkdir(parents=True, exist_ok=True)
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        **record,
    }
    with _write_lock:
        with path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(record, ensure_ascii=False) + "\n")