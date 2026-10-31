import datetime

import httpx
import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr
from sqlalchemy import text

import main
import zoho_tokens
from crypto import TokenCipher
from zoho_oauth import ZohoError
from zoho_store import get_tokens, save_tokens
from zoho_tokens import REFRESH_MARGIN, ZohoNotConnected, get_access_token

KEY = Fernet.generate_key().decode()
CIPHER = TokenCipher(KEY)
NOW = datetime.datetime(2026, 11, 1, 12, 0, tzinfo=datetime.UTC)

SETTINGS = main.settings.model_copy(
    update=dict(
        zoho_client_id="1000.TESTCLIENTID",
        zoho_client_secret=SecretStr("the-client-secret"),
        zoho_redirect_uri="http://localhost:8000/integrations/zoho/callback",
        zoho_accounts_url="https://accounts.zoho.com",
        token_encryption_key=SecretStr(KEY),
    )
)

REFRESHED = {
    "access_token": "1000.new-access",
    "scope": "ZohoBooks.bills.CREATE ZohoBooks.settings.READ",
    "api_domain": "https://www.zohoapis.com",
    "token_type": "Bearer",
    "expires_in": 3600,
}


class Zoho:
    """A pretend Zoho that answers token requests and counts them."""

    def __init__(self, status: int = 200, body=None, *, content: bytes | None = None, error: Exception | None = None):
        self.status, self.body, self.content, self.error = status, body, content, error
        self.requests: list[httpx.Request] = []
        self.client = httpx.Client(transport=httpx.MockTransport(self._handle))

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.error:
            raise self.error
        if self.content is not None:
            return httpx.Response(self.status, content=self.content)
        return httpx.Response(self.status, json=self.body if self.body is not None else REFRESHED)


def connect(database, *, expires_in: datetime.timedelta, **overrides):
    values = dict(
        access_token="1000.old-access",
        refresh_token="1000.the-refresh",
        expires_at=NOW + expires_in,
        scope="ZohoBooks.bills.CREATE",
        api_domain="https://www.zohoapis.com",
        connected_by="Herman",
    )
    with database() as session:
        save_tokens(session, CIPHER, **{**values, **overrides})
        session.commit()


def token(database, zoho: Zoho | None = None, *, now: datetime.datetime = NOW) -> str:
    with database() as session:
        return get_access_token(session, SETTINGS, now=now, http=(zoho or Zoho()).client)


def stored(database):
    with database() as session:
        return get_tokens(session, CIPHER)


def test_a_token_with_plenty_of_time_left_is_used_without_calling_zoho(database):
    connect(database, expires_in=datetime.timedelta(minutes=30))
    zoho = Zoho()
    assert token(database, zoho) == "1000.old-access"
    assert zoho.requests == []


def test_the_margin_is_one_minute():
    assert REFRESH_MARGIN == datetime.timedelta(seconds=60)


def test_a_token_just_outside_the_margin_is_still_used(database):
    connect(database, expires_in=datetime.timedelta(seconds=61))
    zoho = Zoho()
    assert token(database, zoho) == "1000.old-access"
    assert zoho.requests == []


def test_a_token_inside_the_margin_is_refreshed(database):
    connect(database, expires_in=datetime.timedelta(seconds=59))
    zoho = Zoho()
    assert token(database, zoho) == "1000.new-access"
    assert len(zoho.requests) == 1


def test_a_token_exactly_at_the_margin_is_refreshed(database):
    connect(database, expires_in=REFRESH_MARGIN)
    assert token(database, Zoho()) == "1000.new-access"


def test_a_forced_expired_token_refreshes_and_the_new_one_is_stored(database):
    connect(database, expires_in=-datetime.timedelta(hours=3))
    zoho = Zoho()

    assert token(database, zoho) == "1000.new-access"

    saved = stored(database)
    assert saved.access_token == "1000.new-access"
    assert saved.expires_at == NOW + datetime.timedelta(hours=1)
    assert len(zoho.requests) == 1


