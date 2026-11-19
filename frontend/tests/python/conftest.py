import pytest

from club_web import features, pages


@pytest.fixture
def changes_section(monkeypatch):
    monkeypatch.setattr(features, "CHANGES_ENABLED", True)


@pytest.fixture
def home_read_section(monkeypatch):
    monkeypatch.setattr(pages, "HOME_HIDDEN_BY_DEFAULT", frozenset())


@pytest.fixture
def push_notifications(monkeypatch):
    monkeypatch.setattr(features, "PUSH_ENABLED", True)


@pytest.fixture
def mini_app(monkeypatch):
    monkeypatch.setattr(features, "MINI_APP_ENABLED", True)


@pytest.fixture
def dpo(monkeypatch):
    monkeypatch.setattr(features, "DPO_ENABLED", True)
