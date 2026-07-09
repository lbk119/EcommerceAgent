from __future__ import annotations

import json
import argparse
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "output" / "nonfunctional"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


WEIGHTS = {
    "code_unit": 5,
    "contract_schema": 5,
    "planner_quality": 6,
    "hallucination_quality": 2,
    "memory_isolation": 5,
    "agent_orchestration": 6,
    "security_redteam": 8,
    "e2e_flow": 6,
    "deepagents_runtime": 7,
    "realtime_chat_profile": 6,
    "standard_profile_latency": 5,
    "memory_store": 5,
    "hitl_safety": 5,
    "loop_guard": 5,
    "profile_safety": 5,
    "mcp_whitelist": 3,
    "filesystem_sandbox": 5,
    "performance": 6,
    "chaos_recovery": 5,
}


@dataclass
class Dimension:
    status: str = "not_run"
    score: float = 0.0
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    key_findings: list[str] = field(default_factory=list)
    blocking_issues: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "score": round(self.score, 2),
            "passed": self.passed,
            "failed": self.failed,
            "skipped": self.skipped,
            "key_findings": self.key_findings,
            "blocking_issues": self.blocking_issues,
        }


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect non-functional test artifacts into a summary report.")
    parser.add_argument("--mode", choices=["dev", "release"], default="dev", help="gate mode for release decision")
    args = parser.parse_args()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    dimensions = build_dimensions()
    metadata = deepagents_metadata(dimensions, mode=args.mode)
    overall_score = weighted_score(dimensions)
    blocking_issues = [issue for item in dimensions.values() for issue in item.blocking_issues]
    mode_blockers = mode_blocking_issues(args.mode, dimensions)
    mode_blockers.extend(metadata_blocking_issues(args.mode, metadata))
    blocking_issues.extend(mode_blockers)
    top_risks = [*mode_blockers, *build_top_risks(dimensions)][:8]
    confidence, coverage_notes = confidence_and_coverage(args.mode, dimensions)
    release_decision = decide_release(overall_score, dimensions, blocking_issues)
    summary = {
        "generated_at": utc_now(),
        "git_commit": git_commit(),
        "mode": args.mode,
        **metadata,
        "confidence": confidence,
        "coverage_notes": coverage_notes,
        "overall_score": round(overall_score, 2),
        "release_decision": release_decision,
        "dimensions": {name: dimensions[name].to_dict() for name in WEIGHTS},
        "top_risks": top_risks,
        "recommended_actions": recommended_actions(dimensions, blocking_issues),
    }
    (OUTPUT_DIR / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=True, allow_nan=False), encoding="utf-8")
    (OUTPUT_DIR / "summary.md").write_text(render_markdown(summary), encoding="utf-8")
    return 0


