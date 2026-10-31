import datetime

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

import main
from crypto import TokenCipher
from zoho_oauth import ZohoError
from zoho_store import get_tokens, save_tokens

HEADERS = {"X-Access-Key": main.settings.access_key.get_secret_value()}
client = TestClient(main.app, headers=HEADERS)

KEY = Fernet.generate_key().decode()
CIPHER = TokenCipher(KEY)


@pytest.fixture
def configured(monkeypatch):
    settings = main.settings.model_copy(
        update=dict(
            zoho_client_id="1000.TESTCLIENTID",
            zoho_client_secret=SecretStr("the-client-secret"),
            zoho_redirect_uri="http://localhost:8000/integrations/zoho/callback",
            zoho_accounts_url="https://accounts.zoho.com",
            token_encryption_key=SecretStr(KEY),
        )
    )
    monkeypatch.setattr(main, "settings", settings)
    return settings


def connect(database, cipher=CIPHER, **overrides):
    values = dict(
        access_token="1000.access-abc",
        refresh_token="1000.refresh-def",
        expires_at=datetime.datetime.now(datetime.UTC) + datetime.timedelta(hours=1),
        scope="ZohoBooks.bills.CREATE",
        api_domain="https://www.zohoapis.com",
        connected_by="Herman",
    )
    with database() as session:
        save_tokens(session, cipher, **{**values, **overrides})
        session.commit()


def rows(database) -> int:
    with database() as session:
        return session.execute(text("SELECT COUNT(*) FROM zoho_connection")).scalar()


class TestStatus:
    def test_not_connected_at_first(self, database, configured):
        r = client.get("/integrations/zoho/status")
        assert r.status_code == 200
        assert r.json() == {
            "configured": True,
            "connected": False,
            "connected_by": None,
            "connected_at": None,
            "api_domain": None,
            "message": None,
        }

    def test_connected_shows_who_and_when(self, database, configured):
        connect(database)
        body = client.get("/integrations/zoho/status").json()
        assert body["configured"] is True
        assert body["connected"] is True
        assert body["connected_by"] == "Herman"
        assert body["connected_at"]
        assert body["api_domain"] == "https://www.zohoapis.com"
        assert body["message"] is None

    def test_no_token_or_secret_is_ever_shown(self, database, configured):
        connect(database)
        shown = client.get("/integrations/zoho/status").text
        for secret in ["access-abc", "refresh-def", "the-client-secret", KEY, "gAAAAA"]:
            assert secret not in shown

    def test_it_does_not_call_zoho(self, database, configured, monkeypatch):
        connect(database)

        def forbidden(*args, **kwargs):
            raise AssertionError("the status check must not call Zoho")

        monkeypatch.setattr(main, "revoke_token", forbidden)
        monkeypatch.setattr(main, "list_organizations", forbidden)
        monkeypatch.setattr(main, "get_access_token", forbidden)
        assert client.get("/integrations/zoho/status").json()["connected"] is True

    def test_an_expired_access_token_still_counts_as_connected(self, database, configured):
        connect(database, expires_at=datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=2))
        assert client.get("/integrations/zoho/status").json()["connected"] is True

    def test_not_set_up_on_this_server(self, database, monkeypatch):
        monkeypatch.setattr(main, "settings", main.settings.model_copy(update=dict(zoho_client_id="")))
        r = client.get("/integrations/zoho/status")
        assert r.status_code == 200
        assert r.json()["configured"] is False
        assert r.json()["connected"] is False

    def test_a_connection_the_key_cannot_read_says_to_connect_again(self, database, configured):
        connect(database, cipher=TokenCipher(Fernet.generate_key().decode()))
        body = client.get("/integrations/zoho/status").json()
        assert body["connected"] is False
        assert body["configured"] is True
        assert "Connect Zoho again" in body["message"]

    def test_it_needs_the_access_key(self, configured):
        assert TestClient(main.app).get("/integrations/zoho/status").status_code == 401

    def test_a_database_failure_is_a_503(self, configured, monkeypatch):
        def broken(*args, **kwargs):
            raise OperationalError("select", {}, Exception("connection lost"))

        monkeypatch.setattr(main, "get_tokens", broken)
        r = client.get("/integrations/zoho/status")
        assert r.status_code == 503
        assert "database" in r.json()["detail"].lower()

    def test_the_status_follows_connect_and_disconnect(self, database, configured, monkeypatch):
        monkeypatch.setattr(main, "revoke_token", lambda settings, token, **kw: None)
        assert client.get("/integrations/zoho/status").json()["connected"] is False
        connect(database)
        assert client.get("/integrations/zoho/status").json()["connected"] is True
        client.delete("/integrations/zoho")
        assert client.get("/integrations/zoho/status").json()["connected"] is False


