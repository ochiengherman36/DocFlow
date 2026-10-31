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
NO_EXPENSE_ACCOUNT = (
    "No active expense account was found in Zoho Books. Add one under Accountant, Chart of Accounts, "
    "or set ZOHO_EXPENSE_ACCOUNT_ID on the server."
)

EXPENSE_TYPES = ("expense", "cost_of_goods_sold", "other_expense")
PREFERRED_NAMES = ("other expenses", "office supplies")
ACCOUNTS_PER_PAGE = 200
MAX_ACCOUNT_PAGES = 5


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

@dataclass(frozen=True)
class Account:
    account_id: str
    name: str
    account_type: str


def list_accounts(
    settings: Settings, access_token: str, api_domain: str | None, *, http: httpx.Client | None = None
) -> list[Account]:
    """The active accounts in the chart of accounts. Needs the ZohoBooks.accountants.READ scope."""
    accounts: list[Account] = []
    for page in range(1, MAX_ACCOUNT_PAGES + 1):
        body = books_request(
            settings,
            access_token,
            api_domain,
            "GET",
            "chartofaccounts",
            params={"page": page, "per_page": ACCOUNTS_PER_PAGE},
            http=http,
        )
        items = body.get("chartofaccounts")
        for item in items if isinstance(items, list) else []:
            if not isinstance(item, dict) or not item.get("account_id") or item.get("is_active") is False:
                continue
            accounts.append(
                Account(
                    account_id=str(item["account_id"]),
                    name=str(item.get("account_name") or ""),
                    account_type=str(item.get("account_type") or ""),
                )
            )

        page_context = body.get("page_context")
        if not (isinstance(page_context, dict) and page_context.get("has_more_page")):
            break
    return accounts


def choose_expense_account(accounts: list[Account]) -> Account | None:
    """Prefer 'Other Expenses', then 'Office Supplies'. Otherwise the first expense account by name,
    then cost of goods sold, then other expenses."""
    usable = [a for a in accounts if a.account_type in EXPENSE_TYPES]
    for wanted in PREFERRED_NAMES:
        for account in usable:
            if account.name.casefold() == wanted:
                return account
    for account_type in EXPENSE_TYPES:
        matching = sorted((a for a in usable if a.account_type == account_type), key=lambda a: a.name.casefold())
        if matching:
            return matching[0]
    return None


def find_expense_account(
    settings: Settings, access_token: str, api_domain: str | None, *, http: httpx.Client | None = None
) -> Account:
    """The account bill lines are booked to: ZOHO_EXPENSE_ACCOUNT_ID if set, otherwise chosen from Zoho Books."""
    if settings.zoho_expense_account_id:
        return Account(settings.zoho_expense_account_id, "the account set in ZOHO_EXPENSE_ACCOUNT_ID", "expense")

    chosen = choose_expense_account(list_accounts(settings, access_token, api_domain, http=http))
    if chosen is None:
        raise ZohoError(NO_EXPENSE_ACCOUNT)
    return chosen