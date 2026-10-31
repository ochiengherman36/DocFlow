import datetime
import secrets
from urllib.parse import urlencode

from sqlalchemy import delete
from sqlalchemy.orm import Session

from config import Settings
from models import OAuthState

STATE_TTL = datetime.timedelta(minutes=10)

SCOPES = [
    "ZohoBooks.bills.CREATE",
    "ZohoBooks.bills.READ",
    "ZohoBooks.contacts.CREATE",
    "ZohoBooks.contacts.READ",
    "ZohoBooks.settings.READ",
]


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