class TestDisconnect:
    def test_it_revokes_the_refresh_token_and_removes_the_connection(self, database, configured, monkeypatch):
        connect(database)
        revoked = []
        monkeypatch.setattr(main, "revoke_token", lambda settings, token, **kw: revoked.append(token))

        r = client.delete("/integrations/zoho")

        assert r.status_code == 200
        assert r.json() == {"was_connected": True, "revoked": True}
        assert revoked == ["1000.refresh-def"]
        assert rows(database) == 0

    def test_the_connection_is_removed_even_if_zoho_cannot_be_reached(self, database, configured, monkeypatch):
        connect(database)

        def down(settings, token, **kwargs):
            raise ZohoError("could not reach Zoho")

        monkeypatch.setattr(main, "revoke_token", down)

        r = client.delete("/integrations/zoho")

        assert r.status_code == 200
        assert r.json() == {"was_connected": True, "revoked": False}
        assert rows(database) == 0

    def test_a_failed_revoke_is_logged_without_the_token(self, database, configured, monkeypatch, caplog):
        connect(database)

        def down(settings, token, **kwargs):
            raise ZohoError("could not reach Zoho")

        monkeypatch.setattr(main, "revoke_token", down)
        with caplog.at_level("WARNING", logger="docflow"):
            client.delete("/integrations/zoho")
        assert "could not reach Zoho" in caplog.text
        assert "refresh-def" not in caplog.text

    def test_disconnecting_when_nothing_is_connected_is_harmless(self, database, configured, monkeypatch):
        called = []
        monkeypatch.setattr(main, "revoke_token", lambda *a, **k: called.append(1))
        r = client.delete("/integrations/zoho")
        assert r.status_code == 200
        assert r.json() == {"was_connected": False, "revoked": False}
        assert called == []

    def test_disconnecting_twice_is_harmless(self, database, configured, monkeypatch):
        connect(database)
        monkeypatch.setattr(main, "revoke_token", lambda *a, **k: None)
        assert client.delete("/integrations/zoho").json()["was_connected"] is True
        assert client.delete("/integrations/zoho").json()["was_connected"] is False

    def test_a_connection_the_key_cannot_read_is_still_removed(self, database, configured, monkeypatch):
        connect(database, cipher=TokenCipher(Fernet.generate_key().decode()))
        called = []
        monkeypatch.setattr(main, "revoke_token", lambda *a, **k: called.append(1))

        r = client.delete("/integrations/zoho")

        assert r.json() == {"was_connected": True, "revoked": False}
        assert called == []
        assert rows(database) == 0

    def test_it_can_remove_a_leftover_connection_when_zoho_is_no_longer_set_up(self, database, monkeypatch):
        connect(database)
        monkeypatch.setattr(main, "settings", main.settings.model_copy(update=dict(zoho_client_id="")))
        r = client.delete("/integrations/zoho")
        assert r.json() == {"was_connected": True, "revoked": False}
        assert rows(database) == 0

    def test_the_stored_tokens_are_gone_for_good(self, database, configured, monkeypatch):
        connect(database)
        monkeypatch.setattr(main, "revoke_token", lambda *a, **k: None)
        client.delete("/integrations/zoho")
        with database() as session:
            assert get_tokens(session, CIPHER) is None

    def test_it_needs_the_access_key(self, database, configured):
        connect(database)
        assert TestClient(main.app).delete("/integrations/zoho").status_code == 401
        assert rows(database) == 1

    def test_a_database_failure_is_a_503(self, configured, monkeypatch):
        def broken(*args, **kwargs):
            raise OperationalError("delete", {}, Exception("connection lost"))

        monkeypatch.setattr(main, "delete_tokens", broken)
        r = client.delete("/integrations/zoho")
        assert r.status_code == 503

    def test_the_browser_may_send_delete(self, configured):
        r = client.options(
            "/integrations/zoho",
            headers={"Origin": main.settings.frontend_origin, "Access-Control-Request-Method": "DELETE"},
        )
        assert r.status_code == 200
        assert "DELETE" in r.headers["access-control-allow-methods"]