import datetime

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy.exc import OperationalError

import main
import zoho_tokens
from crypto import TokenCipher
from zoho_books import API_DOMAINS, Organization, books_url, list_organizations
from zoho_oauth import RefreshResponse, ZohoError
from zoho_store import get_tokens, save_tokens

HEADERS = {"X-Access-Key": main.settings.access_key.get_secret_value()}
client = TestClient(main.app, headers=HEADERS)

KEY = Fernet.generate_key().decode()
CIPHER = TokenCipher(KEY)

SETTINGS = main.settings.model_copy(
    update=dict(
        zoho_client_id="1000.TESTCLIENTID",
        zoho_client_secret=SecretStr("the-client-secret"),
        zoho_redirect_uri="http://localhost:8000/integrations/zoho/callback",
        zoho_accounts_url="https://accounts.zoho.com",
        token_encryption_key=SecretStr(KEY),
    )
)

BOOKS_REPLY = {
    "code": 0,
    "message": "success",
    "organizations": [
        {
            "organization_id": "944669917",
            "name": "Herman Traders",
            "is_default_org": True,
            "currency_code": "KES",
            "country": "Kenya",
        },
        {"organization_id": "100200300", "name": "Second Org", "is_default_org": False, "currency_code": "USD"},
    ],
}


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(main, "settings", SETTINGS)
    return SETTINGS


def connect(database, *, expires_in=datetime.timedelta(hours=1), **overrides):
    values = dict(
        access_token="1000.access-abc",
        refresh_token="1000.refresh-def",
        expires_at=datetime.datetime.now(datetime.UTC) + expires_in,
        scope="ZohoBooks.settings.READ",
        api_domain="https://www.zohoapis.com",
        connected_by="Herman",
    )
    with database() as session:
        save_tokens(session, CIPHER, **{**values, **overrides})
        session.commit()


