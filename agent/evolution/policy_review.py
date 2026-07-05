import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml


project_root_path = Path(__file__).parents[2].resolve()
memory_dir = project_root_path / "data" / "memory"
proposals_path = memory_dir / "policy_proposals.jsonl"
policy_overrides_path = project_root_path / "prompt" / "policy_overrides.yml"


def create_policy_proposal(session_id: str, task_query: str, reflection: Dict[str, Any]) -> dict:
    proposal = {
        "proposal_id": str(uuid.uuid4()),
        "status": "pending",
        "session_id": session_id,
        "target": "main_agent.system_prompt_append",
        "task_query": task_query,
        "rationale": reflection.get("summary", ""),
        "instruction": _build_instruction(reflection),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "reviewed_at": None,
    }
    _append_proposal(proposal)
    return proposal


def list_policy_proposals(status: Optional[str] = None) -> List[dict]:
    proposals = _read_proposals()
    if status:
        proposals = [proposal for proposal in proposals if proposal.get("status") == status]
    return proposals


def approve_policy_proposal(proposal_id: str) -> dict:
    proposals = _read_proposals()
    selected = None
    for proposal in proposals:
        if proposal.get("proposal_id") == proposal_id:
            proposal["status"] = "approved"
            proposal["reviewed_at"] = datetime.now(timezone.utc).isoformat()
            selected = proposal
            break
    if not selected:
        raise ValueError(f"策略建议不存在: {proposal_id}")

    _write_proposals(proposals)
    _append_policy_override(selected)
    return selected


def reject_policy_proposal(proposal_id: str) -> dict:
    proposals = _read_proposals()
    selected = None
    for proposal in proposals:
        if proposal.get("proposal_id") == proposal_id:
            proposal["status"] = "rejected"
            proposal["reviewed_at"] = datetime.now(timezone.utc).isoformat()
            selected = proposal
            break
    if not selected:
        raise ValueError(f"策略建议不存在: {proposal_id}")
    _write_proposals(proposals)
    return selected


def _build_instruction(reflection: Dict[str, Any]) -> str:
    lessons = reflection.get("lessons", [])
    if reflection.get("status") == "failed":
        return "当任务出现相似失败迹象时，先检查外部工具可用性、输入文件路径和子智能体边界，再继续推理。"
    if lessons:
        return lessons[0]
    return "执行相似任务时，优先检索长期记忆并复用已验证的成功步骤。"


def _append_proposal(proposal: dict) -> None:
    memory_dir.mkdir(parents=True, exist_ok=True)
    with proposals_path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(proposal, ensure_ascii=False) + "\n")


def _read_proposals() -> List[dict]:
    if not proposals_path.exists():
        return []
    proposals = []
    with proposals_path.open("r", encoding="utf-8") as file:
        for line in file:
            try:
                proposals.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return proposals


def _write_proposals(proposals: List[dict]) -> None:
    memory_dir.mkdir(parents=True, exist_ok=True)
    with proposals_path.open("w", encoding="utf-8") as file:
        for proposal in proposals:
            file.write(json.dumps(proposal, ensure_ascii=False) + "\n")


def _append_policy_override(proposal: dict) -> None:
    overrides = {"main_agent": {"system_prompt_append": []}}
    if policy_overrides_path.exists():
        with policy_overrides_path.open("r", encoding="utf-8") as file:
            loaded = yaml.safe_load(file) or {}
            if isinstance(loaded, dict):
                overrides = loaded

    main_agent = overrides.setdefault("main_agent", {})
    instructions = main_agent.setdefault("system_prompt_append", [])
    instructions.append({
        "proposal_id": proposal["proposal_id"],
        "instruction": proposal["instruction"],
        "approved_at": proposal["reviewed_at"],
    })

    with policy_overrides_path.open("w", encoding="utf-8") as file:
        yaml.safe_dump(overrides, file, allow_unicode=True, sort_keys=False)