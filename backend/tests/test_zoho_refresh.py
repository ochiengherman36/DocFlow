from urllib.parse import parse_qs

import httpx
import pytest
from pydantic import SecretStr

import main
from zoho_oauth import RefreshResponse, ZohoError, ZohoRefreshRejected, refresh_access_token

SETTINGS = main.settings.model_copy(
    update=dict(
        zoho_client_id="1000.TESTCLIENTID",
        zoho_client_secret=SecretStr("the-client-secret"),
        zoho_redirect_uri="http://localhost:8000/integrations/zoho/callback",
        zoho_accounts_url="https://accounts.zoho.com",
    )
)

SUCCESS = {
    "access_token": "1000.new-access",
    "scope": "ZohoBooks.bills.CREATE",
    "api_domain": "https://www.zohoapis.com",
    "token_type": "Bearer",
    "expires_in": 3600,
}


def client_replying(status: int = 200, body=None, *, content: bytes | None = None, seen: list | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        if content is not None:
            return httpx.Response(status, content=content)
        return httpx.Response(status, json=body)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_a_new_access_token_comes_back():
    result = refresh_access_token(SETTINGS, "1000.refresh", http=client_replying(body=SUCCESS))
    assert result == RefreshResponse(
        access_token="1000.new-access",
        expires_in=3600,
        refresh_token=None,
        scope="ZohoBooks.bills.CREATE",
        api_domain="https://www.zohoapis.com",
    )


def test_the_request_is_a_refresh_grant_to_the_token_address():
    seen = []
    refresh_access_token(SETTINGS, "1000.refresh", http=client_replying(body=SUCCESS, seen=seen))
    request = seen[0]

    assert request.method == "POST"
    assert str(request.url) == "https://accounts.zoho.com/oauth/v2/token"
    form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
    assert form == {
        "grant_type": "refresh_token",
        "refresh_token": "1000.refresh",
        "client_id": "1000.TESTCLIENTID",
        "client_secret": "the-client-secret",
    }


def test_nothing_secret_is_in_the_address():
    seen = []
    refresh_access_token(SETTINGS, "1000.refresh", http=client_replying(body=SUCCESS, seen=seen))
    assert "the-client-secret" not in str(seen[0].url)
    assert "1000.refresh" not in str(seen[0].url)


def test_a_new_refresh_token_is_passed_on_if_zoho_sends_one():
    body = {**SUCCESS, "refresh_token": "1000.rotated"}
    assert refresh_access_token(SETTINGS, "old", http=client_replying(body=body)).refresh_token == "1000.rotated"


@pytest.mark.parametrize("bad", [None, 0, -1, "3600"])
def test_an_odd_lifetime_falls_back_to_an_hour(bad):
    body = {**SUCCESS, "expires_in": bad}
    assert refresh_access_token(SETTINGS, "r", http=client_replying(body=body)).expires_in == 3600


@pytest.mark.parametrize("status", [200, 400, 401])
@pytest.mark.parametrize("code", ["invalid_code", "invalid_grant", "invalid_token"])
def test_a_refused_refresh_token_is_reported_as_rejected(status, code):
    with pytest.raises(ZohoRefreshRejected) as caught:
        refresh_access_token(SETTINGS, "dead", http=client_replying(status, {"error": code}))
    assert caught.value.code == code


def test_a_bad_client_is_not_blamed_on_the_refresh_token():
    with pytest.raises(ZohoError) as caught:
        refresh_access_token(SETTINGS, "fine", http=client_replying(401, {"error": "invalid_client"}))
    assert not isinstance(caught.value, ZohoRefreshRejected)


def test_zohos_rate_limit_reply_is_not_a_rejection():
    body = {
        "error_description": "You have made too many requests continuously. Please try again after some time.",
        "error": "Access Denied",
        "status": "failure",
    }
    with pytest.raises(ZohoError) as caught:
        refresh_access_token(SETTINGS, "fine", http=client_replying(200, body))
    assert not isinstance(caught.value, ZohoRefreshRejected)


@pytest.mark.parametrize("status", [500, 502, 503])
def test_a_server_problem_is_not_a_rejection(status):
    with pytest.raises(ZohoError) as caught:
        refresh_access_token(SETTINGS, "fine", http=client_replying(status, content=b"<html>down</html>"))
    assert not isinstance(caught.value, ZohoRefreshRejected)


def test_a_network_failure_is_not_a_rejection():
    def down(request):
        raise httpx.ConnectError("no route")

    with pytest.raises(ZohoError, match="could not reach") as caught:
        refresh_access_token(SETTINGS, "fine", http=httpx.Client(transport=httpx.MockTransport(down)))
    assert not isinstance(caught.value, ZohoRefreshRejected)


def test_a_reply_without_an_access_token_is_an_error():
    with pytest.raises(ZohoError, match="access token"):
        refresh_access_token(SETTINGS, "fine", http=client_replying(body={"expires_in": 3600}))


def test_the_error_never_contains_the_secret_or_the_token():
    with pytest.raises(ZohoError) as caught:
        refresh_access_token(SETTINGS, "1000.my-refresh", http=client_replying(401, {"error": "invalid_client"}))
    assert "the-client-secret" not in str(caught.value)
    assert "my-refresh" not in str(caught.value)


def test_the_address_follows_the_data_centre():
    seen = []
    eu = SETTINGS.model_copy(update=dict(zoho_accounts_url="https://accounts.zoho.eu"))
    refresh_access_token(eu, "r", http=client_replying(body=SUCCESS, seen=seen))
    assert str(seen[0].url) == "https://accounts.zoho.eu/oauth/v2/token"