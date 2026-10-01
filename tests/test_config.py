import pytest
from pydantic import ValidationError

from govwatch.config import Settings


@pytest.fixture
def keys(monkeypatch):
    monkeypatch.setenv("GOVWATCH_CONGRESS_API_KEY", "congress-secret")
    monkeypatch.setenv("GOVWATCH_REGULATIONS_API_KEY", "regs-secret")


def test_defaults(keys):
    s = Settings(_env_file=None)
    assert s.poll_interval_seconds == 900
    assert s.backfill_days == 7
    assert s.congress_api_key.get_secret_value() == "congress-secret"


def test_keys_not_leaked_in_repr(keys):
    s = Settings(_env_file=None)
    assert "congress-secret" not in repr(s)
    assert "regs-secret" not in repr(s)


def test_missing_keys_fails(monkeypatch):
    monkeypatch.delenv("GOVWATCH_CONGRESS_API_KEY", raising=False)
    monkeypatch.delenv("GOVWATCH_REGULATIONS_API_KEY", raising=False)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_poll_interval_floor(keys, monkeypatch):
    monkeypatch.setenv("GOVWATCH_POLL_INTERVAL_SECONDS", "5")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)