def build_dimensions() -> dict[str, Dimension]:
    dimensions = {name: Dimension(key_findings=["No artifact found."]) for name in WEIGHTS}
    unit = parse_junit(OUTPUT_DIR / "python-unit.xml")
    contract = parse_junit(OUTPUT_DIR / "python-contract.xml")
    security = parse_junit(OUTPUT_DIR / "python-security.xml")
    e2e = parse_junit(OUTPUT_DIR / "python-e2e.xml")
    go = parse_go_json(OUTPUT_DIR / "go-test.json")
    eval_quality = parse_json_file(OUTPUT_DIR / "eval-quality.json")
    perf = parse_json_file(OUTPUT_DIR / "perf-results.json")
    chaos = parse_json_file(OUTPUT_DIR / "chaos-results.json")

    dimensions["code_unit"] = combine_test_runs("code_unit", [unit, go], hard_block=False)
    dimensions["contract_schema"] = junit_dimension(contract, "contract_schema")
    dimensions["planner_quality"] = eval_dimension(eval_quality, "planner_quality", "pass_rate", "Planner quality mock pass rate")
    dimensions["hallucination_quality"] = eval_dimension(eval_quality, "hallucination_quality", "hallucination_pass_rate", "Hallucination-risk checks")
    dimensions["memory_isolation"] = filtered_dimension([unit, security], ["memory", "tenant", "isolation"], "memory_isolation", block_on_failure=True)
    dimensions["agent_orchestration"] = filtered_dimension([unit], ["agent_planner", "business_agents", "unified_agent_executor", "multi_agent_reducer", "planner"], "agent_orchestration", block_on_failure=True)
    dimensions["deepagents_runtime"] = filtered_dimension([unit], ["deepagents_native", "native_runtime", "subagents_registered"], "deepagents_runtime", block_on_failure=True)
    dimensions["realtime_chat_profile"] = filtered_dimension([unit, e2e], ["realtime_chat", "realtime_deepagents", "ai_chat_accepts"], "realtime_chat_profile", block_on_failure=True)
    dimensions["standard_profile_latency"] = filtered_dimension([unit], ["standard_profile_latency", "p90", "acceptedlatency"], "standard_profile_latency", block_on_failure=False)
    dimensions["memory_store"] = filtered_dimension([unit], ["memory_factory", "memory_namespace", "memory_store"], "memory_store", block_on_failure=True)
    dimensions["hitl_safety"] = filtered_dimension([unit], ["hitl", "human_approval", "interrupt_on"], "hitl_safety", block_on_failure=True)
    dimensions["loop_guard"] = filtered_dimension([unit], ["loop_guard", "runtimeguard", "guard_blocks", "blocks_repeated", "blocks_model_budget"], "loop_guard", block_on_failure=True)
    dimensions["profile_safety"] = filtered_dimension([unit], ["profile", "subagent", "realtime_profile"], "profile_safety", block_on_failure=True)
    dimensions["mcp_whitelist"] = filtered_dimension([unit], ["mcp_policy", "mcp_whitelist", "mcp"], "mcp_whitelist", block_on_failure=True)
    dimensions["filesystem_sandbox"] = filtered_dimension([unit], ["filesystem", "path_traversal", "sandbox", "permissions"], "filesystem_sandbox", block_on_failure=True)
    dimensions["security_redteam"] = junit_dimension(security, "security_redteam", block_on_failure=True)
    dimensions["e2e_flow"] = junit_dimension(e2e, "e2e_flow", block_on_failure=True, skipped_is_status=True)
    dimensions["performance"] = external_dimension(perf, "performance", block_if=lambda data: float(data.get("ai_chat_acceptance_p95_ms") or 0) > 1000)
    dimensions["chaos_recovery"] = external_dimension(chaos, "chaos_recovery")
    return dimensions


def parse_junit(path: Path) -> dict:
    if not path.exists():
        return {"exists": False, "tests": []}
    root = ElementTree.parse(path).getroot()
    testcases = []
    for testcase in root.iter("testcase"):
        failures = list(testcase.findall("failure")) + list(testcase.findall("error"))
        skipped = list(testcase.findall("skipped"))
        testcases.append({
            "name": str(testcase.attrib.get("name") or ""),
            "classname": str(testcase.attrib.get("classname") or ""),
            "failed": bool(failures),
            "skipped": bool(skipped),
        })
    return {"exists": True, "tests": testcases}


def parse_go_json(path: Path) -> dict:
    if not path.exists():
        return {"exists": False, "tests": []}
    test_events: dict[tuple[str, str], dict] = {}
    package_failures = 0
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        action = event.get("Action")
        test_name = event.get("Test")
        package = event.get("Package", "")
        if test_name:
            test_events[(package, test_name)] = {"name": test_name, "classname": package, "failed": action == "fail", "skipped": action == "skip"}
        elif action == "fail":
            package_failures += 1
    tests = list(test_events.values())
    return {"exists": True, "tests": tests, "package_failures": package_failures}


def parse_json_file(path: Path) -> dict:
    if not path.exists():
        return {"exists": False}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        return {"exists": True, "status": "fail", "score": 0, "failed": 1, "key_findings": [f"Invalid JSON: {error}"], "blocking_issues": [f"Invalid JSON artifact: {path.name}"]}
    data["exists"] = True
    return data