def test_the_refresh_request_uses_the_stored_refresh_token(database):
    connect(database, expires_in=-datetime.timedelta(hours=1))
    zoho = Zoho()
    token(database, zoho)
    body = zoho.requests[0].content.decode()
    assert "grant_type=refresh_token" in body
    assert "refresh_token=1000.the-refresh" in body


def test_the_refresh_token_and_who_connected_are_kept(database):
    connect(database, expires_in=-datetime.timedelta(hours=1))
    before = stored(database)
    token(database)
    after = stored(database)
    assert after.refresh_token == "1000.the-refresh"
    assert after.connected_by == "Herman"
    assert after.connected_at == before.connected_at


def test_a_rotated_refresh_token_replaces_the_old_one(database):
    connect(database, expires_in=-datetime.timedelta(hours=1))
    token(database, Zoho(body={**REFRESHED, "refresh_token": "1000.rotated"}))
    assert stored(database).refresh_token == "1000.rotated"


def test_scope_and_domain_are_kept_when_zoho_does_not_repeat_them(database):
    connect(database, expires_in=-datetime.timedelta(hours=1))
    token(database, Zoho(body={"access_token": "1000.new-access", "expires_in": 3600}))
    saved = stored(database)
    assert saved.scope == "ZohoBooks.bills.CREATE"
    assert saved.api_domain == "https://www.zohoapis.com"


def test_scope_and_domain_follow_what_zoho_sends(database):
    connect(database, expires_in=-datetime.timedelta(hours=1))
    token(database)
    saved = stored(database)
    assert saved.scope == "ZohoBooks.bills.CREATE ZohoBooks.settings.READ"


def test_the_new_token_is_encrypted_at_rest(database):
    connect(database, expires_in=-datetime.timedelta(hours=1))
    token(database)
    with database() as session:
        raw = session.execute(text("SELECT access_token_enc FROM zoho_connection")).scalar()
    assert "new-access" not in raw
    assert CIPHER.decrypt(raw) == "1000.new-access"


def test_a_second_call_uses_the_refreshed_token_without_asking_zoho_again(database):
    connect(database, expires_in=-datetime.timedelta(hours=1))
    zoho = Zoho()
    assert token(database, zoho) == "1000.new-access"
    assert token(database, zoho) == "1000.new-access"
    assert token(database, zoho, now=NOW + datetime.timedelta(minutes=30)) == "1000.new-access"
    assert len(zoho.requests) == 1


def test_after_the_hour_it_refreshes_again(database):
    connect(database, expires_in=-datetime.timedelta(hours=1))
    zoho = Zoho()
    token(database, zoho)
    assert token(database, zoho, now=NOW + datetime.timedelta(minutes=59, seconds=30)) == "1000.new-access"
    assert len(zoho.requests) == 2


def test_nothing_connected_means_not_connected_and_no_call_to_zoho(database):
    zoho = Zoho()
    with pytest.raises(ZohoNotConnected, match="not connected"):
        token(database, zoho)
    assert zoho.requests == []


@pytest.mark.parametrize("code", ["invalid_code", "invalid_grant", "invalid_token"])
def test_a_rejected_refresh_token_disconnects(database, code):
    connect(database, expires_in=-datetime.timedelta(hours=1))
    zoho = Zoho(400, {"error": code})

    with pytest.raises(ZohoNotConnected, match="Connect Zoho again"):
        token(database, zoho)

    assert stored(database) is None
    with database() as session:
        assert session.execute(text("SELECT COUNT(*) FROM zoho_connection")).scalar() == 0


def test_once_disconnected_it_stays_disconnected_without_calling_zoho(database):
    connect(database, expires_in=-datetime.timedelta(hours=1))
    with pytest.raises(ZohoNotConnected):
        token(database, Zoho(200, {"error": "invalid_code"}))
    zoho = Zoho()
    with pytest.raises(ZohoNotConnected, match="not connected"):
        token(database, zoho)
    assert zoho.requests == []


def test_connecting_again_after_a_rejection_works(database):
    connect(database, expires_in=-datetime.timedelta(hours=1))
    with pytest.raises(ZohoNotConnected):
        token(database, Zoho(200, {"error": "invalid_code"}))
    connect(database, expires_in=datetime.timedelta(hours=1), access_token="1000.fresh-start")
    assert token(database) == "1000.fresh-start"


