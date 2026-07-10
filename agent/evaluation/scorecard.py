"""Evaluation scorecard and release gate logic."""

from __future__ import annotations

from agent.evaluation.dimensions import DIMENSION_SPECS, DIMENSION_WEIGHTS
from agent.evaluation.report import DimensionEvaluation, EvaluationFinding, EvaluationReport


def status_for_score(score: float, *, warn_at: float = 75, pass_at: float = 90) -> str:
    if score >= pass_at:
        return "pass"
    if score >= warn_at:
        return "warn"
    return "fail"


def weighted_score(dimensions: dict[str, DimensionEvaluation]) -> float:
    total_weight = sum(DIMENSION_WEIGHTS.values()) or 1
    return sum(dimensions[key].score * DIMENSION_WEIGHTS[key] for key in DIMENSION_WEIGHTS) / total_weight


def build_dimension(key: str, score: float, findings: list[EvaluationFinding], *, evidence: dict | None = None, pass_at: float = 90, warn_at: float = 75) -> DimensionEvaluation:
    spec = DIMENSION_SPECS[key]
    blocking = any(item.severity == "block" for item in findings)
    status = "fail" if blocking else status_for_score(score, warn_at=warn_at, pass_at=pass_at)
    return DimensionEvaluation(key=key, label=spec.label, score=max(0, min(float(score), 100)), status=status, findings=findings, evidence=evidence or {})


def decide_release(mode: str, dimensions: dict[str, DimensionEvaluation], coverage_notes: list[str]) -> tuple[str, list[str]]:
    blocking: list[str] = []
    for key, spec in DIMENSION_SPECS.items():
        dimension = dimensions[key]
        for finding in dimension.findings:
            if finding.severity == "block":
                blocking.append(f"{spec.label}: {finding.message}")
        if mode == "release" and spec.blocks_release and dimension.score < spec.release_min_score:
            blocking.append(f"{spec.label} score {dimension.score:.1f} is below release minimum {spec.release_min_score:.1f}.")

    if mode == "release":
        if any("e2e" in note.lower() and "not_run" in note.lower() for note in coverage_notes):
            blocking.append("Release mode requires E2E results.")
        if any("performance not_run" in note.lower() for note in coverage_notes):
            blocking.append("Release mode requires performance results.")
        if any("chaos not_run" in note.lower() for note in coverage_notes):
            blocking.append("Release mode requires chaos results.")

    if blocking:
        return "BLOCK", blocking
    if any(item.status in {"warn", "not_run", "fail"} for item in dimensions.values()) or coverage_notes:
        return "PASS_WITH_WARNINGS", []
    return "PASS", []


def build_report(mode: str, dimensions: dict[str, DimensionEvaluation], coverage_notes: list[str], metadata: dict | None = None) -> EvaluationReport:
    overall = weighted_score(dimensions)
    decision, blocking = decide_release(mode, dimensions, coverage_notes)
    missing = sum(1 for item in dimensions.values() if item.status == "not_run")
    failed = sum(1 for item in dimensions.values() if item.status == "fail")
    coverage_penalty = min(0.3, sum(0.08 if "not_run" in note else 0.03 for note in coverage_notes))
    confidence = max(0.35, 0.95 - missing * 0.06 - failed * 0.12 - coverage_penalty)
    return EvaluationReport(
        overall_score=overall,
        release_decision=decision,
        confidence=confidence,
        dimensions=dimensions,
        blocking_findings=blocking,
        coverage_notes=coverage_notes,
        mode=mode,
        metadata=metadata or {},
    )