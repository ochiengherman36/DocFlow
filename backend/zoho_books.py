import re
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

from config import Settings
from zoho_oauth import ZohoError

BOOKS_TIMEOUT_SECONDS = 15

API_DOMAINS = {
    "https://accounts.zoho.com": "https://www.zohoapis.com",
    "https://accounts.zoho.eu": "https://www.zohoapis.eu",
    "https://accounts.zoho.in": "https://www.zohoapis.in",
    "https://accounts.zoho.com.au": "https://www.zohoapis.com.au",
    "https://accounts.zoho.jp": "https://www.zohoapis.jp",
}
DEFAULT_API_DOMAIN = "https://www.zohoapis.com"
SAFE_API_HOST = re.compile(r"(^|\.)zohoapis\.(com|eu|in|com\.au|jp|sa|ca|com\.cn)$")

RECONNECT_HINT = "The connection may have expired or may be missing a permission. Disconnect and connect Zoho again."
NO_ORGANIZATION = "ZOHO_ORGANIZATION_ID is not set on the server."
TOO_MANY_REQUESTS = "Zoho Books is receiving too many requests. Wait a minute and try again."


class ZohoBooksError(ZohoError):
    """Zoho Books answered, but with an error. needs_reconnect is true when the token or its permissions are the problem."""

    def __init__(self, message: str, code: str | None = None, *, status: int | None = None, needs_reconnect: bool = False):
        super().__init__(message, code)
        self.status = status
        self.needs_reconnect = needs_reconnect


@dataclass(frozen=True)
class Organization:
    organization_id: str
    name: str
    is_default: bool
    currency_code: str | None


def books_url(settings: Settings, api_domain: str | None) -> str:
    """The Books API address. The stored domain is used only if it really is a Zoho API address,
    because the access token is sent there."""
    base = None
    if api_domain:
        parts = urlparse(api_domain)
        if parts.scheme == "https" and parts.hostname and SAFE_API_HOST.search(parts.hostname):
            base = f"https://{parts.hostname}"
    if base is None:
        base = API_DOMAINS.get(settings.zoho_accounts_url, DEFAULT_API_DOMAIN)
    return f"{base}/books/v3"


def _books_error(status: int, code, message) -> ZohoBooksError:
    code_text = None if code is None else str(code)
    text = message.strip().rstrip(".") if isinstance(message, str) else ""
    needs_reconnect = status == 401 or code_text == "57"

    if status == 429:
        reason = TOO_MANY_REQUESTS
    elif text:
        reason = f"Zoho Books said: {text}."
    else:
        reason = f"Zoho Books refused the request (HTTP {status})."
    if needs_reconnect:
        reason = f"{reason} {RECONNECT_HINT}"
    return ZohoBooksError(reason, code=code_text, status=status, needs_reconnect=needs_reconnect)


def _read_reply(response: httpx.Response) -> dict:
    try:
        body = response.json()
    except ValueError as exc:
        raise ZohoError(f"Zoho Books answered with something unreadable (HTTP {response.status_code})") from exc
    if not isinstance(body, dict):
        raise ZohoError("Zoho Books answered with something unexpected")

    code = body.get("code")
    if response.status_code >= 400 or code not in (0, None):
        raise _books_error(response.status_code, code, body.get("message"))
    return body


def books_request(
    settings: Settings,
    access_token: str,
    api_domain: str | None,
    method: str,
    path: str,
    *,
    params: dict | None = None,
    json: dict | None = None,
    with_organization: bool = True,
    http: httpx.Client | None = None,
) -> dict:
    """Call Zoho Books and return the reply as a dict.

    The organization id goes in the address, the body (for POST and PUT) is sent as JSON, and the
    token only in the header. Anything that is not a clean success raises ZohoError, or ZohoBooksError
    when Zoho Books itself answered with an error.
    """
    query = dict(params or {})
    if with_organization:
        if not settings.zoho_organization_id:
            raise ZohoError(NO_ORGANIZATION)
        query["organization_id"] = settings.zoho_organization_id

    url = f"{books_url(settings, api_domain)}/{path.lstrip('/')}"
    headers = {"Authorization": f"Zoho-oauthtoken {access_token}"}
    try:
        if http is None:
            with httpx.Client(timeout=BOOKS_TIMEOUT_SECONDS) as owned:
                response = owned.request(method, url, params=query, json=json, headers=headers)
        else:
            response = http.request(method, url, params=query, json=json, headers=headers)
    except httpx.HTTPError as exc:
        raise ZohoError("could not reach Zoho Books") from exc

    return _read_reply(response)


def list_organizations(
    settings: Settings, access_token: str, api_domain: str | None, *, http: httpx.Client | None = None
) -> list[Organization]:
    body = books_request(settings, access_token, api_domain, "GET", "organizations", with_organization=False, http=http)

    organizations = body.get("organizations")
    if not isinstance(organizations, list):
        return []
    return [
        Organization(
            organization_id=str(item["organization_id"]),
            name=str(item.get("name") or ""),
            is_default=bool(item.get("is_default_org")),
            currency_code=item.get("currency_code"),
        )
        for item in organizations
        if isinstance(item, dict) and item.get("organization_id")
    ]