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
    append_jsonl(events_path, {
        "type": event_type,
        "session_id": session_id,
        "payload": payload,
    })


def append_reflection(session_id: str, task_query: str, status: str, summary: str, lessons: list[str]) -> None:
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
    memory_dir.mkdir(parents=True, exist_ok=True)
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        **record,
    }
    with _write_lock:
        with path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(record, ensure_ascii=False) + "\n")