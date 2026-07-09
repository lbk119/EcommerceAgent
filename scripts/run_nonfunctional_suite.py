from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "output" / "nonfunctional"


PYTEST_TARGETS = {
    "unit": ("tests/unit", OUTPUT_DIR / "python-unit.xml"),
    "contract": ("tests/contract", OUTPUT_DIR / "python-contract.xml"),
    "security": ("tests/security", OUTPUT_DIR / "python-security.xml"),
    "evals": ("tests/evals", OUTPUT_DIR / "python-evals.xml"),
    "e2e": ("tests/e2e", OUTPUT_DIR / "python-e2e.xml"),
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run EcommerceAgent non-functional test layers.")
    for name in ["unit", "contract", "security", "evals", "e2e", "go", "perf", "chaos"]:
        parser.add_argument(f"--{name}", action="store_true", help=f"run {name} layer")
    parser.add_argument("--all", action="store_true", help="run all locally runnable layers and write perf/chaos placeholders")
    parser.add_argument("--mode", choices=["dev", "release"], default="dev", help="summary gate mode")
    parser.add_argument("--perf-results", default=os.getenv("NONFUNCTIONAL_PERF_RESULTS", ""), help="optional Locust/k6/canonical perf JSON to ingest")
    parser.add_argument("--chaos-results", default=os.getenv("NONFUNCTIONAL_CHAOS_RESULTS", ""), help="optional Chaos Toolkit/canonical JSON to ingest")
    parser.add_argument("--no-summary", action="store_true", help="do not collect the final summary after running")
    args = parser.parse_args()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    selected = selected_layers(args)
    if not selected:
        selected = ["unit", "contract", "security", "evals", "go", "perf", "chaos"]

    run_records: list[dict] = []
    exit_code = 0
    for layer in selected:
        if layer in PYTEST_TARGETS:
            result = run_pytest(layer)
        elif layer == "go":
            result = run_go_tests()
        elif layer == "perf":
            result = ingest_or_placeholder(args.perf_results, "perf-results.json", "performance", "Locust/k6 are configured but not executed by default.")
        elif layer == "chaos":
            result = ingest_or_placeholder(args.chaos_results, "chaos-results.json", "chaos_recovery", "Chaos Toolkit experiments are configured but not executed by default.")
        else:
            continue
        run_records.append(result)
        if result["returncode"] != 0:
            exit_code = 1

    (OUTPUT_DIR / "run-metadata.json").write_text(
        json.dumps({"generated_at": utc_now(), "layers": run_records}, indent=2, ensure_ascii=True),
        encoding="utf-8",
    )

    if not args.no_summary:
        summary_result = subprocess.run([sys.executable, str(ROOT / "scripts" / "collect_nonfunctional_report.py"), "--mode", args.mode], cwd=ROOT)
        if summary_result.returncode != 0:
            exit_code = 1
    return exit_code


def selected_layers(args: argparse.Namespace) -> list[str]:
    layers = ["unit", "contract", "security", "evals", "e2e", "go", "perf", "chaos"]
    if args.all:
        return layers
    return [layer for layer in layers if getattr(args, layer)]


def run_pytest(layer: str) -> dict:
    target, junit_path = PYTEST_TARGETS[layer]
    command = [sys.executable, "-m", "pytest", target, "-q", "--junitxml", str(junit_path)]
    if layer in {"security", "evals", "e2e"}:
        command.extend(["-m", layer])
    started = utc_now()
    completed = subprocess.run(command, cwd=ROOT)
    return {"layer": layer, "command": command, "started_at": started, "finished_at": utc_now(), "returncode": completed.returncode, "artifact": str(junit_path.relative_to(ROOT))}


def run_go_tests() -> dict:
    output_path = OUTPUT_DIR / "go-test.json"
    stderr_path = OUTPUT_DIR / "go-test.stderr.txt"
    command = ["go", "test", "-json", "./gateway/internal/..."]
    started = utc_now()
    with output_path.open("w", encoding="utf-8") as stdout_file, stderr_path.open("w", encoding="utf-8") as stderr_file:
        completed = subprocess.run(command, cwd=ROOT, stdout=stdout_file, stderr=stderr_file, text=True)
    return {"layer": "go", "command": command, "started_at": started, "finished_at": utc_now(), "returncode": completed.returncode, "artifact": str(output_path.relative_to(ROOT))}


def write_placeholder(filename: str, layer: str, status: str, message: str) -> dict:
    path = OUTPUT_DIR / filename
    payload = {"layer": layer, "status": status, "score": 0, "passed": 0, "failed": 0, "skipped": 0, "key_findings": [message], "blocking_issues": [], "generated_at": utc_now()}
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")
    return {"layer": layer, "command": [], "started_at": payload["generated_at"], "finished_at": payload["generated_at"], "returncode": 0, "artifact": str(path.relative_to(ROOT))}


def ingest_or_placeholder(source: str, filename: str, layer: str, placeholder_message: str) -> dict:
    if source:
        source_path = Path(source).expanduser()
        if not source_path.is_absolute():
            source_path = ROOT / source_path
        target = OUTPUT_DIR / filename
        if source_path.exists():
            normalized = normalize_external_result(json.loads(source_path.read_text(encoding="utf-8")), layer)
            target.write_text(json.dumps(normalized, indent=2, ensure_ascii=True, allow_nan=False), encoding="utf-8")
            return {"layer": layer, "command": [], "started_at": normalized["generated_at"], "finished_at": normalized["generated_at"], "returncode": 0, "artifact": str(target.relative_to(ROOT))}
        return write_placeholder(filename, layer, "not_run", f"Configured result file not found: {source_path}")
    return write_placeholder(filename, layer, "not_run", placeholder_message)


def normalize_external_result(data: dict, layer: str) -> dict:
    if layer == "performance":
        return normalize_perf_result(data)
    return normalize_chaos_result(data)


def normalize_perf_result(data: dict) -> dict:
    if "status" in data and "score" in data:
        data.setdefault("generated_at", utc_now())
        return data
    metrics = data.get("metrics") or {}
    p95 = first_number(
        data.get("ai_chat_acceptance_p95_ms"),
        metrics.get("ai_chat_acceptance", {}).get("percentiles", {}).get("95"),
        metrics.get("ai_chat_acceptance", {}).get("p95"),
        metrics.get("http_req_duration{name:ai_chat_acceptance}", {}).get("p(95)"),
    )
    job_p95 = first_number(data.get("agent_job_acceptance_p95_ms"), metrics.get("agent_job_acceptance", {}).get("p95"))
    error_rate = first_number(data.get("error_rate"), metrics.get("http_req_failed", {}).get("rate"), 0)
    status = "pass" if p95 and p95 <= 1000 and error_rate < 0.01 else "fail"
    return {
        "layer": "performance",
        "status": status,
        "score": 100 if status == "pass" else 0,
        "passed": 1 if status == "pass" else 0,
        "failed": 0 if status == "pass" else 1,
        "skipped": 0,
        "ai_chat_acceptance_p95_ms": p95,
        "agent_job_acceptance_p95_ms": job_p95,
        "error_rate": error_rate,
        "key_findings": [f"AI Chat acceptance P95: {p95 if p95 is not None else 'unknown'} ms", f"Error rate: {error_rate}"],
        "blocking_issues": [] if status == "pass" else ["Performance threshold failed or missing AI Chat P95."],
        "generated_at": utc_now(),
    }


def normalize_chaos_result(data: dict) -> dict:
    if "status" in data and "score" in data:
        data.setdefault("generated_at", utc_now())
        return data
    experiments = data.get("experiments") or data.get("runs") or []
    if isinstance(experiments, dict):
        experiments = list(experiments.values())
    passed = sum(1 for item in experiments if str(item.get("status") or item.get("state") or "").lower() in {"pass", "passed", "succeeded", "completed"})
    failed = sum(1 for item in experiments if str(item.get("status") or item.get("state") or "").lower() in {"fail", "failed", "error"})
    total = passed + failed
    recovery_rate = first_number(data.get("recovery_success_rate"), passed / total if total else None)
    stuck_running = int(data.get("stuck_running_tasks") or data.get("running_stuck_count") or 0)
    status = "pass" if recovery_rate is not None and recovery_rate >= 0.9 and stuck_running == 0 and failed == 0 else "fail"
    return {
        "layer": "chaos_recovery",
        "status": status,
        "score": round((recovery_rate or 0) * 100, 2),
        "passed": passed,
        "failed": failed,
        "skipped": 0,
        "recovery_success_rate": recovery_rate,
        "stuck_running_tasks": stuck_running,
        "key_findings": [f"Recovery success rate: {recovery_rate if recovery_rate is not None else 'unknown'}", f"Stuck running tasks: {stuck_running}"],
        "blocking_issues": [] if status == "pass" else ["Chaos recovery threshold failed or missing recovery data."],
        "generated_at": utc_now(),
    }


def first_number(*values):
    for value in values:
        if value is None:
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


if __name__ == "__main__":
    raise SystemExit(main())