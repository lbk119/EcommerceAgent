"""策略建议审核与 prompt override 写入。

Agent 完成任务后会生成 reflection；本模块把 reflection 转换为“待审核策略建议”，人工批准后才写入
prompt/policy_overrides.yml。这样 Agent 可以沉淀经验，但不会未经审核就改写系统提示词。
"""

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
    """根据一次任务反思创建待审核策略建议。"""
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
    """读取策略建议列表，可按 pending/approved/rejected 过滤。"""
    proposals = _read_proposals()
    if status:
        proposals = [proposal for proposal in proposals if proposal.get("status") == status]
    return proposals


def approve_policy_proposal(proposal_id: str) -> dict:
    """批准策略建议，并把 instruction 追加写入 policy_overrides.yml。"""
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
    """拒绝策略建议，只更新 proposals 状态，不改 prompt override。"""
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
    """把 reflection 压缩成一条可追加到 system prompt 的 instruction。"""
    lessons = reflection.get("lessons", [])
    if reflection.get("status") == "failed":
        return "当任务出现相似失败迹象时，先检查外部工具可用性、输入文件路径和子智能体边界，再继续推理。"
    if lessons:
        return lessons[0]
    return "执行相似任务时，优先检索长期记忆并复用已验证的成功步骤。"


def _append_proposal(proposal: dict) -> None:
    """追加写 JSONL，一条建议一行，便于人工审阅和增量读取。"""
    memory_dir.mkdir(parents=True, exist_ok=True)
    with proposals_path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(proposal, ensure_ascii=False) + "\n")


def _read_proposals() -> List[dict]:
    """读取 JSONL 建议文件，坏行跳过，避免单条损坏影响整个列表。"""
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
    """重写建议文件，用于 approve/reject 更新状态。"""
    memory_dir.mkdir(parents=True, exist_ok=True)
    with proposals_path.open("w", encoding="utf-8") as file:
        for proposal in proposals:
            file.write(json.dumps(proposal, ensure_ascii=False) + "\n")


def _append_policy_override(proposal: dict) -> None:
    """把已批准策略追加到 YAML override。

    prompts.load_prompt_content 会在下次 reload 时把这些 instruction 合并到主 Agent system prompt。
    """
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