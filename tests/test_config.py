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


def test_values_can_come_from_secret_files(tmp_path, monkeypatch):
    monkeypatch.delenv("GOVWATCH_CONGRESS_API_KEY", raising=False)
    monkeypatch.delenv("GOVWATCH_REGULATIONS_API_KEY", raising=False)
    (tmp_path / "GOVWATCH_CONGRESS_API_KEY").write_text("from-file")
    (tmp_path / "GOVWATCH_REGULATIONS_API_KEY").write_text("regs-from-file")
    (tmp_path / "GOVWATCH_DATABASE_URL").write_text("postgresql://u:secretpw@db/govwatch")

    s = Settings(_env_file=None, _secrets_dir=tmp_path)

    assert s.congress_api_key.get_secret_value() == "from-file"
    assert s.database_url == "postgresql://u:secretpw@db/govwatch"
    # the db password shouldn't show up if settings get logged
    assert "secretpw" not in repr(s)


def test_get_settings_reads_secrets_dir_from_env(tmp_path, monkeypatch):
    from govwatch.config import get_settings

    monkeypatch.delenv("GOVWATCH_CONGRESS_API_KEY", raising=False)
    monkeypatch.delenv("GOVWATCH_REGULATIONS_API_KEY", raising=False)
    for name in ("GOVWATCH_CONGRESS_API_KEY", "GOVWATCH_REGULATIONS_API_KEY"):
        (tmp_path / name).write_text("k")
    monkeypatch.setenv("GOVWATCH_SECRETS_DIR", str(tmp_path))
    # a developer's .env in the working directory would take priority over the files
    monkeypatch.chdir(tmp_path)
    get_settings.cache_clear()
    try:
        assert get_settings().congress_api_key.get_secret_value() == "k"
    finally:
        get_settings.cache_clear()
