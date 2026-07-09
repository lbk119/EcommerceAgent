# Non-Functional Testing Plan

## Test Layers

1. Unit and contract tests: Python uses pytest and pytest-asyncio for Planner, deepagents-native subagent delegation, tool schema validation, runtime guard orchestration, memory, security, critic, and schema contracts. Go gateway tests use go test with focused middleware, JWT, tenant, Casbin, proxy, and health checks.
2. End-to-end smoke tests: pytest wrappers cover register/login, onboarding, sample import, AI Chat, Planner, deepagents-native execution, critic/memory, report/job persistence, and timeline ordering. PowerShell smoke scripts remain local entry points.
3. Quality evaluation: mock DeepEval-style tests score planner compliance, role boundary, groundedness, actionability, and hallucination risk. Live DeepEval/Giskard judging is opt-in with `RUN_LIVE_LLM_EVALS=1`.
4. Security automation: pytest red-team cases cover prompt injection, data leakage, cross-tenant memory leakage, and tool overreach. promptfoo, Giskard, and garak directories are placeholders for external runners.
5. Performance and chaos: Locust models authenticated workflows and AI Chat polling. k6 covers gateway HTTP smoke. Chaos Toolkit experiment samples describe LLM timeout, Redis/NATS outage, MySQL slowness, and Brain API 5xx cases.

## Tool Choices

- `pytest` and `pytest-asyncio`: native fit for the Python Agent core and async runtime.
- `respx` and `monkeypatch`: mock HTTP/model boundaries without real LLM calls.
- `go test`: keeps gateway contracts in Go, where JWT, tenant context, proxying, and Casbin live.
- `DeepEval` style metrics: suitable for component and agent-run scoring; initial implementation is mock-first for CI.
- `Giskard`, `promptfoo`, and `garak`: reserved for richer agentic checks, declarative prompt suites, and weekly adversarial probes.
- `Locust`: Python user journeys match register/login/onboarding/import/chat/job workflows.
- `k6`: lean high-throughput HTTP gateway checks.
- `Chaos Toolkit`: declarative experiments can run in CI/CD once service orchestration is available.

## Local Commands

```powershell
python -m pip install -r requirements.txt
python -m pytest tests/unit tests/contract -q
python -m pytest tests/evals -m evals -q
python -m pytest tests/security -m security -q
python -m pytest tests/e2e -m e2e -q
go test ./gateway/internal/...
locust -f tests/perf/locustfile.py --host $env:GATEWAY_URL
k6 run tests/perf/k6_gateway.js
```

E2E tests default to `http://127.0.0.1:9090` and skip if the gateway is not running.

## CI Groups

- `unit-python`: unit and contract pytest suite.
- `unit-go`: gateway Go tests.
- `e2e-smoke`: manual workflow dispatch against a configured `GATEWAY_URL`.
- `evals`: manual or release gate; mock evals run without `OPENAI_API_KEY`.
- `security-redteam`: nightly or manual local red-team tests.
- `perf-chaos`: nightly or release-gate environment placeholder for Locust/k6/Chaos Toolkit.

## Acceptance Thresholds

- Planner compliance >= 95%.
- Realtime business-task misroute rate = 0.
- deepagents-native assignment, subagent/tool policy validity, and grounded analysis validation = 100%.
- Cross-tenant/shop memory leakage = 0.
- Prompt injection success rate <= 5%.
- Data leakage findings = 0.
- AI Chat acceptance P95 < 1000 ms.
- Standard job completion P95 < 15 s on local sample data.
- Critical step failure recovery >= 90%.
- Average tool calls and token cost must not regress more than 20% from baseline.

## Adding An Eval Case

Add a new item to `tests/evals/datasets/ecommerce_agent_cases.yaml` with `id`, `task`, and either `expected_intent` or `expected_control`. Run:

```powershell
python -m pytest tests/evals/test_deepeval_quality.py -q
```

The mock report is written to `output/evals/mock_quality_report.json`.

## External Eval Integration

- DeepEval: replace the mock runner in `tests/evals/test_deepeval_quality.py` with GEval metrics once live judging is enabled.
- Giskard: add scenario checks under `tests/security/giskard` and run them only when the dependency and model credentials are configured.
- promptfoo: expand `tests/security/promptfoo/promptfooconfig.yaml` for declarative prompt and red-team suites.
- garak: mount the gateway or agent runner as a target and schedule weekly probes for leakage, prompt injection, jailbreak, and hallucination.

## Current Risks

- Go gateway coverage still needs explicit JWT middleware, tenant/shop injection, proxy timeout, and health tests beyond existing Casbin checks.
- E2E tests depend on a running local stack and sample data import; CI currently skips them unless manually enabled.
- Mock eval thresholds are intentionally lower than final acceptance until Planner coverage is tuned against the 30-case dataset.
- Chaos experiments are templates until Docker Compose or a controlled staging environment can inject failures.
