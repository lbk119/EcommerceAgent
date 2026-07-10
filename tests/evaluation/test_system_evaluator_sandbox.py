from agent.evaluation.system_evaluator import SystemEvaluator


def test_sandbox_security_dimension_enforces_container_hardening(tmp_path):
    report = SystemEvaluator(nonfunctional_dir=tmp_path).evaluate(mode="dev")

    dimension = report.dimensions["sandbox_security"]
    assert dimension.status == "warn"
    assert dimension.score < 100
    assert dimension.evidence["sandbox_e2e"] == "not_run"
    assert dimension.evidence["check_count"] >= 7


def test_sandbox_security_blocks_agent_side_process_import(monkeypatch, tmp_path):
    from agent.evaluation import system_evaluator

    monkeypatch.setattr(system_evaluator, "source_imports_forbidden", lambda path, forbidden: ["agent/fake.py"])

    report = SystemEvaluator(nonfunctional_dir=tmp_path).evaluate(mode="dev")

    dimension = report.dimensions["sandbox_security"]
    assert dimension.status == "fail"
    assert any(item.severity == "block" for item in dimension.findings)