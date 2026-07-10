from agent.evaluation.system_evaluator import SystemEvaluator


def test_prompt_guard_security_dimension_passes_core_redteam_checks(tmp_path):
    report = SystemEvaluator(nonfunctional_dir=tmp_path).evaluate(mode="dev")

    dimension = report.dimensions["prompt_guard_security"]
    assert dimension.status == "pass"
    assert dimension.score >= 95


def test_api_contract_observability_dimension_requires_e2e_for_full_score(tmp_path):
    report = SystemEvaluator(nonfunctional_dir=tmp_path).evaluate(mode="dev")

    dimension = report.dimensions["api_contract_observability"]
    assert dimension.status == "warn"
    assert dimension.score < 100
    assert dimension.evidence["e2e"] == "not_run"
    assert dimension.evidence["passed"] == dimension.evidence["check_count"]