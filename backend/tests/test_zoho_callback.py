import datetime
from urllib.parse import parse_qs, urlparse

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError

import main
from crypto import TokenCipher
from models import OAuthState, ZohoConnection
from zoho_oauth import TokenResponse, ZohoError
from zoho_store import get_tokens

HEADERS = {"X-Access-Key": main.settings.access_key.get_secret_value()}
authorised = TestClient(main.app, headers=HEADERS)
browser = TestClient(main.app, follow_redirects=False)

REDIRECT = "http://localhost:8000/integrations/zoho/callback"
ORIGIN = main.settings.frontend_origin
KEY = Fernet.generate_key().decode()
CIPHER = TokenCipher(KEY)

GOOD = TokenResponse(
    access_token="1000.access-abc.xyz",
    refresh_token="1000.refresh-def.xyz",
    expires_in=3600,
    scope="ZohoBooks.bills.CREATE ZohoBooks.settings.READ",
    api_domain="https://www.zohoapis.com",
)


@pytest.fixture
def configured(monkeypatch):
    settings = main.settings.model_copy(
        update=dict(
            zoho_client_id="1000.TESTCLIENTID",
            zoho_client_secret=SecretStr("the-client-secret"),
            zoho_redirect_uri=REDIRECT,
            zoho_accounts_url="https://accounts.zoho.com",
            token_encryption_key=SecretStr(KEY),
        )
    )
    monkeypatch.setattr(main, "settings", settings)
    return settings


@pytest.fixture
def exchange(monkeypatch):
    calls = []

    def fake(settings, code, **kwargs):
        calls.append(code)
        return GOOD

    monkeypatch.setattr(main, "exchange_code", fake)
    return calls


def start(reviewer: str | None = None) -> str:
    headers = {"X-Reviewer": reviewer} if reviewer else {}
    url = authorised.post("/integrations/zoho/connect", headers=headers).json()["authorization_url"]
    return parse_qs(urlparse(url).query)["state"][0]


def callback(**params):
    return browser.get("/integrations/zoho/callback", params=params)


def outcome(response) -> dict:
    assert response.status_code == 303
    location = response.headers["location"]
    assert location.startswith(f"{ORIGIN}/?")
    return {k: v[0] for k, v in parse_qs(urlparse(location).query).items()}


def stored_tokens(database):
    with database() as session:
        return get_tokens(session, CIPHER)


def connection_rows(database) -> int:
    with database() as session:
        return session.execute(text("SELECT COUNT(*) FROM zoho_connection")).scalar()


def state_row(database, state: str) -> OAuthState:
    with database() as session:
        return session.get(OAuthState, state)


def utc(value: datetime.datetime) -> datetime.datetime:
    return value.replace(tzinfo=datetime.UTC) if value.tzinfo is None else value


def test_a_good_flow_connects_and_sends_the_browser_back(database, configured, exchange):
    state = start()
    r = callback(code="the-code", state=state)
    assert outcome(r) == {"zoho": "connected"}
    assert exchange == ["the-code"]


def test_the_good_flow_stores_the_tokens_readably_through_the_app(database, configured, exchange):
    before = datetime.datetime.now(datetime.UTC)
    callback(code="the-code", state=start("Herman"))
    tokens = stored_tokens(database)

    assert tokens.access_token == GOOD.access_token
    assert tokens.refresh_token == GOOD.refresh_token
    assert tokens.scope == GOOD.scope
    assert tokens.api_domain == GOOD.api_domain
    assert tokens.connected_by == "Herman"
    assert before + datetime.timedelta(seconds=3590) <= tokens.expires_at <= before + datetime.timedelta(seconds=3620)


def test_the_stored_tokens_are_encrypted_at_rest(database, configured, exchange):
    callback(code="the-code", state=start())
    with database() as session:
        row = session.execute(text("SELECT access_token_enc, refresh_token_enc FROM zoho_connection")).one()
    assert "access-abc" not in row.access_token_enc
    assert "refresh-def" not in row.refresh_token_enc


