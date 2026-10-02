import pytest

import transcribe


@pytest.fixture(autouse=True)
def default_summary_languages(monkeypatch):
    """Tests assume the default en,zh notes, whatever SUMMARY_LANGUAGES the
    developer's local .env sets; tests that need other languages override it."""
    monkeypatch.setattr(transcribe, "SUMMARY_LANGUAGES", ["en", "zh"])
