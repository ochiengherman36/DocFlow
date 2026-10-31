import argparse
import datetime
import hashlib
import sys

from config import get_settings
from crypto import TokenDecryptionError, get_cipher
from db import get_sessionmaker
from models import ZohoConnection
from zoho_oauth import ZohoError
from zoho_store import CONNECTION_ID, get_tokens
from zoho_tokens import ZohoNotConnected, get_access_token


def fingerprint(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()[:8]


def main() -> int:
    parser = argparse.ArgumentParser(description="Check that the saved Zoho access token refreshes.")
    parser.add_argument("--expire", action="store_true", help="make the saved access token look expired first")
    args = parser.parse_args()

    settings = get_settings()
    if not settings.zoho_configured:
        print("Zoho is not set up. Fill in the ZOHO_* values and TOKEN_ENCRYPTION_KEY in .env.")
        return 1

    cipher = get_cipher(settings)
    with get_sessionmaker()() as session:
        try:
            before = get_tokens(session, cipher)
        except TokenDecryptionError:
            print("The saved tokens cannot be read with this TOKEN_ENCRYPTION_KEY. Connect Zoho again.")
            return 1
        if before is None:
            print("Zoho is not connected.")
            return 1

        print(f"Connected by {before.connected_by}")
        print(f"Before: token {fingerprint(before.access_token)}, expires {before.expires_at.isoformat()}")

        if args.expire:
            row = session.get(ZohoConnection, CONNECTION_ID)
            row.expires_at = datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=1)
            session.commit()
            print("Made the saved token look expired.")

        try:
            token = get_access_token(session, settings)
        except ZohoNotConnected as exc:
            print(f"DISCONNECTED: {exc}")
            return 1
        except ZohoError as exc:
            print(f"FAILED: {exc}")
            return 1

        print(f"After:  token {fingerprint(token)}, expires {get_tokens(session, cipher).expires_at.isoformat()}")
        print("Refreshed." if fingerprint(token) != fingerprint(before.access_token) else "Still valid, not refreshed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())