def test_the_state_is_used_up(database, configured, exchange):
    state = start()
    callback(code="the-code", state=state)
    assert state_row(database, state).used_at is not None


def test_the_callback_needs_no_access_key(database, configured, exchange):
    state = start()
    r = TestClient(main.app, follow_redirects=False).get(
        "/integrations/zoho/callback", params={"code": "c", "state": state}
    )
    assert r.status_code == 303


def test_the_browser_is_not_sent_any_token_or_code(database, configured, exchange):
    r = callback(code="the-secret-code", state=start())
    location = r.headers["location"]
    for secret in ["the-secret-code", "access-abc", "refresh-def", "the-client-secret"]:
        assert secret not in location


def test_an_unknown_state_is_rejected_and_nothing_is_stored(database, configured, exchange):
    start()
    r = callback(code="the-code", state="not-a-real-state")
    assert outcome(r) == {"zoho": "error", "reason": "invalid_state"}
    assert exchange == []
    assert connection_rows(database) == 0


def test_a_missing_state_is_rejected(database, configured, exchange):
    r = callback(code="the-code")
    assert outcome(r) == {"zoho": "error", "reason": "invalid_state"}
    assert exchange == []
    assert connection_rows(database) == 0


def test_an_empty_state_is_rejected(database, configured, exchange):
    assert outcome(callback(code="the-code", state=""))["reason"] == "invalid_state"
    assert exchange == []


def test_a_very_long_state_is_rejected_without_a_crash(database, configured, exchange):
    assert outcome(callback(code="the-code", state="x" * 5000))["reason"] == "invalid_state"
    assert exchange == []


def test_an_expired_state_is_rejected_and_nothing_is_stored(database, configured, exchange):
    state = start()
    with database() as session:
        row = session.get(OAuthState, state)
        row.expires_at = datetime.datetime.now(datetime.UTC) - datetime.timedelta(seconds=1)
        session.commit()

    r = callback(code="the-code", state=state)

    assert outcome(r) == {"zoho": "error", "reason": "invalid_state"}
    assert exchange == []
    assert connection_rows(database) == 0


def test_a_reused_state_is_rejected(database, configured, exchange):
    state = start()
    assert outcome(callback(code="first", state=state)) == {"zoho": "connected"}

    second = callback(code="second", state=state)

    assert outcome(second) == {"zoho": "error", "reason": "invalid_state"}
    assert exchange == ["first"]


def test_a_reused_state_does_not_disturb_the_first_connection(database, configured, exchange):
    state = start("Herman")
    callback(code="first", state=state)
    callback(code="second", state=state)
    assert stored_tokens(database).connected_by == "Herman"
    assert connection_rows(database) == 1


def test_a_wrong_state_does_not_use_up_a_real_one(database, configured, exchange):
    real = start()
    callback(code="the-code", state="guess")
    assert state_row(database, real).used_at is None
    assert outcome(callback(code="the-code", state=real)) == {"zoho": "connected"}


def test_denying_access_is_handled(database, configured, exchange):
    state = start()
    r = callback(error="access_denied", state=state)
    assert outcome(r) == {"zoho": "error", "reason": "denied"}
    assert exchange == []
    assert connection_rows(database) == 0
    assert state_row(database, state).used_at is not None


def test_a_denial_with_a_bad_state_is_still_just_an_invalid_state(database, configured, exchange):
    assert outcome(callback(error="access_denied", state="nope"))["reason"] == "invalid_state"


def test_a_missing_code_is_handled(database, configured, exchange):
    r = callback(state=start())
    assert outcome(r) == {"zoho": "error", "reason": "missing_code"}
    assert exchange == []
    assert connection_rows(database) == 0


