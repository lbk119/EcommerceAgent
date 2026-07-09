from __future__ import annotations

from agent.subagent.config import get_deepagents_profile
from agent.subagent.runtime import main_system_prompt
from agent.subagent.subagents import build_deepagents_subagents, get_subagent_specs


def test_realtime_deepagents_profile_is_toolless_main_agent() -> None:
    profile = get_deepagents_profile("realtime")

    assert profile.subagents == ()
    assert profile.max_tool_calls == 0
    assert profile.max_subagent_calls == 0
    assert not profile.allow_business_tools
    assert not profile.allow_filesystem
    assert build_deepagents_subagents("realtime") == []
    assert get_subagent_specs("realtime") == []


def test_realtime_system_prompt_explains_background_job_boundary() -> None:
    prompt = main_system_prompt("realtime")

    assert "no tools and no subagents" in prompt
    assert "background standard job" in prompt
    assert "Do not pretend you executed business analysis" in prompt
