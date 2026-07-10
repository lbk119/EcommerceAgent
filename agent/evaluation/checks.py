"""Reusable deterministic checks for the system-level Evaluation Agent."""

from __future__ import annotations

import ast
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from agent.evaluation.report import EvaluationFinding


ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class CheckResult:
    name: str
    passed: bool
    message: str
    severity: str = "warn"
    score: float = 100
    evidence: dict[str, Any] | None = None

    def finding(self) -> EvaluationFinding:
        severity = "info" if self.passed else self.severity
        return EvaluationFinding(self.message, severity=severity, source=self.name)


def pass_check(name: str, message: str, evidence: dict[str, Any] | None = None) -> CheckResult:
    return CheckResult(name=name, passed=True, message=message, severity="info", score=100, evidence=evidence or {})


def warn_check(name: str, message: str, score: float = 70, evidence: dict[str, Any] | None = None) -> CheckResult:
    return CheckResult(name=name, passed=False, message=message, severity="warn", score=score, evidence=evidence or {})


def block_check(name: str, message: str, evidence: dict[str, Any] | None = None) -> CheckResult:
    return CheckResult(name=name, passed=False, message=message, severity="block", score=0, evidence=evidence or {})


def score_checks(checks: list[CheckResult]) -> tuple[float, list[EvaluationFinding], dict[str, Any]]:
    if not checks:
        return 0, [EvaluationFinding("No checks were registered.", severity="warn", source="score_checks")], {"check_count": 0}
    score = sum(item.score if not item.passed else 100 for item in checks) / len(checks)
    findings = [item.finding() for item in checks if not item.passed]
    if not findings:
        findings = [EvaluationFinding(f"All {len(checks)} deterministic checks passed.", source="score_checks")]
    return score, findings, {"check_count": len(checks), "passed": sum(1 for item in checks if item.passed), "failed": sum(1 for item in checks if not item.passed)}


def parse_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"exists": False}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        return {"exists": True, "status": "fail", "error": str(error)}
    payload["exists"] = True
    return payload


def parse_junit(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"exists": False, "tests": []}
    root = ElementTree.parse(path).getroot()
    tests = []
    for testcase in root.iter("testcase"):
        failures = list(testcase.findall("failure")) + list(testcase.findall("error"))
        skipped = list(testcase.findall("skipped"))
        tests.append({
            "name": testcase.attrib.get("name", ""),
            "classname": testcase.attrib.get("classname", ""),
            "failed": bool(failures),
            "skipped": bool(skipped),
        })
    return {"exists": True, "tests": tests}


def junit_score(run: dict[str, Any], label: str) -> tuple[float | None, list[EvaluationFinding], dict[str, Any]]:
    if not run.get("exists"):
        return None, [EvaluationFinding(f"{label} artifact not found.", severity="warn", source="junit")], {}
    tests = run.get("tests") or []
    if not tests:
        return 0, [EvaluationFinding(f"{label} artifact contains no tests.", severity="warn", source="junit")], {"tests": 0}
    failed = sum(1 for item in tests if item.get("failed"))
    skipped = sum(1 for item in tests if item.get("skipped"))
    passed = len(tests) - failed - skipped
    score = 100 * passed / len(tests)
    findings = []
    if failed:
        findings.append(EvaluationFinding(f"{label} has {failed} failing tests.", severity="block", source="junit"))
    if skipped:
        findings.append(EvaluationFinding(f"{label} has {skipped} skipped tests.", severity="warn", source="junit"))
    return score, findings, {"passed": passed, "failed": failed, "skipped": skipped, "total": len(tests)}


def source_imports_forbidden(path: Path, forbidden: set[str]) -> list[str]:
    if not path.exists():
        return [f"missing:{path}"]
    findings: list[str] = []
    for file_path in path.rglob("*.py"):
        tree = ast.parse(file_path.read_text(encoding="utf-8"), filename=str(file_path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in forbidden or alias.name.split(".", 1)[0] in forbidden:
                        findings.append(str(file_path.relative_to(ROOT)))
            elif isinstance(node, ast.ImportFrom) and node.module:
                if node.module in forbidden or node.module.split(".", 1)[0] in forbidden:
                    findings.append(str(file_path.relative_to(ROOT)))
    return sorted(set(findings))


def test_artifact_paths(nonfunctional_dir: Path) -> dict[str, Path]:
    return {
        "unit": nonfunctional_dir / "python-unit.xml",
        "contract": nonfunctional_dir / "python-contract.xml",
        "security": nonfunctional_dir / "python-security.xml",
        "evals": nonfunctional_dir / "python-evals.xml",
        "e2e": nonfunctional_dir / "python-e2e.xml",
        "summary": nonfunctional_dir / "summary.json",
        "eval_quality": nonfunctional_dir / "eval-quality.json",
        "performance": nonfunctional_dir / "perf-results.json",
        "chaos": nonfunctional_dir / "chaos-results.json",
    }


def env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "on"}