def test_a_failed_exchange_is_handled_and_nothing_is_stored(database, configured, monkeypatch):
    def refuse(settings, code, **kwargs):
        raise ZohoError("invalid_code")

    monkeypatch.setattr(main, "exchange_code", refuse)
    state = start()

    r = callback(code="bad-code", state=state)

    assert outcome(r) == {"zoho": "error", "reason": "exchange_failed"}
    assert "invalid_code" not in r.headers["location"]
    assert connection_rows(database) == 0
    assert state_row(database, state).used_at is not None


def test_a_failed_exchange_cannot_be_retried_with_the_same_state(database, configured, monkeypatch):
    attempts = []

    def refuse(settings, code, **kwargs):
        attempts.append(code)
        raise ZohoError("invalid_code")

    monkeypatch.setattr(main, "exchange_code", refuse)
    state = start()
    callback(code="one", state=state)
    assert outcome(callback(code="two", state=state))["reason"] == "invalid_state"
    assert attempts == ["one"]


def test_without_zoho_settings_it_fails_cleanly(database, monkeypatch, exchange):
    monkeypatch.setattr(main, "settings", main.settings.model_copy(update=dict(zoho_client_id="")))
    r = callback(code="the-code", state="whatever")
    assert outcome(r) == {"zoho": "error", "reason": "not_configured"}
    assert exchange == []


def test_connecting_again_replaces_the_earlier_connection(database, configured, monkeypatch):
    replies = iter(
        [
            GOOD,
            TokenResponse("1000.access-NEW.xyz", "1000.refresh-NEW.xyz", 3600, None, "https://www.zohoapis.com"),
        ]
    )
    monkeypatch.setattr(main, "exchange_code", lambda settings, code, **kw: next(replies))

    callback(code="one", state=start("Herman"))
    callback(code="two", state=start("Wanjiru"))

    tokens = stored_tokens(database)
    assert connection_rows(database) == 1
    assert (tokens.access_token, tokens.refresh_token, tokens.connected_by) == (
        "1000.access-NEW.xyz",
        "1000.refresh-NEW.xyz",
        "Wanjiru",
    )


def test_a_database_failure_while_checking_the_state_is_a_clean_redirect(configured, monkeypatch, exchange):
    def broken(*args, **kwargs):
        raise OperationalError("select", {}, Exception("connection lost"))

    monkeypatch.setattr(main, "consume_state", broken)
    r = callback(code="the-code", state="whatever")
    assert outcome(r) == {"zoho": "error", "reason": "unavailable"}
    assert exchange == []


def test_a_database_failure_while_saving_is_a_clean_redirect(configured, exchange, monkeypatch):
    def broken(*args, **kwargs):
        raise OperationalError("insert", {}, Exception("connection lost"))

    monkeypatch.setattr(main, "save_tokens", broken)
    r = callback(code="the-code", state=start())
    assert outcome(r) == {"zoho": "error", "reason": "unavailable"}


def test_the_callback_only_accepts_get(configured):
    assert browser.post("/integrations/zoho/callback").status_code in (401, 405)


def test_other_zoho_endpoints_still_need_the_access_key(configured):
    assert TestClient(main.app).post("/integrations/zoho/connect").status_code == 401


def test_the_only_new_public_path_is_the_callback():
    assert main.PUBLIC_PATHS == {"/health", "/integrations/zoho/callback"}


def test_the_failure_is_written_to_the_log_without_secrets(database, configured, monkeypatch, caplog):
    def refuse(settings, code, **kwargs):
        raise ZohoError("invalid_code")

    monkeypatch.setattr(main, "exchange_code", refuse)
    with caplog.at_level("WARNING", logger="docflow"):
        callback(code="the-secret-code", state=start())
    assert "invalid_code" in caplog.text
    assert "the-secret-code" not in caplog.text
    assert "the-client-secret" not in caplog.text


def test_no_row_in_the_connection_table_after_every_rejected_attempt(database, configured, exchange):
    for params in [{}, {"state": "x"}, {"code": "c"}, {"code": "c", "state": "x" * 300}]:
        callback(**params)
    with database() as session:
        assert session.scalars(select(ZohoConnection)).first() is None
    assert exchange == []