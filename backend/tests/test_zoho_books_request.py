import json

import httpx
import pytest
from pydantic import SecretStr

import main
from zoho_books import (
    NO_ORGANIZATION,
    RECONNECT_HINT,
    TOO_MANY_REQUESTS,
    ZohoBooksError,
    books_request,
)
from zoho_oauth import ZohoError

SETTINGS = main.settings.model_copy(
    update=dict(
        zoho_client_id="1000.TESTCLIENTID",
        zoho_client_secret=SecretStr("the-client-secret"),
        zoho_accounts_url="https://accounts.zoho.com",
        zoho_organization_id="944669917",
    )
)
NO_ORG = SETTINGS.model_copy(update=dict(zoho_organization_id=""))
DOMAIN = "https://www.zohoapis.com"


def books_client(status: int = 200, body=None, *, seen: list | None = None, content: bytes | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        if content is not None:
            return httpx.Response(status, content=content)
        return httpx.Response(status, json=body if body is not None else {"code": 0, "message": "success"})

    return httpx.Client(transport=httpx.MockTransport(handler))


def call(client, method="GET", path="bills", **kwargs):
    return books_request(SETTINGS, "1000.tok", DOMAIN, method, path, http=client, **kwargs)


class TestTheRequest:
    def test_a_get_carries_the_organization_and_the_token_header(self):
        seen = []
        call(books_client(seen=seen))

        request = seen[0]
        assert request.method == "GET"
        assert request.url.scheme == "https"
        assert request.url.host == "www.zohoapis.com"
        assert request.url.path == "/books/v3/bills"
        assert dict(request.url.params) == {"organization_id": "944669917"}
        assert request.headers["authorization"] == "Zoho-oauthtoken 1000.tok"

    def test_extra_query_values_are_kept_next_to_the_organization(self):
        seen = []
        call(books_client(seen=seen), params={"bill_number": "INV-1"})
        assert dict(seen[0].url.params) == {"bill_number": "INV-1", "organization_id": "944669917"}

    def test_a_post_sends_its_body_as_json_and_the_organization_in_the_address(self):
        seen = []
        call(books_client(seen=seen), "POST", "bills", json={"vendor_id": "7", "line_items": [{"rate": 1}]})

        request = seen[0]
        assert request.method == "POST"
        assert request.headers["content-type"] == "application/json"
        assert json.loads(request.content) == {"vendor_id": "7", "line_items": [{"rate": 1}]}
        assert dict(request.url.params) == {"organization_id": "944669917"}

    def test_a_get_sends_no_body(self):
        seen = []
        call(books_client(seen=seen))
        assert seen[0].content == b""

    def test_a_leading_slash_on_the_path_is_fine(self):
        seen = []
        call(books_client(seen=seen), path="/contacts")
        assert seen[0].url.path == "/books/v3/contacts"

    def test_the_organization_can_be_left_out(self):
        seen = []
        call(books_client(seen=seen), path="organizations", with_organization=False)
        assert dict(seen[0].url.params) == {}

    def test_the_token_is_never_in_the_address(self):
        seen = []
        call(books_client(seen=seen))
        assert "1000.tok" not in str(seen[0].url)

    def test_a_success_returns_the_reply(self):
        reply = {"code": 0, "message": "success", "bill": {"bill_id": "55"}}
        assert call(books_client(body=reply)) == reply

    def test_a_reply_without_a_code_counts_as_success(self):
        assert call(books_client(body={"bills": []})) == {"bills": []}

    def test_a_missing_organization_id_stops_before_any_request(self):
        seen = []
        with pytest.raises(ZohoError, match="ZOHO_ORGANIZATION_ID"):
            books_request(NO_ORG, "t", DOMAIN, "GET", "bills", http=books_client(seen=seen))
        assert seen == []
        assert "ZOHO_ORGANIZATION_ID" in NO_ORGANIZATION

    def test_a_missing_organization_id_does_not_matter_when_none_is_needed(self):
        books_request(NO_ORG, "t", DOMAIN, "GET", "organizations", with_organization=False, http=books_client())


class TestErrors:
    def test_an_expired_token_says_to_connect_again(self):
        reply = {"code": 14, "message": "The access token provided is invalid"}
        with pytest.raises(ZohoBooksError) as caught:
            call(books_client(401, reply))
        error = caught.value
        assert "The access token provided is invalid" in str(error)
        assert RECONNECT_HINT in str(error)
        assert error.needs_reconnect is True
        assert error.status == 401
        assert error.code == "14"

    def test_a_missing_permission_says_to_connect_again(self):
        reply = {"code": 57, "message": "You are not authorized to perform this operation"}
        with pytest.raises(ZohoBooksError) as caught:
            call(books_client(400, reply))
        assert "not authorized" in str(caught.value)
        assert RECONNECT_HINT in str(caught.value)
        assert caught.value.needs_reconnect is True
        assert caught.value.code == "57"

    def test_a_validation_problem_shows_zohos_own_words_and_no_reconnect_advice(self):
        reply = {"code": 1001, "message": "Please ensure that the bill number is not empty."}
        with pytest.raises(ZohoBooksError) as caught:
            call(books_client(400, reply), "POST", json={})
        assert str(caught.value) == "Zoho Books said: Please ensure that the bill number is not empty."
        assert caught.value.needs_reconnect is False
        assert caught.value.status == 400
        assert caught.value.code == "1001"

    def test_an_error_code_inside_a_200_reply_is_caught(self):
        with pytest.raises(ZohoBooksError, match="Zoho Books said: nope") as caught:
            call(books_client(200, {"code": 5, "message": "nope"}))
        assert caught.value.needs_reconnect is False

    def test_too_many_requests_has_its_own_message(self):
        with pytest.raises(ZohoBooksError) as caught:
            call(books_client(429, {"code": 45, "message": "Rate limit exceeded"}))
        assert str(caught.value) == TOO_MANY_REQUESTS
        assert caught.value.needs_reconnect is False

    def test_an_error_without_a_message_still_says_something(self):
        with pytest.raises(ZohoBooksError, match=r"refused the request \(HTTP 500\)"):
            call(books_client(500, {"code": 1}))

    def test_an_unauthorised_reply_without_a_message_still_gives_the_advice(self):
        with pytest.raises(ZohoBooksError) as caught:
            call(books_client(401, {}))
        assert "HTTP 401" in str(caught.value)
        assert RECONNECT_HINT in str(caught.value)

    def test_the_message_does_not_end_in_a_double_stop(self):
        with pytest.raises(ZohoBooksError) as caught:
            call(books_client(400, {"code": 1, "message": "Bad value."}))
        assert str(caught.value) == "Zoho Books said: Bad value."

    def test_a_reply_that_is_not_json_is_caught(self):
        with pytest.raises(ZohoError, match="unreadable"):
            call(books_client(502, content=b"<html>bad gateway</html>"))

    def test_a_reply_that_is_not_an_object_is_caught(self):
        with pytest.raises(ZohoError, match="unexpected"):
            call(books_client(200, ["a"]))

    def test_a_network_failure_is_caught(self):
        def down(request):
            raise httpx.ConnectError("no route")

        with pytest.raises(ZohoError, match="could not reach Zoho Books"):
            call(httpx.Client(transport=httpx.MockTransport(down)))

    def test_a_timeout_is_caught(self):
        def slow(request):
            raise httpx.ReadTimeout("too slow")

        with pytest.raises(ZohoError, match="could not reach Zoho Books"):
            call(httpx.Client(transport=httpx.MockTransport(slow)))

    def test_a_books_error_is_still_a_plain_zoho_error_for_older_callers(self):
        with pytest.raises(ZohoError):
            call(books_client(400, {"code": 1, "message": "x"}))

    def test_the_token_never_appears_in_an_error(self):
        with pytest.raises(ZohoBooksError) as caught:
            call(books_client(401, {"code": 14, "message": "invalid"}))
        assert "1000.tok" not in str(caught.value)