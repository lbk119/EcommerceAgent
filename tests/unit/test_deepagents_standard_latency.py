from __future__ import annotations

import statistics
import time

from agent.subagent.config import get_deepagents_profile
from agent.subagent.subagents import get_subagent_specs


def test_standard_profile_latency_mock_p90_under_30s() -> None:
    samples = []
    for _ in range(12):
        started = time.perf_counter()
        profile = get_deepagents_profile("standard")
        specs = get_subagent_specs("standard")
        assert profile.max_runtime_seconds <= 60
        assert len(specs) >= 7
        samples.append(time.perf_counter() - started)

    p90 = statistics.quantiles(samples, n=10)[8]

    assert p90 < 30