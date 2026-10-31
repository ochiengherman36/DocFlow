import datetime
import secrets
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx
from sqlalchemy import delete
from sqlalchemy.orm import Session

from config import Settings
from models import OAuthState

STATE_TTL = datetime.timedelta(minutes=10)
MAX_STATE_LENGTH = 128
DEFAULT_TOKEN_LIFETIME = 3600
TOKEN_TIMEOUT_SECONDS = 15
REFRESH_REJECTED = {"invalid_code", "invalid_grant", "invalid_token"}

SCOPES = [
    "ZohoBooks.bills.CREATE",
    "ZohoBooks.bills.READ",
    "ZohoBooks.contacts.CREATE",
    "ZohoBooks.contacts.READ",
    "ZohoBooks.settings.READ",
]


class ZohoError(Exception):
    def __init__(self, message: str, code: str | None = None):
        super().__init__(message)
        self.code = code


class ZohoRefreshRejected(ZohoError):
    pass


@dataclass(frozen=True)
class TokenResponse:
    access_token: str
    refresh_token: str
    expires_in: int
    scope: str | None
    api_domain: str | None


@dataclass(frozen=True)
class RefreshResponse:
    access_token: str
    expires_in: int
    refresh_token: str | None
    scope: str | None
    api_domain: str | None


def create_state(session: Session, created_by: str, *, now: datetime.datetime | None = None) -> str:
    """Make a one-time state, valid for ten minutes. Old expired ones are removed. The caller commits."""
    now = now or datetime.datetime.now(datetime.UTC)
    session.execute(delete(OAuthState).where(OAuthState.expires_at < now))

    state = secrets.token_urlsafe(32)
    session.add(OAuthState(state=state, created_by=created_by, expires_at=now + STATE_TTL))
    session.flush()
    return state


def authorization_url(settings: Settings, state: str) -> str:
    query = urlencode(
        {
            "scope": ",".join(SCOPES),
            "client_id": settings.zoho_client_id,
            "response_type": "code",
            "access_type": "offline",
            "redirect_uri": settings.zoho_redirect_uri,
            "state": state,
            "prompt": "consent",
        }
    )
    return f"{settings.zoho_accounts_url}/oauth/v2/auth?{query}"


def consume_state(session: Session, state: str | None, *, now: datetime.datetime | None = None) -> str | None:
    """Use up a state. Returns who started the connection, or None if the state is not valid.

    A state works once, within its ten minutes. The row is locked so two requests cannot both use it.
    The caller commits.
    """
    if not state or len(state) > MAX_STATE_LENGTH:
        return None

    now = now or datetime.datetime.now(datetime.UTC)
    row = session.get(OAuthState, state, with_for_update=True)
    if row is None or row.used_at is not None:
        return None

    expires_at = row.expires_at if row.expires_at.tzinfo else row.expires_at.replace(tzinfo=datetime.UTC)
    if expires_at < now:
        return None

    row.used_at = now
    session.flush()
    return row.created_by


def _post_token(settings: Settings, form: dict[str, str], http: httpx.Client | None) -> dict:
    """Send a form to Zoho's token address. Zoho reports some failures as a 200 with an error field."""
    url = f"{settings.zoho_accounts_url}/oauth/v2/token"
    try:
        if http is None:
            with httpx.Client(timeout=TOKEN_TIMEOUT_SECONDS) as owned:
                response = owned.post(url, data=form)
        else:
            response = http.post(url, data=form)
    except httpx.HTTPError as exc:
        raise ZohoError("could not reach Zoho") from exc

    try:
        body = response.json()
    except ValueError as exc:
        raise ZohoError(f"Zoho answered with something unreadable (HTTP {response.status_code})") from exc

    if not isinstance(body, dict):
        raise ZohoError("Zoho answered with something unexpected")
    if "error" in body:
        code = str(body["error"])
        raise ZohoError(code, code=code)
    if response.status_code >= 400:
        raise ZohoError(f"Zoho refused the request (HTTP {response.status_code})")
    return body


def _lifetime(body: dict) -> int:
    expires_in = body.get("expires_in")
    return expires_in if isinstance(expires_in, int) and expires_in > 0 else DEFAULT_TOKEN_LIFETIME


def exchange_code(settings: Settings, code: str, *, http: httpx.Client | None = None) -> TokenResponse:
    """Trade the one-time code for tokens."""
    body = _post_token(
        settings,
        {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": settings.zoho_client_id,
            "client_secret": settings.zoho_client_secret.get_secret_value(),
            "redirect_uri": settings.zoho_redirect_uri,
        },
        http,
    )

    access_token = body.get("access_token")
    refresh_token = body.get("refresh_token")
    if not access_token or not refresh_token:
        raise ZohoError("Zoho did not send both tokens")

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=_lifetime(body),
        scope=body.get("scope"),
        api_domain=body.get("api_domain"),
    )


def refresh_access_token(
    settings: Settings, refresh_token: str, *, http: httpx.Client | None = None
) -> RefreshResponse:
    """Get a new access token. Zoho does not send a new refresh token, so the old one stays valid.

    Raises ZohoRefreshRejected only when Zoho says the refresh token itself is no good. Any other
    failure (network, rate limit, a bad client secret) is a plain ZohoError, because the saved
    connection may still be fine.
    """
    try:
        body = _post_token(
            settings,
            {
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": settings.zoho_client_id,
                "client_secret": settings.zoho_client_secret.get_secret_value(),
            },
            http,
        )
    except ZohoError as exc:
        if exc.code in REFRESH_REJECTED:
            raise ZohoRefreshRejected(str(exc), code=exc.code) from exc
        raise

    access_token = body.get("access_token")
    if not access_token:
        raise ZohoError("Zoho did not send an access token")

    return RefreshResponse(
        access_token=access_token,
        expires_in=_lifetime(body),
        refresh_token=body.get("refresh_token"),
        scope=body.get("scope"),
        api_domain=body.get("api_domain"),
    )