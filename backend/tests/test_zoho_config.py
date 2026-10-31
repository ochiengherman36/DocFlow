import pytest
from cryptography.fernet import Fernet
from pydantic import ValidationError

from config import Settings

REQUIRED = {
    "anthropic_api_key": "k",
    "anthropic_model": "m",
    "frontend_origin": "http://localhost:3000",
    "access_key": "a-long-enough-test-key",
    "database_url": "sqlite://",
}
ZOHO_ENV = ["ZOHO_CLIENT_ID", "ZOHO_CLIENT_SECRET", "ZOHO_REDIRECT_URI", "ZOHO_ACCOUNTS_URL", "ZOHO_ORGANIZATION_ID", "TOKEN_ENCRYPTION_KEY"]


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    for name in ZOHO_ENV:
        monkeypatch.delenv(name, raising=False)


def make(**extra) -> Settings:
    return Settings(_env_file=None, **{**REQUIRED, **extra})


def fully_configured(**extra) -> Settings:
    return make(
        zoho_client_id="1000.CLIENT",
        zoho_client_secret="shh",
        zoho_redirect_uri="http://localhost:8000/integrations/zoho/callback",
        token_encryption_key=Fernet.generate_key().decode(),
        **extra,
    )


def test_the_app_starts_without_any_zoho_settings():
    s = make()
    assert s.zoho_configured is False
    assert s.zoho_accounts_url == "https://accounts.zoho.com"


def test_all_the_settings_together_count_as_configured():
    assert fully_configured().zoho_configured is True


@pytest.mark.parametrize(
    "missing",
    ["zoho_client_id", "zoho_client_secret", "zoho_redirect_uri", "token_encryption_key"],
)
def test_one_missing_setting_means_not_configured(missing):
    values = dict(
        zoho_client_id="1000.CLIENT",
        zoho_client_secret="shh",
        zoho_redirect_uri="http://localhost:8000/integrations/zoho/callback",
        token_encryption_key=Fernet.generate_key().decode(),
    )
    values[missing] = ""
    assert make(**values).zoho_configured is False


def test_blank_values_from_an_example_file_count_as_missing(monkeypatch):
    for name in ZOHO_ENV:
        monkeypatch.setenv(name, "")
    s = make()
    assert s.zoho_configured is False
    assert s.zoho_accounts_url == "https://accounts.zoho.com"


def test_the_accounts_address_is_tidied():
    assert make(zoho_accounts_url=" https://accounts.zoho.eu/ ").zoho_accounts_url == "https://accounts.zoho.eu"


def test_the_accounts_address_must_be_https():
    with pytest.raises(ValidationError):
        make(zoho_accounts_url="http://accounts.zoho.com")
    with pytest.raises(ValidationError):
        make(zoho_accounts_url="accounts.zoho.com")


def test_the_redirect_address_must_be_a_web_address():
    with pytest.raises(ValidationError):
        make(zoho_redirect_uri="localhost:8000/callback")
    assert make(zoho_redirect_uri="https://api.example.com/integrations/zoho/callback").zoho_redirect_uri.startswith("https://")


def test_a_made_up_encryption_key_is_refused():
    for bad in ["too-short", "x" * 44, "not a key"]:
        with pytest.raises(ValidationError):
            make(token_encryption_key=bad)


def test_a_real_encryption_key_is_accepted():
    key = Fernet.generate_key().decode()
    assert make(token_encryption_key=key).token_encryption_key.get_secret_value() == key


def test_secrets_do_not_show_in_the_printed_settings():
    s = fully_configured()
    shown = repr(s)
    assert "shh" not in shown
    assert s.token_encryption_key.get_secret_value() not in shown

def test_the_organization_id_is_optional_and_blank_by_default():
    assert make().zoho_organization_id == ""


def test_the_organization_id_is_kept_as_numbers():
    assert make(zoho_organization_id=" 944669917 ").zoho_organization_id == "944669917"


def test_the_organization_id_must_be_numbers_only():
    for bad in ["abc", "9446-69917", "944669917a"]:
        with pytest.raises(ValidationError):
            make(zoho_organization_id=bad)


def test_a_blank_organization_id_from_an_example_file_is_fine(monkeypatch):
    monkeypatch.setenv("ZOHO_ORGANIZATION_ID", "")
    assert make().zoho_organization_id == ""


def test_the_organization_id_does_not_change_whether_connecting_is_possible():
    assert fully_configured(zoho_organization_id="944669917").zoho_configured is True
    assert fully_configured().zoho_configured is True