@pytest.mark.parametrize(
    "zoho",
    [
        Zoho(200, {"error": "Access Denied", "status": "failure"}),
        Zoho(401, {"error": "invalid_client"}),
        Zoho(503, content=b"<html>down</html>"),
        Zoho(error=httpx.ConnectError("no route")),
        Zoho(error=httpx.ReadTimeout("slow")),
    ],
    ids=["rate-limited", "bad-client", "server-down", "unreachable", "timeout"],
)
def test_a_temporary_failure_keeps_the_connection_and_raises(database, zoho):
    connect(database, expires_in=-datetime.timedelta(hours=1))
    before = stored(database)

    with pytest.raises(ZohoError):
        token(database, zoho)

    after = stored(database)
    assert after is not None
    assert after.access_token == before.access_token
    assert after.refresh_token == before.refresh_token
    assert after.expires_at == before.expires_at


def test_after_a_temporary_failure_the_next_call_tries_again(database):
    connect(database, expires_in=-datetime.timedelta(hours=1))
    with pytest.raises(ZohoError):
        token(database, Zoho(503, content=b"down"))
    assert token(database, Zoho()) == "1000.new-access"


def test_a_connection_made_with_another_key_reads_as_not_connected_and_is_left_alone(database):
    other = TokenCipher(Fernet.generate_key().decode())
    with database() as session:
        save_tokens(
            session, other, access_token="a", refresh_token="r", expires_at=NOW + datetime.timedelta(hours=1),
            scope=None, api_domain=None, connected_by="Herman",
        )
        session.commit()
    zoho = Zoho()

    with pytest.raises(ZohoNotConnected, match="could not be read"):
        token(database, zoho)

    assert zoho.requests == []
    with database() as session:
        assert session.execute(text("SELECT COUNT(*) FROM zoho_connection")).scalar() == 1


def test_if_another_request_refreshed_first_this_one_does_not_refresh_again(database, monkeypatch):
    connect(database, expires_in=-datetime.timedelta(hours=1))
    real = zoho_tokens.get_tokens
    calls = []

    def racing(session, cipher, *, lock=False):
        calls.append(lock)
        if lock and len(calls) == 2:
            with database() as other:
                save_tokens(
                    other, cipher, access_token="1000.refreshed-elsewhere", refresh_token="1000.the-refresh",
                    expires_at=NOW + datetime.timedelta(hours=1), scope=None, api_domain=None, connected_by="Herman",
                )
                other.commit()
        return real(session, cipher, lock=lock)

    monkeypatch.setattr(zoho_tokens, "get_tokens", racing)
    zoho = Zoho()

    assert token(database, zoho) == "1000.refreshed-elsewhere"
    assert zoho.requests == []


def test_the_quick_path_takes_no_lock_and_the_refresh_path_does(database, monkeypatch):
    real = zoho_tokens.get_tokens
    calls = []

    def spy(session, cipher, *, lock=False):
        calls.append(lock)
        return real(session, cipher, lock=lock)

    monkeypatch.setattr(zoho_tokens, "get_tokens", spy)

    connect(database, expires_in=datetime.timedelta(hours=1))
    token(database)
    assert calls == [False]

    calls.clear()
    connect(database, expires_in=-datetime.timedelta(hours=1))
    token(database)
    assert calls == [False, True]


def test_the_default_time_is_the_real_clock(database):
    with database() as session:
        save_tokens(
            session, CIPHER, access_token="1000.old-access", refresh_token="1000.the-refresh",
            expires_at=datetime.datetime.now(datetime.UTC) + datetime.timedelta(minutes=30),
            scope=None, api_domain=None, connected_by="Herman",
        )
        session.commit()
    with database() as session:
        assert get_access_token(session, SETTINGS, http=Zoho().client) == "1000.old-access"


def test_it_needs_the_encryption_key(database):
    no_key = SETTINGS.model_copy(update=dict(token_encryption_key=SecretStr("")))
    with database() as session:
        with pytest.raises(RuntimeError, match="TOKEN_ENCRYPTION_KEY"):
            get_access_token(session, no_key, now=NOW)