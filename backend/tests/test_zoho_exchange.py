import datetime
from urllib.parse import parse_qs

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import select

import main
from models import OAuthState
from zoho_oauth import TokenResponse, ZohoError, consume_state, create_state, exchange_code

SETTINGS = main.settings.model_copy(
    update=dict(
        zoho_client_id="1000.TESTCLIENTID",
        zoho_client_secret=SecretStr("the-client-secret"),
        zoho_redirect_uri="http://localhost:8000/integrations/zoho/callback",
        zoho_accounts_url="https://accounts.zoho.com",
    )
)

SUCCESS = {
    "access_token": "1000.access",
    "refresh_token": "1000.refresh",
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


def test_the_code_is_traded_for_tokens():
    seen = []
    result = exchange_code(SETTINGS, "the-code", http=client_replying(body=SUCCESS, seen=seen))

    assert result == TokenResponse(
        access_token="1000.access",
        refresh_token="1000.refresh",
        expires_in=3600,
        scope="ZohoBooks.bills.CREATE",
        api_domain="https://www.zohoapis.com",
    )
    assert len(seen) == 1


def test_the_request_goes_to_the_token_address_as_a_form_post():
    seen = []
    exchange_code(SETTINGS, "the-code", http=client_replying(body=SUCCESS, seen=seen))
    request = seen[0]

    assert request.method == "POST"
    assert str(request.url) == "https://accounts.zoho.com/oauth/v2/token"
    assert request.headers["content-type"] == "application/x-www-form-urlencoded"
    form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
    assert form == {
        "grant_type": "authorization_code",
        "code": "the-code",
        "client_id": "1000.TESTCLIENTID",
        "client_secret": "the-client-secret",
        "redirect_uri": "http://localhost:8000/integrations/zoho/callback",
    }


def test_the_secret_travels_in_the_body_never_in_the_address():
    seen = []
    exchange_code(SETTINGS, "the-code", http=client_replying(body=SUCCESS, seen=seen))
    assert "the-client-secret" not in str(seen[0].url)
    assert "the-code" not in str(seen[0].url)


def test_the_token_address_follows_the_data_centre():
    seen = []
    eu = SETTINGS.model_copy(update=dict(zoho_accounts_url="https://accounts.zoho.eu"))
    exchange_code(eu, "c", http=client_replying(body=SUCCESS, seen=seen))
    assert str(seen[0].url) == "https://accounts.zoho.eu/oauth/v2/token"


def test_zoho_reporting_an_error_in_a_200_reply_is_caught():
    with pytest.raises(ZohoError, match="invalid_code"):
        exchange_code(SETTINGS, "bad", http=client_replying(200, {"error": "invalid_code"}))


def test_an_http_error_is_caught():
    with pytest.raises(ZohoError):
        exchange_code(SETTINGS, "bad", http=client_replying(400, {"message": "nope"}))
    with pytest.raises(ZohoError, match="invalid_client"):
        exchange_code(SETTINGS, "bad", http=client_replying(401, {"error": "invalid_client"}))


def test_a_reply_that_is_not_json_is_caught():
    with pytest.raises(ZohoError):
        exchange_code(SETTINGS, "c", http=client_replying(502, content=b"<html>Bad gateway</html>"))


def test_a_reply_that_is_not_an_object_is_caught():
    with pytest.raises(ZohoError):
        exchange_code(SETTINGS, "c", http=client_replying(200, ["a", "list"]))


@pytest.mark.parametrize("missing", ["access_token", "refresh_token"])
def test_a_reply_without_both_tokens_is_caught(missing):
    body = {k: v for k, v in SUCCESS.items() if k != missing}
    with pytest.raises(ZohoError, match="both tokens"):
        exchange_code(SETTINGS, "c", http=client_replying(body=body))


@pytest.mark.parametrize("bad", [None, 0, -5, "3600", 12.5])
def test_a_missing_or_odd_lifetime_falls_back_to_an_hour(bad):
    body = {**SUCCESS, "expires_in": bad}
    assert exchange_code(SETTINGS, "c", http=client_replying(body=body)).expires_in == 3600


def test_a_lifetime_zoho_sends_is_kept():
    body = {**SUCCESS, "expires_in": 1800}
    assert exchange_code(SETTINGS, "c", http=client_replying(body=body)).expires_in == 1800


def test_optional_fields_may_be_missing():
    body = {"access_token": "a", "refresh_token": "r"}
    result = exchange_code(SETTINGS, "c", http=client_replying(body=body))
    assert (result.scope, result.api_domain) == (None, None)


def test_a_network_failure_is_caught():
    def down(request):
        raise httpx.ConnectError("no route")

    with pytest.raises(ZohoError, match="could not reach"):
        exchange_code(SETTINGS, "c", http=httpx.Client(transport=httpx.MockTransport(down)))


def test_a_timeout_is_caught():
    def slow(request):
        raise httpx.ReadTimeout("too slow")

    with pytest.raises(ZohoError):
        exchange_code(SETTINGS, "c", http=httpx.Client(transport=httpx.MockTransport(slow)))


def test_the_error_text_never_contains_the_secret():
    with pytest.raises(ZohoError) as caught:
        exchange_code(SETTINGS, "the-code", http=client_replying(401, {"error": "invalid_client"}))
    assert "the-client-secret" not in str(caught.value)
    assert "the-code" not in str(caught.value)


NOW = datetime.datetime(2026, 11, 1, 9, 0, tzinfo=datetime.UTC)


def test_a_fresh_state_is_accepted_once_and_names_who_started_it(database):
    with database() as session:
        state = create_state(session, "Herman", now=NOW)
        session.commit()
    with database() as session:
        assert consume_state(session, state, now=NOW + datetime.timedelta(minutes=1)) == "Herman"
        session.commit()
    with database() as session:
        assert consume_state(session, state, now=NOW + datetime.timedelta(minutes=2)) is None


def test_a_state_works_right_up_to_ten_minutes_and_not_after(database):
    with database() as session:
        state = create_state(session, "Herman", now=NOW)
        session.commit()
    with database() as session:
        assert consume_state(session, state, now=NOW + datetime.timedelta(minutes=10, seconds=1)) is None
    with database() as session:
        assert consume_state(session, state, now=NOW + datetime.timedelta(minutes=9, seconds=59)) == "Herman"


@pytest.mark.parametrize("bad", [None, "", "unknown", "x" * 129])
def test_other_states_are_refused(database, bad):
    with database() as session:
        create_state(session, "Herman", now=NOW)
        session.commit()
    with database() as session:
        assert consume_state(session, bad, now=NOW) is None


def test_using_a_state_marks_when_it_was_used(database):
    with database() as session:
        state = create_state(session, "Herman", now=NOW)
        session.commit()
    with database() as session:
        consume_state(session, state, now=NOW + datetime.timedelta(minutes=3))
        session.commit()
    with database() as session:
        used = session.scalars(select(OAuthState)).one().used_at
    assert used.replace(tzinfo=datetime.UTC) == NOW + datetime.timedelta(minutes=3)