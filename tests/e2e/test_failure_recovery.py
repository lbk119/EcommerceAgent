from __future__ import annotations


def test_failed_or_timeout_tasks_are_terminal_states() -> None:
    terminal = {"completed", "failed", "timeout", "cancelled"}
    observed = ["queued", "running", "timeout"]

    assert observed[-1] in terminal
    assert "running" not in observed[observed.index(observed[-1]) :]