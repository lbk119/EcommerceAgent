"""System-level Evaluation Agent dimensions and weights."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EvaluationDimensionSpec:
    key: str
    label: str
    weight: float
    release_min_score: float = 0
    blocks_release: bool = False


DIMENSIONS: tuple[EvaluationDimensionSpec, ...] = (
    EvaluationDimensionSpec("business_output_quality", "Business Output Quality", 15),
    EvaluationDimensionSpec("planner_compliance", "Planner Compliance", 15, release_min_score=85, blocks_release=True),
    EvaluationDimensionSpec("subagent_assignment", "Subagent Assignment", 10),
    EvaluationDimensionSpec("tool_contract_schema", "Tool Contract And Schema", 10),
    EvaluationDimensionSpec("runtime_budget", "Runtime Budget And Loop Guard", 10),
    EvaluationDimensionSpec("memory_isolation", "Memory Isolation", 10, release_min_score=95, blocks_release=True),
    EvaluationDimensionSpec("sandbox_security", "Docker Sandbox Security", 10, release_min_score=90, blocks_release=True),
    EvaluationDimensionSpec("prompt_guard_security", "Prompt Guard And Security", 10, release_min_score=90, blocks_release=True),
    EvaluationDimensionSpec("evaluation_reflection", "Evaluation And Reflection Quality", 5),
    EvaluationDimensionSpec("api_contract_observability", "API Contract And Observability", 5),
)

DIMENSION_WEIGHTS = {item.key: item.weight for item in DIMENSIONS}
DIMENSION_SPECS = {item.key: item for item in DIMENSIONS}


def dimension_keys() -> list[str]:
    return [item.key for item in DIMENSIONS]