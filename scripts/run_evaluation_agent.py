from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.evaluation.system_evaluator import SystemEvaluator


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the system-level Evaluation Agent.")
    parser.add_argument("--mode", choices=["dev", "release"], default="dev")
    parser.add_argument("--nonfunctional-dir", default=str(ROOT / "output" / "nonfunctional"))
    parser.add_argument("--output-dir", default=str(ROOT / "output" / "evaluation"))
    parser.add_argument("--run-sandbox-e2e", action="store_true", help="run tests/e2e/test_docker_sandbox.py before scoring and write python-e2e.xml")
    args = parser.parse_args()

    nonfunctional_dir = Path(args.nonfunctional_dir)
    if args.run_sandbox_e2e:
        nonfunctional_dir.mkdir(parents=True, exist_ok=True)
        completed = subprocess.run([
            sys.executable,
            "-m",
            "pytest",
            "tests/e2e/test_docker_sandbox.py",
            "-q",
            "-rs",
            "--junitxml",
            str(nonfunctional_dir / "python-e2e.xml"),
        ], cwd=ROOT)
        if completed.returncode != 0:
            print("Sandbox E2E failed; report will include the junit artifact and release gate may block.")

    report = SystemEvaluator(root=ROOT, nonfunctional_dir=nonfunctional_dir).evaluate(mode=args.mode)
    report.write(Path(args.output_dir))
    print(f"Evaluation report written to {Path(args.output_dir) / 'evaluation_report.json'}")
    print(f"Decision: {report.release_decision}; score={report.overall_score:.2f}; confidence={report.confidence:.2f}")
    return 1 if report.release_decision == "BLOCK" and args.mode == "release" else 0


if __name__ == "__main__":
    raise SystemExit(main())