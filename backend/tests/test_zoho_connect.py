import datetime
import re
from urllib.parse import parse_qs, urlparse

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

import main
from models import OAuthState
from zoho_oauth import SCOPES, STATE_TTL, authorization_url, create_state

HEADERS = {"X-Access-Key": main.settings.access_key.get_secret_value()}
client = TestClient(main.app, headers=HEADERS)

REDIRECT = "http://localhost:8000/integrations/zoho/callback"


def zoho_settings(**changes):
    values = dict(
        zoho_client_id="1000.TESTCLIENTID",
        zoho_client_secret=SecretStr("the-client-secret"),
        zoho_redirect_uri=REDIRECT,
        zoho_accounts_url="https://accounts.zoho.com",
        token_encryption_key=SecretStr(Fernet.generate_key().decode()),
    )
    return main.settings.model_copy(update={**values, **changes})


@pytest.fixture
def configured(monkeypatch):
    settings = zoho_settings()
    monkeypatch.setattr(main, "settings", settings)
    return settings


def connect(**headers):
    return client.post("/integrations/zoho/connect", headers=headers)


def parsed(url: str):
    parts = urlparse(url)
    return parts, {k: v[0] for k, v in parse_qs(parts.query).items()}


def utc(value: datetime.datetime) -> datetime.datetime:
    return value.replace(tzinfo=datetime.UTC) if value.tzinfo is None else value


def all_states(database) -> list[OAuthState]:
    with database() as session:
        return list(session.scalars(select(OAuthState)))


def test_it_returns_the_zoho_address_to_send_the_browser_to(configured):
    r = connect()
    assert r.status_code == 200
    parts, _ = parsed(r.json()["authorization_url"])
    assert (parts.scheme, parts.netloc, parts.path) == ("https", "accounts.zoho.com", "/oauth/v2/auth")
    assert r.json()["expires_in"] == 600


def test_the_address_carries_the_right_parameters(configured):
    _, query = parsed(connect().json()["authorization_url"])
    assert query["client_id"] == "1000.TESTCLIENTID"
    assert query["response_type"] == "code"
    assert query["access_type"] == "offline"
    assert query["redirect_uri"] == REDIRECT
    assert query["prompt"] == "consent"
    assert set(query) == {"scope", "client_id", "response_type", "access_type", "redirect_uri", "state", "prompt"}


def test_it_asks_for_the_books_scopes_it_needs(configured):
    _, query = parsed(connect().json()["authorization_url"])
    assert query["scope"].split(",") == SCOPES
    assert {"ZohoBooks.bills.CREATE", "ZohoBooks.contacts.READ", "ZohoBooks.settings.READ", "ZohoBooks.accountants.READ"} <= set(SCOPES)


def test_the_state_in_the_address_is_the_one_stored(database, configured):
    _, query = parsed(connect().json()["authorization_url"])
    stored = all_states(database)
    assert [s.state for s in stored] == [query["state"]]


def test_the_stored_state_expires_in_ten_minutes_and_is_unused(database, configured):
    before = datetime.datetime.now(datetime.UTC)
    connect()
    after = datetime.datetime.now(datetime.UTC)
    row = all_states(database)[0]
    assert before + STATE_TTL <= utc(row.expires_at) <= after + STATE_TTL
    assert row.used_at is None


def test_the_state_remembers_who_started_it(database, configured):
    connect(**{"X-Reviewer": "Herman"})
    assert all_states(database)[0].created_by == "Herman"


def test_the_reviewer_defaults_when_no_name_is_sent(database, configured):
    connect()
    assert all_states(database)[0].created_by == "reviewer"


def test_every_call_makes_a_new_unguessable_state(database, configured):
    states = {parsed(connect().json()["authorization_url"])[1]["state"] for _ in range(5)}
    assert len(states) == 5
    for state in states:
        assert len(state) >= 40
        assert re.fullmatch(r"[A-Za-z0-9_-]+", state)
    assert len(all_states(database)) == 5


def test_the_client_secret_is_never_in_the_address(configured):
    url = connect().json()["authorization_url"]
    assert "the-client-secret" not in url


def test_the_accounts_address_follows_the_data_centre(monkeypatch):
    monkeypatch.setattr(main, "settings", zoho_settings(zoho_accounts_url="https://accounts.zoho.eu"))
    parts, _ = parsed(connect().json()["authorization_url"])
    assert parts.netloc == "accounts.zoho.eu"


def test_an_unusual_redirect_address_is_encoded_and_read_back_exactly(monkeypatch):
    awkward = "https://api.example.com/integrations/zoho/callback?x=1&y=a b"
    monkeypatch.setattr(main, "settings", zoho_settings(zoho_redirect_uri=awkward))
    _, query = parsed(connect().json()["authorization_url"])
    assert query["redirect_uri"] == awkward


def test_without_zoho_settings_it_says_so_and_stores_nothing(database, monkeypatch):
    monkeypatch.setattr(main, "settings", zoho_settings(zoho_client_id=""))
    r = connect()
    assert r.status_code == 503
    assert "not set up" in r.json()["detail"]
    assert all_states(database) == []


def test_without_the_access_key_it_is_refused(database, configured):
    r = TestClient(main.app).post("/integrations/zoho/connect")
    assert r.status_code == 401
    assert all_states(database) == []


def test_it_only_accepts_post(configured):
    assert client.get("/integrations/zoho/connect").status_code == 405


def test_a_database_failure_is_a_503(configured, monkeypatch):
    def broken(*args, **kwargs):
        raise OperationalError("insert", {}, Exception("connection lost"))

    monkeypatch.setattr(main, "create_state", broken)
    r = connect()
    assert r.status_code == 503
    assert "database" in r.json()["detail"].lower()


def test_expired_states_are_cleaned_up_when_a_new_one_is_made(database, configured):
    now = datetime.datetime.now(datetime.UTC)
    with database() as session:
        session.add(OAuthState(state="old-expired", created_by="x", expires_at=now - datetime.timedelta(minutes=1)))
        session.add(OAuthState(state="still-valid", created_by="x", expires_at=now + datetime.timedelta(minutes=5)))
        session.commit()

    connect()

    names = {s.state for s in all_states(database)}
    assert "old-expired" not in names
    assert "still-valid" in names
    assert len(names) == 2


def test_create_state_uses_the_time_it_is_given(database):
    moment = datetime.datetime(2026, 11, 1, 9, 0, tzinfo=datetime.UTC)
    with database() as session:
        state = create_state(session, "Herman", now=moment)
        session.commit()
    row = all_states(database)[0]
    assert row.state == state
    assert utc(row.expires_at) == moment + datetime.timedelta(minutes=10)


def test_authorization_url_is_built_from_the_settings_alone():
    url = authorization_url(zoho_settings(), "abc")
    _, query = parsed(url)
    assert query["state"] == "abc"
    assert url.startswith("https://accounts.zoho.com/oauth/v2/auth?")


def test_the_scopes_are_the_ones_the_plan_names():
    assert SCOPES == [
        "ZohoBooks.bills.CREATE",
        "ZohoBooks.bills.READ",
        "ZohoBooks.contacts.CREATE",
        "ZohoBooks.contacts.READ",
        "ZohoBooks.settings.READ",
        "ZohoBooks.accountants.READ",
    ]