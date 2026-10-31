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


def list_organizations(
    settings: Settings, access_token: str, api_domain: str | None, *, http: httpx.Client | None = None
) -> list[Organization]:
    url = f"{books_url(settings, api_domain)}/organizations"
    headers = {"Authorization": f"Zoho-oauthtoken {access_token}"}
    try:
        if http is None:
            with httpx.Client(timeout=BOOKS_TIMEOUT_SECONDS) as owned:
                response = owned.get(url, headers=headers)
        else:
            response = http.get(url, headers=headers)
    except httpx.HTTPError as exc:
        raise ZohoError("could not reach Zoho Books") from exc

    try:
        body = response.json()
    except ValueError as exc:
        raise ZohoError(f"Zoho Books answered with something unreadable (HTTP {response.status_code})") from exc
    if not isinstance(body, dict):
        raise ZohoError("Zoho Books answered with something unexpected")

    code = body.get("code")
    if response.status_code >= 400 or code not in (0, None):
        message = body.get("message")
        if isinstance(message, str) and message.strip():
            raise ZohoError(f"Zoho Books said: {message.strip()}", code=str(code))
        raise ZohoError(f"Zoho Books refused the request (HTTP {response.status_code})", code=str(code))

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