def books_client(status: int = 200, body=None, *, seen: list | None = None, content: bytes | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        if content is not None:
            return httpx.Response(status, content=content)
        return httpx.Response(status, json=body if body is not None else BOOKS_REPLY)

    return httpx.Client(transport=httpx.MockTransport(handler))


class TestEndpoint:
    def test_it_lists_the_organizations(self, database, configured, monkeypatch):
        connect(database)
        monkeypatch.setattr(main, "list_organizations", lambda settings, token, domain, **kw: [
            Organization("944669917", "Herman Traders", True, "KES"),
            Organization("100200300", "Second Org", False, "USD"),
        ])

        r = client.get("/integrations/zoho/organizations")

        assert r.status_code == 200
        assert r.json()["organizations"] == [
            {"organization_id": "944669917", "name": "Herman Traders", "is_default": True, "currency_code": "KES"},
            {"organization_id": "100200300", "name": "Second Org", "is_default": False, "currency_code": "USD"},
        ]

    def test_it_uses_the_stored_token_and_domain(self, database, configured, monkeypatch):
        connect(database, api_domain="https://www.zohoapis.eu")
        seen = {}

        def fake(settings, token, domain, **kwargs):
            seen.update(token=token, domain=domain)
            return []

        monkeypatch.setattr(main, "list_organizations", fake)
        client.get("/integrations/zoho/organizations")
        assert seen == {"token": "1000.access-abc", "domain": "https://www.zohoapis.eu"}

    def test_an_expired_token_is_renewed_first_and_the_new_one_is_used(self, database, configured, monkeypatch):
        connect(database, expires_in=-datetime.timedelta(hours=2))
        monkeypatch.setattr(
            zoho_tokens,
            "refresh_access_token",
            lambda settings, refresh_token, **kw: RefreshResponse("1000.renewed", 3600, None, None, None),
        )
        used = []
        monkeypatch.setattr(main, "list_organizations", lambda settings, token, domain, **kw: used.append(token) or [])

        r = client.get("/integrations/zoho/organizations")

        assert r.status_code == 200
        assert used == ["1000.renewed"]
        with database() as session:
            saved = get_tokens(session, CIPHER)
        assert saved.access_token == "1000.renewed"
        assert saved.expires_at > datetime.datetime.now(datetime.UTC) + datetime.timedelta(minutes=50)

    def test_a_fresh_token_is_not_renewed(self, database, configured, monkeypatch):
        connect(database)

        def forbidden(*args, **kwargs):
            raise AssertionError("a fresh token must not be refreshed")

        monkeypatch.setattr(zoho_tokens, "refresh_access_token", forbidden)
        monkeypatch.setattr(main, "list_organizations", lambda *a, **k: [])
        assert client.get("/integrations/zoho/organizations").status_code == 200

    def test_not_connected_is_a_409(self, database, configured, monkeypatch):
        monkeypatch.setattr(main, "list_organizations", lambda *a, **k: [])
        r = client.get("/integrations/zoho/organizations")
        assert r.status_code == 409
        assert "not connected" in r.json()["detail"]

    def test_a_rejected_refresh_token_disconnects_and_is_a_409(self, database, configured, monkeypatch):
        from zoho_oauth import ZohoRefreshRejected

        connect(database, expires_in=-datetime.timedelta(hours=2))

        def dead(settings, refresh_token, **kwargs):
            raise ZohoRefreshRejected("invalid_code", code="invalid_code")

        monkeypatch.setattr(zoho_tokens, "refresh_access_token", dead)

        r = client.get("/integrations/zoho/organizations")

        assert r.status_code == 409
        assert "Connect Zoho again" in r.json()["detail"]
        assert client.get("/integrations/zoho/status").json()["connected"] is False

    def test_a_temporary_renewal_problem_is_a_502_and_keeps_the_connection(self, database, configured, monkeypatch):
        connect(database, expires_in=-datetime.timedelta(hours=2))

        def down(settings, refresh_token, **kwargs):
            raise ZohoError("could not reach Zoho")

        monkeypatch.setattr(zoho_tokens, "refresh_access_token", down)

        r = client.get("/integrations/zoho/organizations")

        assert r.status_code == 502
        assert "Try again" in r.json()["detail"]
        assert client.get("/integrations/zoho/status").json()["connected"] is True

    def test_a_zoho_books_problem_is_a_502_with_its_message(self, database, configured, monkeypatch):
        connect(database)

        def refused(settings, token, domain, **kwargs):
            raise ZohoError("Zoho Books said: You are not authorized to perform this operation")

        monkeypatch.setattr(main, "list_organizations", refused)
        r = client.get("/integrations/zoho/organizations")
        assert r.status_code == 502
        assert "not authorized" in r.json()["detail"]

    def test_the_access_token_is_never_in_a_response_or_a_log(self, database, configured, monkeypatch, caplog):
        connect(database)

        def refused(settings, token, domain, **kwargs):
            raise ZohoError("Zoho Books refused the request (HTTP 401)")

        monkeypatch.setattr(main, "list_organizations", refused)
        with caplog.at_level("WARNING", logger="docflow"):
            r = client.get("/integrations/zoho/organizations")
        assert "access-abc" not in r.text
        assert "access-abc" not in caplog.text

    def test_not_set_up_is_a_503(self, database, monkeypatch):
        monkeypatch.setattr(main, "settings", main.settings.model_copy(update=dict(zoho_client_id="")))
        assert client.get("/integrations/zoho/organizations").status_code == 503

    def test_it_needs_the_access_key(self, configured):
        assert TestClient(main.app).get("/integrations/zoho/organizations").status_code == 401

    def test_a_database_failure_is_a_503(self, configured, monkeypatch):
        def broken(*args, **kwargs):
            raise OperationalError("select", {}, Exception("connection lost"))

        monkeypatch.setattr(main, "get_access_token", broken)
        assert client.get("/integrations/zoho/organizations").status_code == 503


class TestBooksCall:
    def test_the_request_asks_for_organizations_with_the_zoho_header(self):
        seen = []
        result = list_organizations(SETTINGS, "1000.tok", "https://www.zohoapis.com", http=books_client(seen=seen))

        request = seen[0]
        assert request.method == "GET"
        assert str(request.url) == "https://www.zohoapis.com/books/v3/organizations"
        assert request.headers["authorization"] == "Zoho-oauthtoken 1000.tok"
        assert result == [
            Organization("944669917", "Herman Traders", True, "KES"),
            Organization("100200300", "Second Org", False, "USD"),
        ]

    def test_the_token_is_not_in_the_address(self):
        seen = []
        list_organizations(SETTINGS, "1000.tok", "https://www.zohoapis.com", http=books_client(seen=seen))
        assert "1000.tok" not in str(seen[0].url)

    def test_an_error_reply_uses_zohos_message(self):
        reply = {"code": 57, "message": "You are not authorized to perform this operation"}
        with pytest.raises(ZohoError, match="not authorized") as caught:
            list_organizations(SETTINGS, "t", None, http=books_client(401, reply))
        assert caught.value.code == "57"

    def test_an_error_code_in_a_200_reply_is_caught(self):
        with pytest.raises(ZohoError, match="Zoho Books said: nope"):
            list_organizations(SETTINGS, "t", None, http=books_client(200, {"code": 5, "message": "nope"}))

    def test_an_error_without_a_message_still_says_something(self):
        with pytest.raises(ZohoError, match="HTTP 500"):
            list_organizations(SETTINGS, "t", None, http=books_client(500, {"code": 1}))

    def test_a_reply_that_is_not_json_is_caught(self):
        with pytest.raises(ZohoError, match="unreadable"):
            list_organizations(SETTINGS, "t", None, http=books_client(502, content=b"<html>bad gateway</html>"))

    def test_a_reply_that_is_not_an_object_is_caught(self):
        with pytest.raises(ZohoError):
            list_organizations(SETTINGS, "t", None, http=books_client(200, ["a"]))

    def test_a_network_failure_is_caught(self):
        def down(request):
            raise httpx.ConnectError("no route")

        with pytest.raises(ZohoError, match="could not reach"):
            list_organizations(SETTINGS, "t", None, http=httpx.Client(transport=httpx.MockTransport(down)))

    def test_no_organizations_is_an_empty_list(self):
        assert list_organizations(SETTINGS, "t", None, http=books_client(body={"code": 0, "organizations": []})) == []
        assert list_organizations(SETTINGS, "t", None, http=books_client(body={"code": 0})) == []

    def test_entries_without_an_id_are_skipped(self):
        body = {"code": 0, "organizations": [{"name": "No id"}, "junk", {"organization_id": 5, "name": None}]}
        assert list_organizations(SETTINGS, "t", None, http=books_client(body=body)) == [
            Organization("5", "", False, None)
        ]


class TestBooksAddress:
    def test_a_zoho_api_domain_is_used(self):
        assert books_url(SETTINGS, "https://www.zohoapis.eu") == "https://www.zohoapis.eu/books/v3"
        assert books_url(SETTINGS, "https://zohoapis.com") == "https://zohoapis.com/books/v3"

    def test_without_a_stored_domain_the_data_centre_decides(self):
        assert books_url(SETTINGS, None) == "https://www.zohoapis.com/books/v3"
        eu = SETTINGS.model_copy(update=dict(zoho_accounts_url="https://accounts.zoho.eu"))
        assert books_url(eu, None) == "https://www.zohoapis.eu/books/v3"
        assert set(API_DOMAINS.values()) >= {"https://www.zohoapis.com", "https://www.zohoapis.in"}

    @pytest.mark.parametrize(
        "bad",
        [
            "http://www.zohoapis.com",
            "https://evil.example.com",
            "https://zohoapis.com.evil.example.com",
            "https://evilzohoapis.com",
            "https://www.zohoapis.com@evil.example.com",
            "not a url",
            "",
        ],
    )
    def test_an_address_that_is_not_zoho_is_never_trusted_with_the_token(self, bad):
        assert books_url(SETTINGS, bad) == "https://www.zohoapis.com/books/v3"