def junit_dimension(run: dict, name: str, block_on_failure: bool = False, skipped_is_status: bool = False) -> Dimension:
    if not run.get("exists"):
        return Dimension(status="not_run", key_findings=[f"{name} artifact not found."])
    tests = run.get("tests", [])
    passed = sum(1 for test in tests if not test["failed"] and not test["skipped"])
    failed = sum(1 for test in tests if test["failed"])
    skipped = sum(1 for test in tests if test["skipped"])
    total = passed + failed + skipped
    if total == 0:
        status = "not_run"
        score = 0
    elif failed:
        status = "fail"
        score = 0
    elif skipped_is_status and passed == 0 and skipped > 0:
        status = "skipped"
        score = 0
    elif skipped:
        status = "warn"
        score = max(0, passed / total * 100)
    else:
        status = "pass"
        score = 100
    blocking = [f"{name} has {failed} failing tests."] if block_on_failure and failed else []
    return Dimension(status=status, score=score, passed=passed, failed=failed, skipped=skipped, key_findings=[f"{passed} passed, {failed} failed, {skipped} skipped."], blocking_issues=blocking)


def combine_test_runs(name: str, runs: list[dict], hard_block: bool = False) -> Dimension:
    existing = [run for run in runs if run.get("exists")]
    if not existing:
        return Dimension(status="not_run", key_findings=[f"{name} artifacts not found."])
    merged = {"exists": True, "tests": []}
    package_failures = 0
    for run in existing:
        merged["tests"].extend(run.get("tests", []))
        package_failures += int(run.get("package_failures") or 0)
    dimension = junit_dimension(merged, name, block_on_failure=hard_block)
    if package_failures:
        dimension.failed += package_failures
        dimension.status = "fail"
        dimension.score = 0
        dimension.blocking_issues.append(f"{name} has {package_failures} package-level Go failures.")
    return dimension


def filtered_dimension(runs: list[dict], keywords: list[str], name: str, block_on_failure: bool = False) -> Dimension:
    tests = []
    lowered = [keyword.lower() for keyword in keywords]
    for run in runs:
        if not run.get("exists"):
            continue
        for test in run.get("tests", []):
            label = f"{test.get('classname', '')}.{test.get('name', '')}".lower()
            if any(keyword in label for keyword in lowered):
                tests.append(test)
    return junit_dimension({"exists": bool(tests), "tests": tests}, name, block_on_failure=block_on_failure)


def eval_dimension(data: dict, name: str, metric: str, label: str) -> Dimension:
    if not data.get("exists"):
        return Dimension(status="not_run", key_findings=["eval-quality.json not found."])
    if data.get("status") == "fail":
        return Dimension(status="fail", score=0, failed=1, key_findings=data.get("key_findings", []), blocking_issues=data.get("blocking_issues", []))
    rate = float(data.get(metric) or 0)
    score = max(0, min(rate * 100, 100))
    if metric == "hallucination_pass_rate":
        passed = int(data.get("hallucination_passed") or 0)
        failed = int(data.get("hallucination_failed") or 0)
    else:
        passed = int(data.get("passed") or 0)
        failed = int(data.get("failed") or 0)
    status = "pass" if score >= 90 else "warn" if score >= 75 else "fail"
    blocking = []
    if any(not item.get("checks", {}).get("role_boundary", True) for item in data.get("results", [])):
        blocking.append("Realtime/DeepAgents role boundary check failed.")
        status = "fail"
    return Dimension(status=status, score=score, passed=passed, failed=failed, skipped=int(data.get("skipped") or 0), key_findings=[f"{label}: {score:.1f}%"], blocking_issues=blocking)


def external_dimension(data: dict, name: str, block_if=None) -> Dimension:
    if not data.get("exists"):
        return Dimension(status="not_run", key_findings=[f"{name} artifact not found."])
    status = str(data.get("status") or "not_run")
    score = float(data.get("score") or (100 if status == "pass" else 0))
    blocking = list(data.get("blocking_issues") or [])
    if block_if and block_if(data):
        blocking.append("AI Chat acceptance P95 exceeds 1000 ms.")
        status = "fail"
        score = 0
    return Dimension(status=status, score=score, passed=int(data.get("passed") or 0), failed=int(data.get("failed") or 0), skipped=int(data.get("skipped") or 0), key_findings=list(data.get("key_findings") or []), blocking_issues=blocking)


