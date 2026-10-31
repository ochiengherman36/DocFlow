import datetime

import httpx
from sqlalchemy.orm import Session

from config import Settings
from crypto import TokenDecryptionError, get_cipher
from zoho_oauth import ZohoError, ZohoRefreshRejected, refresh_access_token
from zoho_store import delete_tokens, get_tokens, update_access_token

REFRESH_MARGIN = datetime.timedelta(seconds=60)

NOT_CONNECTED = "Zoho is not connected."
UNREADABLE = "The saved Zoho connection could not be read. Connect Zoho again."
REJECTED = "Zoho no longer accepts the saved connection. Connect Zoho again."


class ZohoNotConnected(Exception):
    pass


def _fresh(expires_at: datetime.datetime, now: datetime.datetime) -> bool:
    return expires_at - now > REFRESH_MARGIN


def get_access_token(
    session: Session,
    settings: Settings,
    *,
    now: datetime.datetime | None = None,
    http: httpx.Client | None = None,
) -> str:
    """Return an access token that is good for at least another minute, refreshing it if it is not.

    The row is locked while refreshing, so two requests cannot both refresh. If Zoho rejects the
    refresh token the connection is removed and ZohoNotConnected is raised. Any other failure
    leaves the connection alone and raises ZohoError. This commits.
    """
    now = now or datetime.datetime.now(datetime.UTC)
    cipher = get_cipher(settings)

    try:
        tokens = get_tokens(session, cipher)
    except TokenDecryptionError as exc:
        raise ZohoNotConnected(UNREADABLE) from exc
    if tokens is None:
        raise ZohoNotConnected(NOT_CONNECTED)
    if _fresh(tokens.expires_at, now):
        return tokens.access_token

    try:
        tokens = get_tokens(session, cipher, lock=True)
    except TokenDecryptionError as exc:
        session.rollback()
        raise ZohoNotConnected(UNREADABLE) from exc
    if tokens is None:
        session.rollback()
        raise ZohoNotConnected(NOT_CONNECTED)
    if _fresh(tokens.expires_at, now):
        session.rollback()
        return tokens.access_token

    try:
        refreshed = refresh_access_token(settings, tokens.refresh_token, http=http)
    except ZohoRefreshRejected as exc:
        delete_tokens(session)
        session.commit()
        raise ZohoNotConnected(REJECTED) from exc
    except ZohoError:
        session.rollback()
        raise

    update_access_token(
        session,
        cipher,
        access_token=refreshed.access_token,
        expires_at=now + datetime.timedelta(seconds=refreshed.expires_in),
        refresh_token=refreshed.refresh_token,
        scope=refreshed.scope,
        api_domain=refreshed.api_domain,
    )
    session.commit()
    return refreshed.access_token