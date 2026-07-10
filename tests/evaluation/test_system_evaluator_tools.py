from agent.evaluation.system_evaluator import SystemEvaluator


def test_tool_contract_dimension_checks_registered_metadata(tmp_path):
    report = SystemEvaluator(nonfunctional_dir=tmp_path).evaluate(mode="dev")

    dimension = report.dimensions["tool_contract_schema"]

    assert dimension.status == "pass"
    assert dimension.evidence["check_count"] > 0


def test_subagent_assignment_dimension_checks_tool_whitelists(tmp_path):
    report = SystemEvaluator(nonfunctional_dir=tmp_path).evaluate(mode="dev")

    dimension = report.dimensions["subagent_assignment"]
    assert dimension.status == "pass"
    assert dimension.evidence["passed"] == dimension.evidence["check_count"]