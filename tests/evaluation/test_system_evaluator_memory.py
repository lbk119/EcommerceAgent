from agent.evaluation.system_evaluator import SystemEvaluator


def test_memory_isolation_requires_scoped_namespace_and_production_store_gate(tmp_path):
    report = SystemEvaluator(nonfunctional_dir=tmp_path).evaluate(mode="dev")

    dimension = report.dimensions["memory_isolation"]
    assert dimension.status == "pass"
    messages = [item.message for item in dimension.findings]
    assert messages == ["All 2 deterministic checks passed."]