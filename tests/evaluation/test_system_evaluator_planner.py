from agent.evaluation.system_evaluator import SystemEvaluator


def test_planner_compliance_dimension_passes_without_artifacts(tmp_path):
    report = SystemEvaluator(nonfunctional_dir=tmp_path).evaluate(mode="dev")

    dimension = report.dimensions["planner_compliance"]
    assert dimension.status == "pass"
    assert dimension.score >= 90


def test_release_mode_blocks_without_required_external_artifacts(tmp_path):
    report = SystemEvaluator(nonfunctional_dir=tmp_path).evaluate(mode="release")

    assert report.release_decision == "BLOCK"
    assert "Release mode requires E2E results." in report.blocking_findings


def test_dev_mode_missing_artifacts_reduce_score_and_confidence(tmp_path):
    report = SystemEvaluator(nonfunctional_dir=tmp_path).evaluate(mode="dev")

    assert report.release_decision == "PASS_WITH_WARNINGS"
    assert report.overall_score < 100
    assert report.confidence < 0.95