def weighted_score(dimensions: dict[str, Dimension]) -> float:
    return sum(dimensions[name].score * weight / 100 for name, weight in WEIGHTS.items())


def decide_release(score: float, dimensions: dict[str, Dimension], blocking_issues: list[str]) -> str:
    if blocking_issues or any(item.status == "fail" and item.blocking_issues for item in dimensions.values()):
        return "BLOCK"
    if score < 85 or any(item.status in {"warn", "skipped", "not_run", "fail"} for item in dimensions.values()):
        return "WARN"
    return "PASS"


def mode_blocking_issues(mode: str, dimensions: dict[str, Dimension]) -> list[str]:
    if mode != "release":
        return []
    issues: list[str] = []
    if dimensions["e2e_flow"].status != "pass":
        issues.append("Release mode requires E2E flow to pass.")
    if dimensions["performance"].status != "pass":
        issues.append("Release mode requires performance thresholds to pass.")
    if dimensions["security_redteam"].status != "pass":
        issues.append("Release mode requires security red-team tests to pass.")
    if dimensions["memory_isolation"].status != "pass":
        issues.append("Release mode requires memory isolation checks to pass.")
    if dimensions["agent_orchestration"].status != "pass":
        issues.append("Release mode requires agent orchestration checks to pass.")
    for name, message in {
        "deepagents_runtime": "Release mode requires deepagents-native runtime checks to pass.",
        "loop_guard": "Release mode requires loop guard checks to pass.",
        "hitl_safety": "Release mode requires HITL safety checks to pass.",
        "filesystem_sandbox": "Release mode requires filesystem sandbox checks to pass.",
        "memory_store": "Release mode requires memory store configuration checks to pass.",
        "profile_safety": "Release mode requires profile safety checks to pass.",
        "mcp_whitelist": "Release mode requires MCP whitelist checks to pass.",
    }.items():
        if dimensions[name].status != "pass":
            issues.append(message)
    if dimensions["chaos_recovery"].status == "fail":
        issues.append("Release mode blocks on failed chaos recovery checks.")
    return issues


def metadata_blocking_issues(mode: str, metadata: dict) -> list[str]:
    if mode != "release":
        return []
    issues: list[str] = []
    if not metadata.get("memory_persistence_ready"):
        issues.append("Release mode requires a production-ready deepagents memory store.")
    return issues


def confidence_and_coverage(mode: str, dimensions: dict[str, Dimension]) -> tuple[str, list[str]]:
    notes: list[str] = []
    if dimensions["e2e_flow"].status in {"skipped", "not_run"}:
        notes.append("E2E skipped because gateway is not running or the E2E layer was not selected.")
    if dimensions["performance"].status == "not_run":
        notes.append("Performance is configured but not executed; no AI Chat P95 is available.")
    if dimensions["chaos_recovery"].status == "not_run":
        notes.append("Chaos experiments are configured but not executed; no recovery rate is available.")
    if dimensions["planner_quality"].status in {"pass", "warn"}:
        notes.append("Planner eval is mock-only unless RUN_LIVE_LLM_EVALS=1 is set.")
    missing = sum(1 for item in dimensions.values() if item.status in {"skipped", "not_run"})
    failed = sum(1 for item in dimensions.values() if item.status == "fail")
    if failed:
        return "low", notes
    if missing == 0 and mode == "release":
        return "high", notes
    return "medium" if missing <= 3 else "low", notes


def build_top_risks(dimensions: dict[str, Dimension]) -> list[str]:
    risks = []
    for name, dimension in dimensions.items():
        if dimension.blocking_issues:
            risks.extend(dimension.blocking_issues)
        elif dimension.status in {"warn", "skipped", "not_run", "fail"}:
            risks.append(f"{name}: {dimension.status}, score {dimension.score:.1f}.")
    return risks[:8]


