from __future__ import annotations

import pytest

from agent.security.redaction import redact_secrets


@pytest.mark.security
def test_secrets_are_not_preserved_in_test_outputs() -> None:
    text = "OPENAI_API_KEY=sk-123456789012345678901234 mysql://root:pass@db/app Bearer abc.def"

    safe = redact_secrets(text)

    assert "sk-123" not in safe
    assert "root:pass" not in safe
    assert "abc.def" not in safe