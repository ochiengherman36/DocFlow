from pathlib import Path

import pytest
from pydantic import ValidationError

from config import BASE_DIR, Settings

REQUIRED = {
    "anthropic_api_key": "k",
    "anthropic_model": "m",
    "frontend_origin": "http://localhost:3000",
    "access_key": "a-long-enough-test-key",
    "database_url": "sqlite://",
}


def make(**extra) -> Settings:
    return Settings(_env_file=None, **{**REQUIRED, **extra})


def test_defaults(monkeypatch):
    monkeypatch.delenv("MAX_INVOICE_AGE_DAYS", raising=False)
    s = make()
    assert s.max_invoice_age_days == 365
    assert s.validation_tolerance == 0.01


@pytest.mark.parametrize("name, value", [("max_invoice_age_days", 0), ("max_upload_mb", 0), ("validation_tolerance", -0.5)])
def test_bad_values_are_rejected(name, value):
    with pytest.raises(ValidationError):
        make(**{name: value})


def test_zero_tolerance_is_allowed():
    assert make(validation_tolerance=0).validation_tolerance == 0


def test_access_key_is_required(monkeypatch):
    monkeypatch.delenv("ACCESS_KEY", raising=False)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, anthropic_api_key="k", anthropic_model="m", frontend_origin="http://localhost:3000", database_url="sqlite://")


@pytest.mark.parametrize("key", ["", "   ", "short", "x" * 15])
def test_short_or_blank_access_key_is_rejected(key):
    with pytest.raises(ValidationError, match="at least 16"):
        make(access_key=key)


def test_access_key_of_16_characters_is_accepted():
    assert make(access_key="x" * 16).access_key.get_secret_value() == "x" * 16


def test_access_key_is_not_shown_when_settings_are_printed():
    assert "a-long-enough-test-key" not in repr(make())


def test_api_docs_are_off_by_default(monkeypatch):
    monkeypatch.delenv("ENABLE_API_DOCS", raising=False)
    assert make().enable_api_docs is False


def test_database_url_is_required(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(ValidationError, match="database_url"):
        Settings(
            _env_file=None,
            anthropic_api_key="k",
            anthropic_model="m",
            frontend_origin="http://localhost:3000",
            access_key="a-long-enough-test-key",
        )


@pytest.mark.parametrize(
    "given",
    [
        "postgresql://user:pass@ep-cool-name.eu-central-1.aws.neon.tech/neondb?sslmode=require",
        "postgres://user:pass@ep-cool-name.eu-central-1.aws.neon.tech/neondb?sslmode=require",
        "postgresql+psycopg://user:pass@ep-cool-name.eu-central-1.aws.neon.tech/neondb?sslmode=require",
        "  postgresql://user:pass@ep-cool-name.eu-central-1.aws.neon.tech/neondb?sslmode=require  ",
    ],
)
def test_neon_style_addresses_are_accepted_and_use_the_psycopg_driver(given):
    url = make(database_url=given).database_url.get_secret_value()
    assert url == "postgresql+psycopg://user:pass@ep-cool-name.eu-central-1.aws.neon.tech/neondb?sslmode=require"


@pytest.mark.parametrize("bad", ["", "   ", "mysql://u:p@host/db", "localhost", "https://example.com", "file.db"])
def test_other_database_addresses_are_rejected(bad):
    with pytest.raises(ValidationError, match="PostgreSQL address"):
        make(database_url=bad)


def test_database_password_is_not_shown_when_settings_are_printed():
    s = make(database_url="postgresql://user:s3cret-pass@host/db")
    assert "s3cret-pass" not in repr(s)
    assert "s3cret-pass" not in str(s)