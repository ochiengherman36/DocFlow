import httpx
import pytest
from pydantic import SecretStr

import main
from zoho_oauth import ZohoError, revoke_token

SETTINGS = main.settings.model_copy(
    update=dict(
        zoho_client_id="1000.TESTCLIENTID",
        zoho_client_secret=SecretStr("the-client-secret"),
        zoho_accounts_url="https://accounts.zoho.com",
    )
)


def client_replying(status: int = 200, body=None, *, content: bytes | None = None, seen: list | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        if content is not None:
            return httpx.Response(status, content=content)
        return httpx.Response(status, json=body)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_a_confirmed_revoke_returns_quietly():
    revoke_token(SETTINGS, "1000.refresh", http=client_replying(body={"status": "success"}))


def test_the_request_posts_the_token_to_the_revoke_address():
    seen = []
    revoke_token(SETTINGS, "1000.refresh", http=client_replying(body={"status": "success"}, seen=seen))
    request = seen[0]
    assert request.method == "POST"
    assert request.url.scheme == "https"
    assert request.url.host == "accounts.zoho.com"
    assert request.url.path == "/oauth/v2/token/revoke"
    assert dict(request.url.params) == {"token": "1000.refresh"}


def test_the_client_secret_is_not_sent():
    seen = []
    revoke_token(SETTINGS, "1000.refresh", http=client_replying(body={"status": "success"}, seen=seen))
    assert "the-client-secret" not in str(seen[0].url)
    assert "the-client-secret" not in seen[0].content.decode()


def test_the_address_follows_the_data_centre():
    seen = []
    eu = SETTINGS.model_copy(update=dict(zoho_accounts_url="https://accounts.zoho.eu"))
    revoke_token(eu, "r", http=client_replying(body={"status": "success"}, seen=seen))
    assert seen[0].url.host == "accounts.zoho.eu"


@pytest.mark.parametrize(
    "body",
    [{"status": "failure"}, {"error": "invalid_token"}, {}, ["a"], "text"],
)
def test_anything_but_success_is_an_error(body):
    with pytest.raises(ZohoError):
        revoke_token(SETTINGS, "r", http=client_replying(body=body))


def test_zohos_own_reason_is_kept():
    with pytest.raises(ZohoError, match="invalid_token") as caught:
        revoke_token(SETTINGS, "r", http=client_replying(body={"error": "invalid_token"}))
    assert caught.value.code == "invalid_token"


def test_a_reply_that_is_not_json_is_an_error():
    with pytest.raises(ZohoError, match="unreadable"):
        revoke_token(SETTINGS, "r", http=client_replying(502, content=b"<html>down</html>"))


def test_a_network_failure_is_an_error():
    def down(request):
        raise httpx.ConnectError("no route")

    with pytest.raises(ZohoError, match="could not reach"):
        revoke_token(SETTINGS, "r", http=httpx.Client(transport=httpx.MockTransport(down)))


def test_the_error_text_never_contains_the_token():
    with pytest.raises(ZohoError) as caught:
        revoke_token(SETTINGS, "1000.my-refresh", http=client_replying(body={"error": "invalid_token"}))
    assert "my-refresh" not in str(caught.value)