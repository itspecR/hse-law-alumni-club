import pytest

from club_web import features


@pytest.fixture
def changes_section(monkeypatch):
    monkeypatch.setattr(features, "CHANGES_ENABLED", True)
