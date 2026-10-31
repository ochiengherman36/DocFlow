import datetime
from dataclasses import dataclass

from sqlalchemy.orm import Session

from crypto import TokenCipher
from models import ZohoConnection

CONNECTION_ID = 1


@dataclass(frozen=True)
class StoredTokens:
    access_token: str
    refresh_token: str
    expires_at: datetime.datetime
    scope: str | None
    api_domain: str | None
    connected_by: str
    connected_at: datetime.datetime


def _as_utc(value: datetime.datetime) -> datetime.datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=datetime.UTC)
    return value.astimezone(datetime.UTC)


def save_tokens(
    session: Session,
    cipher: TokenCipher,
    *,
    access_token: str,
    refresh_token: str,
    expires_at: datetime.datetime,
    scope: str | None,
    api_domain: str | None,
    connected_by: str,
) -> None:
    """Store the one Zoho connection, replacing any earlier one. The caller commits."""
    row = session.get(ZohoConnection, CONNECTION_ID)
    if row is None:
        row = ZohoConnection(id=CONNECTION_ID)
        session.add(row)

    row.access_token_enc = cipher.encrypt(access_token)
    row.refresh_token_enc = cipher.encrypt(refresh_token)
    row.expires_at = _as_utc(expires_at)
    row.scope = scope
    row.api_domain = api_domain
    row.connected_by = connected_by
    row.connected_at = datetime.datetime.now(datetime.UTC)
    session.flush()


def get_tokens(session: Session, cipher: TokenCipher) -> StoredTokens | None:
    row = session.get(ZohoConnection, CONNECTION_ID)
    if row is None:
        return None
    return StoredTokens(
        access_token=cipher.decrypt(row.access_token_enc),
        refresh_token=cipher.decrypt(row.refresh_token_enc),
        expires_at=_as_utc(row.expires_at),
        scope=row.scope,
        api_domain=row.api_domain,
        connected_by=row.connected_by,
        connected_at=_as_utc(row.connected_at),
    )


def delete_tokens(session: Session) -> bool:
    """Remove the stored connection. Returns False if there was none. The caller commits."""
    row = session.get(ZohoConnection, CONNECTION_ID)
    if row is None:
        return False
    session.delete(row)
    session.flush()
    return True