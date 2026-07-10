"""Evaluation Agent report structures."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal


FindingSeverity = Literal["info", "warn", "block"]
DimensionStatus = Literal["pass", "warn", "fail", "not_run"]
ReleaseDecision = Literal["PASS", "PASS_WITH_WARNINGS", "BLOCK"]


@dataclass(frozen=True)
class EvaluationFinding:
    message: str
    severity: FindingSeverity = "info"
    source: str = "deterministic"

    def to_dict(self) -> dict[str, Any]:
        return {"message": self.message, "severity": self.severity, "source": self.source}


@dataclass(frozen=True)
class DimensionEvaluation:
    key: str
    label: str
    score: float
    status: DimensionStatus
    findings: list[EvaluationFinding] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": round(self.score, 2),
            "status": self.status,
            "label": self.label,
            "findings": [item.message for item in self.findings],
            "finding_details": [item.to_dict() for item in self.findings],
            "evidence": self.evidence,
        }


@dataclass(frozen=True)
class EvaluationReport:
    overall_score: float
    release_decision: ReleaseDecision
    confidence: float
    dimensions: dict[str, DimensionEvaluation]
    blocking_findings: list[str] = field(default_factory=list)
    coverage_notes: list[str] = field(default_factory=list)
    generated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"))
    mode: str = "dev"
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "mode": self.mode,
            "overall_score": round(self.overall_score, 2),
            "release_decision": self.release_decision,
            "confidence": round(self.confidence, 2),
            "dimensions": {key: value.to_dict() for key, value in self.dimensions.items()},
            "blocking_findings": self.blocking_findings,
            "coverage_notes": self.coverage_notes,
            "metadata": self.metadata,
        }

    def write(self, output_dir: Path) -> None:
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "evaluation_report.json").write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=True, allow_nan=False), encoding="utf-8")
        (output_dir / "evaluation_report.md").write_text(self.to_markdown(), encoding="utf-8")

    def to_markdown(self) -> str:
        lines = [
            "# Evaluation Agent Report",
            "",
            f"- Generated at: `{self.generated_at}`",
            f"- Mode: `{self.mode}`",
            f"- Overall score: **{self.overall_score:.2f}/100**",
            f"- Release decision: **{self.release_decision}**",
            f"- Confidence: **{self.confidence:.2f}**",
            "",
            "## Dimensions",
            "",
            "| Dimension | Status | Score | Findings |",
            "|---|---:|---:|---|",
        ]
        for dimension in self.dimensions.values():
            findings = "<br>".join(item.message for item in dimension.findings[:4]) or "No findings"
            lines.append(f"| {dimension.label} | {dimension.status} | {dimension.score:.2f} | {findings} |")
        lines.extend(["", "## Blocking Findings"])
        lines.extend([f"- {item}" for item in self.blocking_findings] or ["- None"])
        lines.extend(["", "## Coverage Notes"])
        lines.extend([f"- {item}" for item in self.coverage_notes] or ["- None"])
        lines.append("")
        return "\n".join(lines)