def recommended_actions(dimensions: dict[str, Dimension], blocking_issues: list[str]) -> list[str]:
    actions = []
    if blocking_issues:
        actions.append("Fix blocking issues before release gate approval.")
    if dimensions["e2e_flow"].status in {"skipped", "not_run"}:
        actions.append("Run E2E smoke against a live gateway before release.")
    if dimensions["performance"].status == "not_run":
        actions.append("Run Locust or k6 and publish perf-results.json with AI Chat P95.")
    if dimensions["chaos_recovery"].status == "not_run":
        actions.append("Run at least one Chaos Toolkit experiment in staging.")
    if dimensions["planner_quality"].score < 95:
        actions.append("Review failing planner eval cases and update registry or dataset expectations.")
    if dimensions["deepagents_runtime"].status != "pass":
        actions.append("Fix deepagents-native profile/subagent/runtime registration tests.")
    return actions or ["Maintain current gates and watch trend regressions."]


def deepagents_metadata(dimensions: dict[str, Dimension], *, mode: str = "dev") -> dict:
    deepagents_is_enabled = False
    standard_subagents: list[str] = []
    deep_subagents: list[str] = []
    try:
        from agent.subagent.config import deepagents_enabled
        from agent.subagent.subagents import get_subagent_specs

        deepagents_is_enabled = deepagents_enabled("standard")
        standard_subagents = [item.name for item in get_subagent_specs("standard")]
        deep_subagents = [item.name for item in get_subagent_specs("deep")]
    except Exception:
        pass
    try:
        from agent.memory import MemoryBackendFactory

        store_backend = "unknown"
        persistence_ready = False
        try:
            memory_backend = MemoryBackendFactory().build(production=mode == "release")
            store_backend = memory_backend.backend
            persistence_ready = memory_backend.persistence_ready
        except Exception as error:
            store_backend = f"misconfigured:{error.__class__.__name__}"
        return {
            "deepagents_enabled": deepagents_is_enabled,
            "store_backend": store_backend,
            "memory_persistence_ready": persistence_ready,
            "realtime_tools_count": 0,
            "standard_subagents": standard_subagents,
            "deep_subagents": deep_subagents,
            "loop_guard_triggered_count": dimensions.get("loop_guard", Dimension()).passed,
            "standard_job_p90_seconds": None,
            "ai_chat_accepted_p95_ms": None,
            "hitl_interrupt_tests": dimensions.get("hitl_safety", Dimension()).passed,
            "mcp_whitelist_status": dimensions.get("mcp_whitelist", Dimension()).status,
        }
    except Exception as error:
        return {
            "deepagents_enabled": deepagents_is_enabled,
            "store_backend": f"unknown:{error.__class__.__name__}",
            "memory_persistence_ready": False,
            "realtime_tools_count": 0,
            "standard_subagents": standard_subagents,
            "deep_subagents": deep_subagents,
            "loop_guard_triggered_count": 0,
            "standard_job_p90_seconds": None,
            "ai_chat_accepted_p95_ms": None,
            "hitl_interrupt_tests": 0,
            "mcp_whitelist_status": "unknown",
        }


def render_markdown(summary: dict) -> str:
    lines = [
        "# 非功能测试健康报告",
        "",
        f"- 生成时间: `{summary['generated_at']}`",
        f"- Git commit: `{summary.get('git_commit') or 'unknown'}`",
        f"- Gate mode: `{summary.get('mode', 'dev')}`",
        f"- 可信度: **{summary.get('confidence', 'unknown')}**",
        f"- 总分: **{summary['overall_score']}/100**",
        f"- 上线结论: **{summary['release_decision']}**",
        "",
        "## 维度得分",
        "",
        "| 维度 | 状态 | 分数 | 通过 | 失败 | 跳过 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for name, item in summary["dimensions"].items():
        lines.append(f"| `{name}` | {item['status']} | {item['score']} | {item['passed']} | {item['failed']} | {item['skipped']} |")
    lines.extend(["", "## 覆盖说明"])
    lines.extend([f"- {note}" for note in summary.get("coverage_notes", [])] or ["- No coverage limitations reported."])
    lines.extend(["", "## 主要风险"])
    risks = summary.get("top_risks") or ["No major risks reported."]
    lines.extend([f"- {risk}" for risk in risks])
    lines.extend(["", "## 建议动作"])
    lines.extend([f"- {action}" for action in summary.get("recommended_actions", [])])
    lines.append("")
    return "\n".join(lines)


def git_commit() -> str:
    try:
        result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=False)
    except OSError:
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


if __name__ == "__main__":
    raise SystemExit(main())
